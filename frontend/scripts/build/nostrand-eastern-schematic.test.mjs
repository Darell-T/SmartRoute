import { test } from "node:test";
import assert from "node:assert/strict";

import { applyNostrandEasternSchematic } from "./nostrand-eastern-schematic.mjs";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / 84410;

function ll(xM, yM) {
  return [-73.951 + xM * DEG_PER_M_LON, 40.670 + yM * DEG_PER_M_LAT];
}

function feature(id, color, routeIds, coords) {
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

function meterDelta(a, b) {
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
  assert.ok(outTail.properties.nostrand_eastern_straight_tail);
  assert.deepEqual(outTail.geometry.coordinates.at(-1), ll(0, 0));

  const lastSegment = meterDelta(
    outTail.geometry.coordinates.at(-2),
    outTail.geometry.coordinates.at(-1),
  );
  assert.ok(Math.abs(lastSegment[1]) < Math.abs(lastSegment[0]) * 0.08, "4 tail remains essentially straight");

  const outGreen = features.find((f) => f.properties.corridor_id === "green-5");
  const splitPoint = outTail.geometry.coordinates.at(-1);
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
  assert.ok(outRed.properties.nostrand_eastern_branch_curve);
  const coords = outRed.geometry.coordinates;
  const firstStep = meterDelta(coords[0], coords[1]);
  assert.ok(firstStep[0] > 0, "branch should not begin by backtracking west");
  assert.ok(firstStep[1] < 1, "branch should begin flat-to-south, not upward");
});
