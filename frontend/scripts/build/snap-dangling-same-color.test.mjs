import { test } from "node:test";
import assert from "node:assert/strict";
import { snapDanglingSameColorEndpoints } from "./snap-dangling-same-color.mjs";

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.76 * Math.PI) / 180));
const O = [-73.98, 40.76];
const P = (dxM, dyM) => [O[0] + dxM * DEG_LON, O[1] + dyM * DEG_LAT];
const R = 6371000;
function hav([a, b], [c, d]) {
  const r = Math.PI / 180, dy = (d - b) * r, dx = (c - a) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(b * r) * Math.cos(d * r) * Math.sin(dx / 2) ** 2));
}
function feat(id, color, routes, coords) {
  return { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { corridor_id: id, color, route_ids: routes } };
}
function metersFromOrigin(p) {
  return [(p[0] - O[0]) / DEG_LON, (p[1] - O[1]) / DEG_LAT];
}
function tangentAngleToHorizontal(a, b) {
  const [ax, ay] = metersFromOrigin(a);
  const [bx, by] = metersFromOrigin(b);
  const angle = Math.abs((Math.atan2(by - ay, bx - ax) * 180) / Math.PI);
  return Math.min(angle, 180 - angle);
}

test("snaps a converging dangling same-color endpoint onto the sibling trunk", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  // B/D lane comes down from upper-left; its START dangles ~8m above the trunk, heading into it
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(48, 8), P(35, 30), P(20, 60)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { snapDistM: 12 });
  assert.equal(snappedCount, 1);
  const out = features.find((f) => f.properties.corridor_id === "BD");
  const dToTrunk = Math.min(...trunk.geometry.coordinates.map((p) => hav(p, out.geometry.coordinates[0])));
  assert.ok(hav(out.geometry.coordinates[0], P(48, 0)) < 1.5 || dToTrunk < 1.5, "start snapped onto the trunk line");
  assert.equal(out.properties.same_color_endpoint_snapped, true);
});

test("does NOT snap parallel same-color lanes (perpendicular projection, not converging)", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  // a parallel orange lane 8m above, running alongside (endpoints are abreast, not pointing in)
  const parallel = feat("BD", "#FF6319", ["B", "D"], [P(0, 8), P(40, 8), P(90, 8)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, parallel], { snapDistM: 12 });
  assert.equal(snappedCount, 0);
});

test("snaps a loose-end terminus that ends ~6m beside a same-color sibling (no own-route piece nearby)", () => {
  // B/D lane starts here and runs parallel-offset ~6m from the M trunk, then heads
  // away; its START is a loose end (no other B/D piece nearby) -> should merge.
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(40, 6), P(60, 6), P(90, 60), P(130, 260)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { looseSnapDistM: 7 });
  assert.equal(snappedCount, 1, "loose-end start should snap onto the trunk");
  const out = features.find((f) => f.properties.corridor_id === "BD");
  assert.ok(hav(out.geometry.coordinates[0], P(40, 0)) < 1.5, "start moved onto the M trunk");
});

test("snapped high-angle loose-end terminus leaves the trunk with a tangent-continuous curve", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-80, 0), P(0, 0), P(80, 0), P(180, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(40, 6), P(42, 80), P(45, 160)]);

  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], {
    looseSnapDistM: 7,
  });

  assert.equal(snappedCount, 1);
  const out = features.find((f) => f.properties.corridor_id === "BD");
  assert.ok(hav(out.geometry.coordinates[0], P(40, 0)) < 1.5, "start moved onto the trunk");
  assert.ok(out.geometry.coordinates.length > branch.geometry.coordinates.length, "merge is sampled as a curve, not a single kink");
  assert.ok(
    tangentAngleToHorizontal(out.geometry.coordinates[0], out.geometry.coordinates[1]) <= 25,
    "first branch segment should leave the trunk nearly tangent to the trunk",
  );
});

test("does NOT loose-end snap a parallel lane a full lane-width (8m) apart", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const parallel = feat("BD", "#FF6319", ["B", "D"], [P(40, 8), P(60, 8), P(90, 8), P(130, 8)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, parallel], { looseSnapDistM: 7 });
  assert.equal(snappedCount, 0, "8m parallel offset is beyond the loose-snap threshold");
});

test("does NOT snap a different-color endpoint", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const blue = feat("A", "#0A84FF", ["A"], [P(48, 8), P(35, 30), P(20, 60)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, blue], { snapDistM: 12 });
  assert.equal(snappedCount, 0);
});

test("leaves an already-touching endpoint unchanged", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(50, 0), P(35, 30), P(20, 60)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { snapDistM: 12 });
  assert.equal(snappedCount, 0);
});

test("does NOT snap across a gap larger than snapDistM", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(48, 40), P(35, 70), P(20, 100)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { snapDistM: 12 });
  assert.equal(snappedCount, 0);
});
