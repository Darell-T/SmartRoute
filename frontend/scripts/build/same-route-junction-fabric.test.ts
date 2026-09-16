import { test } from "node:test";
import assert from "node:assert/strict";
import { repairSameRouteEndpointCrossings } from "./same-route-junction-fabric.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.84 * Math.PI) / 180));
const O: Position = [-73.87, 40.84];
const P = (xM: number, yM: number): Position => [O[0] + xM * DEG_PER_M_LON, O[1] + yM * DEG_PER_M_LAT];

type TestFeatureProperties = {
  corridor_id: string;
  bundle_id: string;
  route_ids: string[];
  color_route_ids: string[] | Record<string, string[]>;
  color: string;
  same_route_junction_fabric?: boolean;
};

type TestFeature = Feature<LineStringGeometry, TestFeatureProperties>;

function line(
  id: string,
  routeIds: string[],
  coords: Position[],
  extra: Partial<TestFeatureProperties> = {},
): TestFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: id,
      bundle_id: id,
      route_ids: routeIds,
      color_route_ids: routeIds,
      color: "#00933C",
      ...extra,
    },
  };
}

function mxy(p: Position): Position {
  return [
    (p[0] - O[0]) / DEG_PER_M_LON,
    (p[1] - O[1]) / DEG_PER_M_LAT,
  ];
}

function segmentIntersection(a: Position, b: Position, c: Position, d: Position): { t: number; u: number } | null {
  const [x1, y1] = mxy(a);
  const [x2, y2] = mxy(b);
  const [x3, y3] = mxy(c);
  const [x4, y4] = mxy(d);
  const den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4);
  if (Math.abs(den) < 1e-9) return null;
  const t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den;
  const u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den;
  if (t > 0 && t < 1 && u > 0 && u < 1) {
    return { t, u };
  }
  return null;
}

function hasInteriorIntersection(
  a: { geometry: LineStringGeometry },
  b: { geometry: LineStringGeometry },
): boolean {
  const ac = a.geometry.coordinates;
  const bc = b.geometry.coordinates;
  for (let i = 1; i < ac.length; i += 1) {
    for (let j = 1; j < bc.length; j += 1) {
      if (segmentIntersection(ac[i - 1], ac[i], bc[j - 1], bc[j])) return true;
    }
  }
  return false;
}

test("snaps a same-route endpoint overshoot back to the sibling trunk split", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 160)]);
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)]);

  assert.equal(hasInteriorIntersection(branch, trunk), true, "fixture starts with an X crossing");

  const result = repairSameRouteEndpointCrossings([trunk, branch], {
    maxEndpointOvershootM: 60,
  });

  assert.equal(result.repairCount, 1);
  const fixedBranch = result.features.find((feature) => feature.properties.corridor_id === "branch");
  assert.ok(fixedBranch);
  assert.notEqual(fixedBranch, branch, "repaired feature is cloned");
  assert.equal(fixedBranch.properties.same_route_junction_fabric, true);
  assert.equal(hasInteriorIntersection(fixedBranch, trunk), false, "the X crossing is removed");
  assert.ok(Math.abs(mxy(fixedBranch.geometry.coordinates[0])[0]) < 0.1, "branch starts on trunk x");
});

test("does not repair crossings between different active routes", () => {
  const trunk = line("trunk", ["2"], [P(0, -100), P(0, 160)], { color: "#EE352E" });
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)]);

  const result = repairSameRouteEndpointCrossings([trunk, branch], {
    maxEndpointOvershootM: 60,
  });

  assert.equal(result.repairCount, 0);
  assert.equal(result.features[0], trunk);
  assert.equal(result.features[1], branch);
});

test("clips same-color sibling endpoint overshoot even when the crossing is several vertices from the endpoint", () => {
  const trunk = line("trunk", ["4"], [P(0, -100), P(0, 180)]);
  const branch = line("branch", ["5"], [
    P(-80, -80),
    P(-30, -20),
    P(12, 55),
    P(8, 80),
    P(4, 96),
  ]);

  assert.equal(hasInteriorIntersection(branch, trunk), true, "fixture starts with sibling-route X crossing");

  const result = repairSameRouteEndpointCrossings([trunk, branch], {
    maxEndpointOvershootM: 80,
  });

  assert.equal(result.repairCount, 1);
  const fixedBranch = result.features.find((feature) => feature.properties.corridor_id === "branch");
  assert.ok(fixedBranch);
  assert.equal(hasInteriorIntersection(fixedBranch, trunk), false);
  assert.ok(fixedBranch.geometry.coordinates.length < branch.geometry.coordinates.length);
  const fixedBranchEnd = fixedBranch.geometry.coordinates.at(-1);
  assert.ok(fixedBranchEnd);
  assert.ok(Math.abs(mxy(fixedBranchEnd)[0]) < 0.1, "branch ends on trunk x");
});

test("does not clip an interior crossing that is not an endpoint overshoot", () => {
  const left = line("left", ["5"], [P(-40, -100), P(40, 100)]);
  const right = line("right", ["5"], [P(40, -100), P(-40, 100)]);

  const result = repairSameRouteEndpointCrossings([left, right], {
    maxEndpointOvershootM: 60,
  });

  assert.equal(result.repairCount, 0, "interior crossings need a fuller junction model");
});

test("empty and malformed features are a no-op and an endpoint snap is deterministic", () => {
  const emptyFirst = repairSameRouteEndpointCrossings([]);
  const emptySecond = repairSameRouteEndpointCrossings([]);
  assert.equal(emptyFirst.repairCount, 0);
  assert.deepEqual(emptyFirst.features, []);
  assert.deepEqual(emptyFirst, emptySecond);

  const stub = line("stub", ["5"], [P(0, 0)]);
  const stubResult = repairSameRouteEndpointCrossings([stub]);
  assert.equal(stubResult.repairCount, 0);
  assert.equal(stubResult.features[0], stub);

  const trunkCoords: Position[] = [P(0, -100), P(0, 160)];
  const branchCoords: Position[] = [P(-14, -16), P(18, 80), P(45, 150)];
  const first = repairSameRouteEndpointCrossings(
    [line("trunk", ["5"], trunkCoords), line("branch", ["5"], branchCoords)],
    { maxEndpointOvershootM: 60 },
  );
  const second = repairSameRouteEndpointCrossings(
    [line("trunk", ["5"], trunkCoords), line("branch", ["5"], branchCoords)],
    { maxEndpointOvershootM: 60 },
  );
  assert.equal(first.repairCount, 1);
  assert.equal(second.repairCount, 1);
  assert.deepEqual(
    first.features.find((feature) => feature.properties.corridor_id === "branch")?.geometry.coordinates,
    second.features.find((feature) => feature.properties.corridor_id === "branch")?.geometry.coordinates,
  );
});

test("keyed color_route_ids still count as the same active route", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 160)], {
    color_route_ids: { "#00933C": ["5"] },
  });
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)], {
    color_route_ids: { "#00933C": ["5"] },
  });
  const result = repairSameRouteEndpointCrossings([trunk, branch], { maxEndpointOvershootM: 60 });
  assert.equal(result.repairCount, 1);
});

test("color_route_ids keyed to another color still union via object values", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 160)], {
    color_route_ids: { "#EE352E": ["5"] },
  });
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)], {
    color_route_ids: { "#EE352E": ["5"] },
  });
  const result = repairSameRouteEndpointCrossings([trunk, branch], { maxEndpointOvershootM: 60 });
  assert.equal(result.repairCount, 1);
});

test("route_ids are used when color_route_ids is omitted", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 160)], { color_route_ids: undefined });
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)], {
    color_route_ids: undefined,
  });
  const result = repairSameRouteEndpointCrossings([trunk, branch], { maxEndpointOvershootM: 60 });
  assert.equal(result.repairCount, 1);
});

test("clips a start-side overshoot several vertices from the first point", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 160)]);
  const branch = line("branch", ["5"], [
    P(-20, 10),
    P(-10, 10),
    P(-5, 10),
    P(25, 40),
    P(45, 80),
  ]);
  assert.equal(hasInteriorIntersection(branch, trunk), true);
  const result = repairSameRouteEndpointCrossings([trunk, branch], { maxEndpointOvershootM: 80, minSegmentM: 8 });
  assert.equal(result.repairCount, 1);
  const fixed = result.features.find((feature) => feature.properties.corridor_id === "branch");
  assert.ok(fixed);
  assert.equal(hasInteriorIntersection(fixed, trunk), false);
  assert.ok(Math.abs(mxy(fixed.geometry.coordinates[0])[0]) < 0.5, "start should snap onto the trunk");
});

test("clips an end-side overshoot and drops a leftover sub-minSegment stub", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 180)]);
  const branch = line("branch", ["5"], [
    P(-80, -80),
    P(-30, -20),
    P(12, 55),
    P(1, 70),
    P(8, 80),
    P(4, 96),
  ]);
  assert.equal(hasInteriorIntersection(branch, trunk), true);
  const result = repairSameRouteEndpointCrossings([trunk, branch], {
    maxEndpointOvershootM: 90,
    minSegmentM: 15,
  });
  assert.equal(result.repairCount, 1);
  const fixed = result.features.find((feature) => feature.properties.corridor_id === "branch");
  assert.ok(fixed);
  assert.equal(hasInteriorIntersection(fixed, trunk), false);
  const end = fixed.geometry.coordinates.at(-1);
  assert.ok(end);
  assert.ok(Math.abs(mxy(end)[0]) < 0.5, "end should snap onto the trunk");
});

test("an end-side overshoot on the last segment moves the endpoint and drops a stub shorter than minSegmentM", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 180)]);
  const branch = line("branch", ["5"], [
    P(-80, -40),
    P(-20, 70),
    P(18, 78),
  ]);
  assert.equal(hasInteriorIntersection(branch, trunk), true);
  const result = repairSameRouteEndpointCrossings([trunk, branch], {
    maxEndpointOvershootM: 80,
    minSegmentM: 25,
  });
  assert.equal(result.repairCount, 1);
  const fixed = result.features.find((feature) => feature.properties.corridor_id === "branch");
  assert.ok(fixed);
  assert.equal(hasInteriorIntersection(fixed, trunk), false);
  const end = fixed.geometry.coordinates.at(-1);
  assert.ok(end);
  assert.ok(Math.abs(mxy(end)[0]) < 0.5, "last-segment overshoot should snap the endpoint onto the trunk");
});
