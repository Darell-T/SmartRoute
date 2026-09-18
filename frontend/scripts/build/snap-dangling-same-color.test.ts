import { test } from "node:test";
import assert from "node:assert/strict";
import { snapDanglingSameColorEndpoints } from "./snap-dangling-same-color.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.76 * Math.PI) / 180));
const O: Position = [-73.98, 40.76];
const P = (dxM: number, dyM: number): Position => [O[0] + dxM * DEG_LON, O[1] + dyM * DEG_LAT];
const R = 6371000;
function hav([a, b]: Position, [c, d]: Position): number {
  const r = Math.PI / 180, dy = (d - b) * r, dx = (c - a) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(b * r) * Math.cos(d * r) * Math.sin(dx / 2) ** 2));
}

type TestFeatureProperties = {
  corridor_id: string;
  color: string;
  route_ids: string[] | string;
  same_color_endpoint_snapped?: boolean;
  same_color_y_join_fabric?: boolean;
  same_color_y_join_fabric_count?: number;
  visual_feature_type?: string;
};

type TestFeature = Feature<LineStringGeometry, TestFeatureProperties>;

function feat(id: string, color: string, routes: string[], coords: Position[]): TestFeature {
  return { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { corridor_id: id, color, route_ids: routes } };
}
function metersFromOrigin(p: Position): Position {
  return [(p[0] - O[0]) / DEG_LON, (p[1] - O[1]) / DEG_LAT];
}
function tangentAngleToHorizontal(a: Position, b: Position): number {
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
  assert.ok(out);
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
  assert.ok(out);
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
  assert.ok(out);
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

test("empty and malformed features are a no-op and a converging snap is deterministic", () => {
  const emptyFirst = snapDanglingSameColorEndpoints([]);
  const emptySecond = snapDanglingSameColorEndpoints([]);
  assert.deepEqual(emptyFirst, { features: [], snappedCount: 0 });
  assert.deepEqual(emptyFirst, emptySecond);

  const stub = feat("stub", "#FF6319", ["M"], [P(0, 0)]);
  const stubResult = snapDanglingSameColorEndpoints([stub]);
  assert.equal(stubResult.snappedCount, 0);
  assert.equal(stubResult.features[0]?.properties.corridor_id, "stub");

  const trunkA = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branchA = feat("BD", "#FF6319", ["B", "D"], [P(48, 8), P(35, 30), P(20, 60)]);
  const trunkB = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branchB = feat("BD", "#FF6319", ["B", "D"], [P(48, 8), P(35, 30), P(20, 60)]);
  const first = snapDanglingSameColorEndpoints([trunkA, branchA], { snapDistM: 12 });
  const second = snapDanglingSameColorEndpoints([trunkB, branchB], { snapDistM: 12 });
  assert.equal(first.snappedCount, 1);
  assert.equal(second.snappedCount, 1);
  assert.deepEqual(
    first.features.find((feature) => feature.properties.corridor_id === "BD")?.geometry.coordinates,
    second.features.find((feature) => feature.properties.corridor_id === "BD")?.geometry.coordinates,
  );
});

test("snaps a high-angle dangling END onto the sibling trunk with a tangent-continuous curve", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-80, 0), P(0, 0), P(80, 0), P(180, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(45, 160), P(42, 80), P(40, 6)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { looseSnapDistM: 7 });
  assert.equal(snappedCount, 1);
  const out = features.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  const end = out.geometry.coordinates.at(-1);
  assert.ok(end);
  assert.ok(hav(end, P(40, 0)) < 1.5, "end should move onto the trunk");
  assert.ok(out.geometry.coordinates.length > branch.geometry.coordinates.length, "end join should be sampled as a curve");
  assert.equal(out.properties.same_color_y_join_fabric, true);
  assert.equal(out.properties.same_color_y_join_fabric_count, 1);
});

test("a low-angle dangling start snaps by moving the endpoint, not by inserting a curve", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(40, 4), P(70, 4.2), P(110, 80)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { snapDistM: 12 });
  assert.equal(snappedCount, 1);
  const out = features.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  assert.equal(out.properties.same_color_y_join_fabric, undefined);
  assert.ok(hav(out.geometry.coordinates[0], P(40, 0)) < 1.5, "start should land on the trunk");
});

test("a short high-angle branch still snaps even when the merge curve would exceed its length", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-80, 0), P(0, 0), P(80, 0), P(180, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(40, 6), P(41, 12)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], {
    looseSnapDistM: 7,
    mergeCurveM: 90,
  });
  assert.equal(snappedCount, 1);
  const out = features.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  assert.ok(hav(out.geometry.coordinates[0], P(40, 0)) < 1.5, "short start should still land on the trunk");
});

test("does not loose-end snap when another same-route piece already sits next to the terminus", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(40, 6), P(80, 6), P(120, 80)]);
  const sibling = feat("BD-other", "#FF6319", ["B", "D"], [P(40, 22), P(40, 90)]);
  const { snappedCount, features } = snapDanglingSameColorEndpoints([trunk, branch, sibling], {
    snapDistM: 14,
    looseSnapDistM: 7,
    looseEndM: 20,
  });
  assert.equal(snappedCount, 0, "a nearby same-route piece means this is not a loose terminus");
  assert.deepEqual(features.find((feature) => feature.properties.corridor_id === "BD")?.geometry.coordinates, branch.geometry.coordinates);
});

test("a short converging branch still snaps when the inward sample overruns the polyline", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(48, 8), P(20, 20)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { snapDistM: 12, convergeSampleM: 40 });
  assert.equal(snappedCount, 1);
});

test("a high-angle snap with mergeCurveM below 8 still moves the endpoint", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-80, 0), P(0, 0), P(80, 0), P(180, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(45, 160), P(42, 80), P(40, 6)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], {
    looseSnapDistM: 7,
    mergeCurveM: 4,
    maxDirectSnapTangentDeg: 0,
  });
  assert.equal(snappedCount, 1);
  const out = features.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  const end = out.geometry.coordinates.at(-1);
  assert.ok(end);
  assert.ok(hav(end, P(40, 0)) < 1.5, "tiny mergeCurveM should still land the end on the trunk");
  assert.equal(out.properties.same_color_y_join_fabric, undefined, "curve below 8m must fall back to a point snap");
});

test("default snap gates leave empty input unchanged", () => {
  const first = snapDanglingSameColorEndpoints([]);
  assert.deepEqual(first, { features: [], snappedCount: 0 });
});

test("a trunk with a collapsed segment still receives a converging snap", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(48, 8), P(35, 30), P(20, 60)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], { snapDistM: 12 });
  assert.equal(snappedCount, 1);
});

test("non-array route_ids still snap by color and prefer the closer sibling", () => {
  const far = feat("M-far", "#FF6319", ["M"], [P(-50, 40), P(0, 40), P(50, 40)]);
  const near = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: [P(48, 8), P(35, 30), P(20, 60)] },
    properties: { corridor_id: "BD", color: "#FF6319", route_ids: "B" },
  };
  const { features, snappedCount } = snapDanglingSameColorEndpoints([far, near, branch], { snapDistM: 12 });
  assert.equal(snappedCount, 1);
  const out = features.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  assert.ok(hav(out.geometry.coordinates[0], P(48, 0)) < 8);
});

test("a high-angle end snap with mergeCurveM 0 still moves the endpoint", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-80, 0), P(0, 0), P(80, 0), P(180, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(45, 160), P(42, 80), P(40, 6)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], {
    looseSnapDistM: 7,
    mergeCurveM: 0,
    maxDirectSnapTangentDeg: 0,
  });
  assert.equal(snappedCount, 1);
  const out = features.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  const end = out.geometry.coordinates.at(-1);
  assert.ok(end);
  assert.ok(hav(end, P(40, 0)) < 1.5);
});

test("a first segment shorter than convergeSampleM still counts as converging", () => {
  const trunk = feat("M", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(48, 8), P(47, 12), P(20, 80)]);
  const { snappedCount } = snapDanglingSameColorEndpoints([trunk, branch], {
    snapDistM: 12,
    convergeSampleM: 40,
  });
  assert.equal(snappedCount, 1);
});

test("two in-range same-color siblings snap to the closer trunk", () => {
  const farther = feat("M-far", "#FF6319", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const closer = feat("M-near", "#FF6319", ["M"], [P(-50, 4), P(0, 4), P(50, 4), P(150, 4)]);
  const branch = feat("BD", "#FF6319", ["B", "D"], [P(48, 8), P(35, 30), P(20, 60)]);
  const { features, snappedCount } = snapDanglingSameColorEndpoints([farther, closer, branch], {
    snapDistM: 12,
  });
  assert.equal(snappedCount, 1);
  const out = features.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  assert.ok(hav(out.geometry.coordinates[0], P(48, 4)) < 8);
});
