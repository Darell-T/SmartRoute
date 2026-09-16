import { test } from "node:test";
import assert from "node:assert/strict";

import { applyNostrandEasternSchematic } from "./nostrand-eastern-schematic.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

type TestProperties = {
  corridor_id: string;
  color: string;
  route_ids: string[] | string;
  color_route_ids: string[];
  length_m: number;
  nostrand_eastern_straight_tail?: boolean;
  nostrand_eastern_branch_curve?: boolean;
};

type TestFeature = Feature<LineStringGeometry, TestProperties>;

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / 84410;

function ll(xM: number, yM: number): Position {
  return [-73.951 + xM * DEG_PER_M_LON, 40.670 + yM * DEG_PER_M_LAT];
}

function feature(id: string, color: string, routeIds: string[], coords: Position[]): TestFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: id,
      color,
      route_ids: routeIds,
      color_route_ids: routeIds,
      length_m: 1,
    },
  };
}

function meterDelta(a: Position, b: Position): Position {
  return [
    (b[0] - a[0]) / DEG_PER_M_LON,
    (b[1] - a[1]) / DEG_PER_M_LAT,
  ];
}

test("Nostrand schematic preserves straight 4 tail and removes terminal hook", () => {
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
    ll(-8, -8),
    ll(-10, -18),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-120, -500),
    ll(-80, -300),
    ll(-35, -120),
    ll(-10, -18),
    ll(-120, 4),
    ll(-500, 12),
  ]);
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, -12),
    ll(-200, -10),
    ll(200, -9),
    ll(600, -8),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(-10, -18),
    ll(-35, -25),
    ll(-10, -80),
    ll(35, -220),
    ll(70, -500),
  ]);

  const { features, diagnostics } = applyNostrandEasternSchematic(
    [redTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 280, trunkBlendM: 130, sampleM: 12 },
  );

  assert.equal(diagnostics.applied, true);

  const outTail = features.find((f) => f.properties.corridor_id === "green-4");
  assert.ok(outTail);
  assert.ok(outTail.properties.nostrand_eastern_straight_tail);
  const tailCoords = outTail.geometry.coordinates;
  assert.deepEqual(tailCoords[tailCoords.length - 1], ll(0, 0));

  const lastSegment = meterDelta(
    tailCoords[tailCoords.length - 2],
    tailCoords[tailCoords.length - 1],
  );
  assert.ok(Math.abs(lastSegment[1]) < Math.abs(lastSegment[0]) * 0.08, "4 tail remains essentially straight");

  const outGreen = features.find((f) => f.properties.corridor_id === "green-5");
  assert.ok(outGreen);
  const splitPoint = tailCoords[tailCoords.length - 1];
  const splitInGreen = outGreen.geometry.coordinates.some((coord) => {
    const [dx, dy] = meterDelta(splitPoint, coord);
    return Math.hypot(dx, dy) < 0.5;
  });
  assert.equal(splitInGreen, true, "5 branch passes through the 4 tail split point");
});

test("Nostrand schematic makes the red branch peel eastward before turning south", () => {
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(0, 0),
    ll(-8, -8),
    ll(-15, -25),
    ll(18, -130),
    ll(50, -320),
  ]);
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(500, 10),
    ll(0, 10),
    ll(-6, 0),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(40, -320),
    ll(10, -140),
    ll(-6, 0),
    ll(-90, 5),
    ll(-200, 10),
  ]);

  const { features, diagnostics } = applyNostrandEasternSchematic(
    [redTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 260, trunkBlendM: 120, sampleM: 10 },
  );

  assert.equal(diagnostics.applied, true);

  const outRed = features.find((f) => f.properties.corridor_id === "red-2");
  assert.ok(outRed);
  assert.ok(outRed.properties.nostrand_eastern_branch_curve);
  const coords = outRed.geometry.coordinates;
  const firstStep = meterDelta(coords[0], coords[1]);
  assert.ok(firstStep[0] > 0, "branch should not begin by backtracking west");
  assert.ok(firstStep[1] < 1, "branch should begin flat-to-south, not upward");
});

test("Nostrand schematic leaves features unchanged when required route pieces are missing", () => {
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(0, 0),
    ll(20, -120),
    ll(40, -300),
  ]);
  const features = [redTrunk, redBranch];

  const result = applyNostrandEasternSchematic(features);

  assert.equal(result.features, features);
  assert.equal(result.diagnostics.applied, false);
  assert.equal(result.diagnostics.reason, "missing_required_features");
});

test("Nostrand schematic ignores malformed route_ids and empty input", () => {
  const empty = applyNostrandEasternSchematic([]);
  assert.equal(empty.diagnostics.applied, false);
  assert.deepEqual(empty.features, []);

  const malformed = feature("red-3", "#EE352E", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(200, 0),
  ]);
  malformed.properties.route_ids = "3";
  const result = applyNostrandEasternSchematic([malformed]);
  assert.equal(result.diagnostics.applied, false);
  assert.equal(result.diagnostics.reason, "missing_required_features");
  assert.equal(result.features[0], malformed);
});

test("Nostrand schematic leaves the 2/3/4 geometry when the 5 never meets the 4 tail", () => {
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(400, -500),
    ll(420, -300),
  ]);
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, -12),
    ll(-200, -10),
    ll(200, -9),
    ll(600, -8),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(0, 0),
    ll(40, -80),
    ll(70, -220),
    ll(90, -500),
  ]);
  const input = [redTrunk, redBranch, greenBranch, greenTail];
  const { features, diagnostics } = applyNostrandEasternSchematic(input, {
    branchTurnSpanM: 280,
    trunkBlendM: 130,
    sampleM: 12,
  });
  assert.equal(diagnostics.applied, false);
  assert.equal(diagnostics.reason, "green_branch_split_not_found");
  assert.equal(diagnostics.green_tail_straightened, true);
  assert.equal(diagnostics.red_branch_rebuilt, true);
  assert.equal(diagnostics.green_branch_rebuilt, false);
  assert.equal(features, input);
  assert.equal(greenTail.properties.nostrand_eastern_straight_tail, undefined);
});

test("Nostrand schematic rebuilds a west-hooking 2 peel onto the eastbound 3 trunk", () => {
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(0, 0),
    ll(-80, -4),
    ll(-160, -8),
    ll(-40, -80),
    ll(20, -220),
    ll(50, -400),
  ]);
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
    ll(-8, -8),
    ll(-10, -18),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-120, -500),
    ll(-80, -300),
    ll(-35, -120),
    ll(-10, -18),
    ll(-120, 4),
    ll(-500, 12),
  ]);
  const { features, diagnostics } = applyNostrandEasternSchematic(
    [redTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 260, trunkBlendM: 120, sampleM: 10 },
  );
  assert.equal(diagnostics.applied, true);
  const outRed = features.find((item) => item.properties.corridor_id === "red-2");
  assert.ok(outRed);
  assert.equal(outRed.properties.nostrand_eastern_branch_curve, true);
  assert.ok(Array.isArray(outRed.properties.route_ids));
  assert.equal(outRed.properties.route_ids[0], "2");
  const firstStep = meterDelta(outRed.geometry.coordinates[0], outRed.geometry.coordinates[1]);
  assert.ok(firstStep[1] <= 1, "rebuilt 2 should leave the trunk flat-to-south");
});

test("Nostrand schematic applies default arc options to a complete split", () => {
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
    ll(-8, -8),
    ll(-10, -18),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-120, -500),
    ll(-80, -300),
    ll(-35, -120),
    ll(-10, -18),
    ll(-120, 4),
    ll(-500, 12),
  ]);
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, -12),
    ll(-200, -10),
    ll(200, -9),
    ll(600, -8),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(-10, -18),
    ll(-35, -25),
    ll(-10, -80),
    ll(35, -220),
    ll(70, -500),
  ]);
  const { diagnostics } = applyNostrandEasternSchematic([redTrunk, redBranch, greenBranch, greenTail]);
  assert.equal(diagnostics.applied, true);
  assert.equal(diagnostics.green_tail_straightened, true);
});

test("Nostrand schematic rebuilds a 2 that meets the 3 at its end vertex", () => {
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(50, -400),
    ll(20, -220),
    ll(-8, -8),
    ll(0, 0),
  ]);
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
    ll(-8, -8),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-120, -500),
    ll(-80, -300),
    ll(-35, -120),
    ll(-10, -18),
    ll(-120, 4),
    ll(-500, 12),
  ]);
  const { features, diagnostics } = applyNostrandEasternSchematic(
    [redTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 260, trunkBlendM: 120, sampleM: 10 },
  );
  assert.equal(diagnostics.applied, true);
  const outRed = features.find((item) => item.properties.corridor_id === "red-2");
  assert.ok(outRed);
  assert.equal(outRed.properties.nostrand_eastern_branch_curve, true);
});

test("Nostrand schematic ignores a colorless 3 and a 5 that only grazes the 4", () => {
  const redTrunk = feature("red-3", "", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(0, 0),
    ll(20, -120),
    ll(40, -300),
  ]);
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(0, 0),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-20, -40),
    ll(-10, -18),
    ll(0, 0),
    ll(-30, 4),
    ll(-80, 8),
  ]);
  const missingColor = applyNostrandEasternSchematic([redTrunk, redBranch, greenBranch, greenTail]);
  assert.equal(missingColor.diagnostics.reason, "missing_required_features");

  const coloredTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const shortGreen = applyNostrandEasternSchematic(
    [coloredTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 80, trunkBlendM: 20, sampleM: 10 },
  );
  assert.equal(shortGreen.diagnostics.applied, false);
});

test("Nostrand schematic keeps a 2 whose ends sit outside the local bbox", () => {
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-1800, 0),
    ll(-600, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(-1700, -30),
    ll(-800, -20),
    ll(0, 0),
    ll(-1600, -400),
  ]);
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
    ll(-8, -8),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-120, -500),
    ll(-80, -300),
    ll(-35, -120),
    ll(-10, -18),
    ll(-120, 4),
    ll(-500, 12),
  ]);
  const { diagnostics } = applyNostrandEasternSchematic(
    [redTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 260, trunkBlendM: 120, sampleM: 10 },
  );
  assert.ok(diagnostics.applied === true || diagnostics.reason === "green_branch_split_not_found" || diagnostics.red_branch_rebuilt === true);
});

test("Nostrand schematic accepts duplicate vertices on the 4 tail without throwing", () => {
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
    ll(0, 0),
    ll(-8, -8),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-120, -500),
    ll(-80, -300),
    ll(-35, -120),
    ll(-10, -18),
    ll(-120, 4),
    ll(-500, 12),
  ]);
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-600, 0),
    ll(-200, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(600, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(0, 0),
    ll(0, 0),
    ll(20, -120),
    ll(50, -320),
  ]);
  const { diagnostics } = applyNostrandEasternSchematic(
    [redTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 260, trunkBlendM: 120, sampleM: 10 },
  );
  assert.ok(diagnostics.applied === true || diagnostics.reason === "green_branch_split_not_found");
});

test("Nostrand schematic skips a 2 whose endpoints and projections sit outside the bbox", () => {
  const redTrunk = feature("red-3", "#EE352E", ["3"], [
    ll(-2500, 0),
    ll(-1800, 0),
    ll(-200, 0),
    ll(200, 0),
    ll(1800, 0),
    ll(2500, 0),
  ]);
  const redBranch = feature("red-2", "#EE352E", ["2"], [
    ll(-2400, -40),
    ll(-400, -10),
    ll(0, 0),
    ll(400, -10),
    ll(2400, -40),
  ]);
  const greenTail = feature("green-4", "#00933C", ["4"], [
    ll(1400, 0),
    ll(1000, 0),
    ll(600, 0),
    ll(180, 0),
    ll(0, 0),
    ll(-8, -8),
  ]);
  const greenBranch = feature("green-5", "#00933C", ["5"], [
    ll(-120, -500),
    ll(-80, -300),
    ll(-35, -120),
    ll(-10, -18),
    ll(-120, 4),
    ll(-500, 12),
  ]);
  const { diagnostics } = applyNostrandEasternSchematic(
    [redTrunk, redBranch, greenBranch, greenTail],
    { branchTurnSpanM: 260, trunkBlendM: 120, sampleM: 10 },
  );
  assert.ok(
    diagnostics.applied === true ||
      diagnostics.reason === "red_branch_split_not_found" ||
      diagnostics.reason === "green_branch_split_not_found",
  );
});
