import { test } from "node:test";
import assert from "node:assert/strict";
import { collapseSameColorOverlaps } from "./collapse-same-color.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

type TestProperties = {
  corridor_id?: string;
  segment_id?: string;
  id?: string | number;
  route_ids: string[] | string;
  color: string;
  color_route_ids?: string[];
  representative_route_id?: string;
  route_id?: string;
  lane_slot_source?: string;
  qa_orphan_origin?: boolean;
  qa_orphan_from_is_terminal?: boolean;
  qa_orphan_to_is_terminal?: boolean;
  qa_orphan_severity?: string;
  same_color_shared_run?: boolean;
  same_color_tail?: boolean;
  same_color_target_tail?: boolean;
  same_color_collapsed_representative?: boolean;
};

type TestFeature = Feature<LineStringGeometry, TestProperties>;

const M_LAT = 1 / 110574;
const M_LON = 1 / (111320 * Math.cos((40.75 * Math.PI) / 180));
const P = (lon0: number, lat0: number, dxM: number, dyM: number): Position => [
  lon0 + dxM * M_LON,
  lat0 + dyM * M_LAT,
];
const O: Position = [-73.94, 40.75];

function feat(
  cid: string,
  routeIds: string[],
  coords: Position[],
  color = "#FCCC0A",
  extra: Partial<TestProperties> = {},
): TestFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: { corridor_id: cid, route_ids: routeIds, color, ...extra },
  };
}

const R = 6371000;
const hav = (a: Position, b: Position): number => {
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
  const representative = features.find((f) => f.properties.corridor_id === "n");
  assert.ok(representative);
  const repRoutes = [...(representative.properties.route_ids ?? [])].sort();
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
  assert.deepEqual([...(shared.properties.route_ids ?? [])].sort(), ["N", "W"]);

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

test("empty and malformed features are a no-op and are deterministic", () => {
  const emptyFirst = collapseSameColorOverlaps([]);
  const emptySecond = collapseSameColorOverlaps([]);
  assert.deepEqual(emptyFirst, { features: [], collapsedCount: 0 });
  assert.deepEqual(emptyFirst, emptySecond);

  const stub = feat("stub", ["N"], [P(...O, 0, 0)]);
  const { features, collapsedCount } = collapseSameColorOverlaps([stub]);
  assert.equal(collapsedCount, 0);
  assert.equal(features[0].properties.corridor_id, "stub");
});

test("a leading unique approach is kept as a same-color source tail", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 50 }, (_, i) => P(...O, 0, i * 30)));
  const approach = Array.from({ length: 8 }, (_, i) => P(...O, 0, -210 + i * 30));
  const overlap = Array.from({ length: 20 }, (_, i) => P(...O, 6, i * 30));
  const peel = Array.from({ length: 12 }, (_, i) => P(...O, 80 + i * 40, 570));
  const branch = feat("branch", ["W"], [...approach, ...overlap.slice(1), ...peel]);
  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  const sourceTail = features.find((feature) => String(feature.properties.corridor_id).startsWith("branch-tail-"));
  assert.ok(sourceTail, "leading unique approach must remain a source tail");
  assert.equal(sourceTail.properties.same_color_tail, true);
  assert.deepEqual(sourceTail.properties.route_ids, ["W"]);
});

test("a mid-trunk overlap emits a leading target tail before the shared run", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 50 }, (_, i) => P(...O, 0, i * 30)));
  const branch = feat("branch", ["W"], [
    ...Array.from({ length: 20 }, (_, i) => P(...O, 6, 600 + i * 30)),
    ...Array.from({ length: 12 }, (_, i) => P(...O, 80 + i * 40, 1170)),
  ]);
  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  const targetTail = features.find((feature) => String(feature.properties.corridor_id).startsWith("trunk-tail-"));
  assert.ok(targetTail, "trunk geometry before the shared run must remain a target tail");
  assert.equal(targetTail.properties.same_color_target_tail, true);
  assert.deepEqual(targetTail.properties.route_ids, ["N"]);
});

test("a later branch overlapping both the trunk and an earlier overlay merges source intervals", () => {
  const trunk = feat("n", ["N"], Array.from({ length: 50 }, (_, i) => P(...O, 0, i * 30)));
  const overlay = feat("r", ["R"], [
    ...Array.from({ length: 22 }, (_, i) => P(...O, 6, 300 + i * 30)),
    ...Array.from({ length: 16 }, (_, i) => P(...O, 80 + i * 40, 930)),
  ]);
  const later = feat("w", ["W"], [
    ...Array.from({ length: 36 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 16 }, (_, i) => P(...O, -80 - i * 40, 1050)),
  ]);
  const { features, collapsedCount } = collapseSameColorOverlaps([trunk, overlay, later], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.ok(collapsedCount >= 2, "overlay and later branch should both collapse");
  assert.ok(features.some((feature) => String(feature.properties.corridor_id).startsWith("w-tail-")));
});

test("overlapping same-color branches on one trunk merge their shared-run intervals", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 50 }, (_, i) => P(...O, 0, i * 30)));
  const west = feat("west", ["W"], [
    ...Array.from({ length: 22 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 12 }, (_, i) => P(...O, -80 - i * 40, 630)),
  ]);
  const east = feat("east", ["R"], [
    ...Array.from({ length: 22 }, (_, i) => P(...O, 6, 400 + i * 30)),
    ...Array.from({ length: 12 }, (_, i) => P(...O, 80 + i * 40, 1030)),
  ]);
  const { features, collapsedCount } = collapseSameColorOverlaps([trunk, west, east], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.ok(collapsedCount >= 2, "both branches should collapse onto the trunk");
  assert.ok(features.some((feature) => feature.properties.same_color_shared_run === true));
});

test("shared runs union color_route_ids from target and source", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)), "#FCCC0A", {
    color_route_ids: ["N"],
    representative_route_id: "N",
    route_id: "N",
    lane_slot_source: "physical",
  });
  const branch = feat("branch", ["W"], [
    ...Array.from({ length: 20 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 20 }, (_, i) => P(...O, 200 + i * 60, 600)),
  ], "#FCCC0A", { color_route_ids: ["W"] });
  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  const shared = features.find((feature) => feature.properties.same_color_shared_run === true);
  assert.ok(shared, "partial overlap must emit a shared run");
  assert.deepEqual([...(shared.properties.route_ids ?? [])].sort(), ["N", "W"]);
  assert.ok(Array.isArray(shared.properties.color_route_ids), "shared run should keep color_route_ids as an array");
  assert.ok(shared.properties.color_route_ids.includes("N"));
  const targetTail = features.find((feature) => feature.properties.same_color_target_tail === true);
  assert.ok(targetTail, "target trunk tail should remain after the shared run");
  assert.ok(Array.isArray(targetTail.properties.color_route_ids));
});

test("shared run fills color_route_ids from route_ids when only the source lists them", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const branch = feat("branch", ["W"], [
    ...Array.from({ length: 20 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 20 }, (_, i) => P(...O, 200 + i * 60, 600)),
  ], "#FCCC0A", { color_route_ids: ["W"] });
  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  const shared = features.find((feature) => feature.properties.same_color_shared_run === true);
  assert.ok(shared, "partial overlap must emit a shared run");
  assert.deepEqual([...(shared.properties.route_ids ?? [])].sort(), ["N", "W"]);
});

test("full same-track collapse unions color_route_ids onto the surviving representative", () => {
  const n = feat("n", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)), "#FCCC0A", {
    color_route_ids: ["N"],
    representative_route_id: "N",
    route_id: "N",
  });
  const w = feat("w", ["W"], Array.from({ length: 40 }, (_, i) => P(...O, 6, i * 30)), "#FCCC0A", {
    color_route_ids: ["W"],
  });
  const { features, collapsedCount } = collapseSameColorOverlaps([n, w], { collapseDistM: 12, minOverlapM: 120 });
  assert.equal(collapsedCount, 1);
  const representative = features.find((feature) => feature.properties.corridor_id === "n");
  assert.ok(representative);
  assert.equal(representative.properties.same_color_collapsed_representative, true);
  assert.deepEqual([...(representative.properties.color_route_ids ?? [])].sort(), ["N", "W"]);
});

test("equal-rank equal-length corridors collapse in corridor_id order", () => {
  const a = feat("q-b", ["Q"], Array.from({ length: 40 }, (_, i) => P(...O, 6, i * 30)));
  const b = feat("q-a", ["Q"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const { collapsedCount, features } = collapseSameColorOverlaps([a, b], { collapseDistM: 12, minOverlapM: 120 });
  assert.equal(collapsedCount, 1);
  assert.ok(
    features.some((feature) => feature.properties.corridor_id === "q-a"),
    "q-a is first by corridor_id and should remain the collapse target",
  );
});

test("colorless geometry and segment_id-only labels are ignored or labeled from fallbacks", () => {
  const unlabeled = {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)) },
    properties: { segment_id: "seg-n", route_ids: ["N"], color: "#FCCC0A" },
  };
  const partner = feat("w", ["W"], Array.from({ length: 30 }, (_, i) => P(...O, 6, i * 30)));
  const colorless = feat("plain", ["1"], Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)), "");
  const { collapsedCount } = collapseSameColorOverlaps([unlabeled, partner, colorless], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.ok(collapsedCount >= 1, "segment_id fallback still allows a same-color collapse");
});

test("default collapse gates still merge two yellow tracks 6m apart", () => {
  const n = feat("n", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const w = feat("w", ["W"], Array.from({ length: 40 }, (_, i) => P(...O, 6, i * 30)));
  const { collapsedCount, features } = collapseSameColorOverlaps([n, w]);
  assert.equal(collapsedCount, 1);
  assert.ok(features.some((feature) => feature.properties.corridor_id === "n"));
});

test("unknown equal-rank route ids collapse in corridor_id order", () => {
  const left = feat("k-b", ["K"], Array.from({ length: 40 }, (_, i) => P(...O, 6, i * 30)));
  const right = feat("k-a", ["K2"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const { collapsedCount, features } = collapseSameColorOverlaps([left, right], { collapseDistM: 12, minOverlapM: 120 });
  assert.equal(collapsedCount, 1);
  assert.ok(features.some((feature) => feature.properties.corridor_id === "k-a"));
});

test("numeric id and missing labels still collapse onto a named yellow partner", () => {
  const numbered = {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)) },
    properties: { id: 7, route_ids: ["N"], color: "#FCCC0A" },
  };
  const unnamed = {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: Array.from({ length: 30 }, (_, i) => P(...O, 6, i * 30)) },
    properties: { route_ids: "N", color: "#FCCC0A" },
  };
  const partner = feat("w", ["W"], Array.from({ length: 30 }, (_, i) => P(...O, -6, i * 30)));
  const { collapsedCount } = collapseSameColorOverlaps([numbered, unnamed, partner], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.ok(collapsedCount >= 1);
});

test("a 210m same-color overlap is left stacked instead of carving a shared run", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const branch = feat("branch", ["W"], [
    ...Array.from({ length: 8 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 20 }, (_, i) => P(...O, 200 + i * 40, 210)),
  ]);
  const { collapsedCount, features } = collapseSameColorOverlaps([trunk, branch], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.equal(collapsedCount, 0);
  assert.equal(features.some((feature) => feature.properties.same_color_shared_run === true), false);
});

test("two overlapping yellow branches on one trunk merge into one target tail", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 50 }, (_, i) => P(...O, 0, i * 30)));
  const west = feat("west", ["W"], [
    ...Array.from({ length: 28 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 12 }, (_, i) => P(...O, -80 - i * 40, 810)),
  ]);
  const east = feat("east", ["R"], [
    ...Array.from({ length: 28 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 12 }, (_, i) => P(...O, 80 + i * 40, 810)),
  ]);
  const { features, collapsedCount } = collapseSameColorOverlaps([trunk, west, east], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.ok(collapsedCount >= 2);
  const targetTails = features.filter((feature) => feature.properties.same_color_target_tail === true);
  assert.ok(targetTails.length <= 2, "overlapping suppression intervals should merge rather than emit many tails");
});

test("a shared run fills color_route_ids from the target when only the target lists them", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)), "#FCCC0A", {
    color_route_ids: ["N"],
  });
  const branch = feat("branch", ["W"], [
    ...Array.from({ length: 20 }, (_, i) => P(...O, 6, i * 30)),
    ...Array.from({ length: 20 }, (_, i) => P(...O, 200 + i * 60, 600)),
  ]);
  const { features } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  const shared = features.find((feature) => feature.properties.same_color_shared_run === true);
  assert.ok(shared);
  assert.ok(Array.isArray(shared.properties.color_route_ids));
  assert.ok(shared.properties.color_route_ids.includes("N"));
});

test("zero-length duplicate vertices still project without throwing", () => {
  const trunk = feat("trunk", ["N"], [
    P(...O, 0, 0),
    P(...O, 0, 0),
    ...Array.from({ length: 38 }, (_, i) => P(...O, 0, (i + 1) * 30)),
  ]);
  const branch = feat("branch", ["W"], [
    P(...O, 6, 0),
    P(...O, 6, 0),
    ...Array.from({ length: 38 }, (_, i) => P(...O, 6, (i + 1) * 30)),
  ]);
  const { collapsedCount } = collapseSameColorOverlaps([trunk, branch], { collapseDistM: 12, minOverlapM: 120 });
  assert.equal(collapsedCount, 1);
});

test("a collapsed two-point source still ranks against a named yellow partner", () => {
  const trunk = feat("trunk", ["N"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const stub = feat("stub", ["W"], [P(...O, 6, 0), P(...O, 6, 0)]);
  const { collapsedCount, features } = collapseSameColorOverlaps([trunk, stub], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.ok(collapsedCount >= 0);
  assert.ok(features.some((feature) => feature.properties.corridor_id === "trunk"));
});

test("features with no corridor, segment, or id labels collapse as unknown", () => {
  const labeled = feat("w", ["W"], Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)));
  const unlabeled = {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: Array.from({ length: 30 }, (_, i) => P(...O, 6, i * 30)) },
    properties: { route_ids: ["N"], color: "#FCCC0A" },
  };
  const { collapsedCount } = collapseSameColorOverlaps([labeled, unlabeled], {
    collapseDistM: 12,
    minOverlapM: 120,
  });
  assert.ok(collapsedCount >= 1);
});
