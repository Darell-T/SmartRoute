import { test } from "node:test";
import assert from "node:assert/strict";
import { collapseSameColorOverlaps } from "./collapse-same-color.mjs";

const M_LAT = 1 / 110574;
const M_LON = 1 / (111320 * Math.cos((40.75 * Math.PI) / 180));
const P = (lon0, lat0, dxM, dyM) => [lon0 + dxM * M_LON, lat0 + dyM * M_LAT];
const O = [-73.94, 40.75];

function feat(cid, routeIds, coords, color = "#FCCC0A", extra = {}) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: { corridor_id: cid, route_ids: routeIds, color, ...extra },
  };
}

const R = 6371000;
const hav = (a, b) => {
  const r = Math.PI / 180, dy = (b[1] - a[1]) * r, dx = (b[0] - a[0]) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dx / 2) ** 2));
};

test("N/W/R same-color overlapping lines collapse onto one (render as one yellow line)", () => {
  // three yellow lines ~6m apart on the same track
  const n = feat("n", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const w = feat("w", ["W"], Array.from({ length: 40 }, (_, i) => P(...O, 6, i * 30)));
  const r = feat("r", ["R"], Array.from({ length: 40 }, (_, i) => P(...O, -6, i * 30)));
  const { features, collapsedCount } = collapseSameColorOverlaps([n, w, r], { collapseDistM: 12, minOverlapM: 120 });
  assert.equal(collapsedCount, 2, "W and R collapse onto N");
  assert.equal(features.length, 1, "full same-track overlap becomes one visual feature");
  const repRoutes = features.find((f) => f.properties.corridor_id === "n").properties.route_ids.sort();
  assert.deepEqual(repRoutes, ["N", "R", "W"]);
});

test("partial same-color overlap creates a shared run and keeps divergent tail route-scoped", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  // shares the first half, then peels far east
  const branch = feat("branch", ["W"], [
    ...Array.from({ length: 20 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 20 }, (_, i) => P(...O, 200 + i * 60, 600)),
  ]);
  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  assert.equal(
    features.some((f) => f.properties.corridor_id === "trunk"),
    false,
    "target trunk is split so the shared run is not drawn twice",
  );

  const shared = features.find((f) => f.properties.same_color_shared_run === true);
  assert.ok(shared, "shared overlap is represented as its own route-unioned run");
  assert.deepEqual([...shared.properties.route_ids].sort(), ["N", "W"]);

  const trunkTail = features.find((f) => String(f.properties.corridor_id).startsWith("trunk-tail-"));
  assert.ok(trunkTail, "target trunk tail remains visible after the shared run");
  assert.deepEqual(trunkTail.properties.route_ids, ["N"], "target tail does not get W globally");

  const tail = features.find((f) => String(f.properties.corridor_id).startsWith("branch-tail-"));
  assert.ok(tail, "divergent branch tail remains visible");
  assert.deepEqual(tail.properties.route_ids, ["W"]);

  const b = tail.geometry.coordinates;
  // the divergent tail (last vertex) stays far from the trunk
  const t = trunkTail.geometry.coordinates;
  const lastProjMin = Math.min(...t.map((p) => hav(p, b[b.length - 1])));
  assert.ok(lastProjMin > 100, "divergent tail is not snapped onto the trunk");
});

test("does not collapse different colors that overlap", () => {
  const yellow = feat("y", ["N"], Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)), "#FCCC0A");
  const orange = feat("o", ["B"], Array.from({ length: 30 }, (_, i) => P(...O, 4, i * 30)), "#FF6319");
  const { collapsedCount } = collapseSameColorOverlaps([yellow, orange], { collapseDistM: 12 });
  assert.equal(collapsedCount, 0);
});

test("does not emit tiny same-color branch slivers after removing a shared run", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)));
  const branch = feat("branch", ["W"], [
    ...Array.from({ length: 20 }, (_, i) => P(...O, 6, i * 30)),
    P(...O, 70, 585),
    P(...O, 82, 590),
  ]);
  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  const tinyTail = features.find((f) => String(f.properties.corridor_id).startsWith("branch-tail-"));
  assert.equal(tinyTail, undefined, "sub-120m sliver is not promoted as a visible route fragment");
});

test("clears stale orphan QA flags on same-color derived shared runs and tails", () => {
  const staleOrphanFlags = {
    qa_orphan_origin: true,
    qa_orphan_from_is_terminal: false,
    qa_orphan_to_is_terminal: false,
    qa_orphan_severity: "error",
  };
  const trunk = feat("trunk", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)), "#FCCC0A", staleOrphanFlags);
  const branch = feat("branch", ["R"], [
    ...Array.from({ length: 20 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 20 }, (_, i) => P(...O, -180 - i * 30, 600)),
  ], "#FCCC0A", staleOrphanFlags);

  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  const derived = features.filter(
    (feature) => feature.properties.same_color_shared_run || feature.properties.same_color_tail || feature.properties.same_color_target_tail,
  );
  assert.ok(derived.length > 0, "test should exercise same-color derived features");

  for (const feature of derived) {
    assert.equal(feature.properties.qa_orphan_origin, undefined);
    assert.equal(feature.properties.qa_orphan_from_is_terminal, undefined);
    assert.equal(feature.properties.qa_orphan_to_is_terminal, undefined);
    assert.equal(feature.properties.qa_orphan_severity, undefined);
  }
});
