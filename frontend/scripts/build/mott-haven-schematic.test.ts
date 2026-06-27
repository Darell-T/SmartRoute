import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildMottHavenFiveSchematicLens,
  buildMottHavenSixSchematicMerge,
  distanceMeters,
} from "./mott-haven-schematic.ts";
import type { Position } from "./types.ts";

const M_PER_DEG_LAT = 110574;
const ORIGIN: Position = [-73.928, 40.81725];

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function p(eastM: number, northM: number): Position {
  return [
    ORIGIN[0] + eastM / metersPerDegLng(ORIGIN[1]),
    ORIGIN[1] + northM / M_PER_DEG_LAT,
  ];
}

function local(coord: Position): Position {
  return [
    (coord[0] - ORIGIN[0]) * metersPerDegLng(ORIGIN[1]),
    (coord[1] - ORIGIN[1]) * M_PER_DEG_LAT,
  ];
}

test("Mott Haven schematic lens closes at the 4/5 trunk, bows west, and rejoins lower", () => {
  const branchCoords = [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ];
  const trunkCoords = [
    p(0, 0),
    p(-8, -90),
    p(-14, -200),
    p(-20, -320),
    p(-25, -440),
  ];

  const result = buildMottHavenFiveSchematicLens({
    branchCoords,
    trunkCoords,
    mergeDistanceM: 310,
    sampleM: 8,
  });

  assert.equal(result.diagnostics.ok, true);
  assert.ok(result.coordinates.length > branchCoords.length * 4);
  assert.ok(result.diagnostics.topApproachLatSpreadM! < 8);
  assert.ok(result.diagnostics.maxTrunkDistanceM! > 145);
  assert.ok(result.diagnostics.mergeDistanceM! < 1);
  assert.ok(result.diagnostics.maxTurnDeg! < 55);

  const topHit = Math.min(...result.coordinates.map((coord) => distanceMeters(coord, trunkCoords[0])));
  assert.ok(topHit < 1, `expected the 5 to meet the 4/5 trunk at the top split, got ${topHit.toFixed(1)}m`);

  const xs = result.coordinates.map((coord) => local(coord)[0]);
  assert.ok(Math.min(...xs) < -180, "lens should reach the Walton-side west loop");

  const afterTop = result.coordinates.slice(result.diagnostics.prefixCutIndex);
  const midLens = afterTop.filter((coord) => {
    const [x, y] = local(coord);
    return y < -25 && y > -285;
  });
  assert.ok(
    midLens.every((coord) => local(coord)[0] <= 5),
    "after the top split the 5 should stay west of the 4 trunk until the lower Y merge",
  );
});

test("Mott Haven schematic lens preserves a street-aligned east approach before the peel", () => {
  const branchCoords = [
    p(820, 0),
    p(620, 0),
    p(430, 0),
    p(260, -20),
    p(40, -50),
  ];
  const trunkCoords = [
    p(0, 0),
    p(0, -100),
    p(0, -220),
    p(0, -340),
  ];

  const result = buildMottHavenFiveSchematicLens({
    branchCoords,
    trunkCoords,
    mergeDistanceM: 300,
    sampleM: 10,
  });

  assert.equal(result.diagnostics.ok, true);
  assert.ok(
    result.diagnostics.topApproachLatSpreadM! < 5,
    `expected flat E 149 St entry, got ${result.diagnostics.topApproachLatSpreadM!.toFixed(1)}m spread`,
  );

  const topRun = result.coordinates.filter((coord) => {
    const [x, y] = local(coord);
    return x >= -1 && x <= 420 && Math.abs(y) < 8;
  });
  assert.ok(topRun.length >= 12, "top approach should be a visible straight run before the peel");
});

test("Mott Haven schematic lens follows the 2 line until the 4/5 trunk crossing", () => {
  const branchCoords = [
    p(900, 150),
    p(650, 130),
    p(430, 110),
    p(260, 90),
    p(40, -50),
  ];
  const trunkCoords = [
    p(-10, 230),
    p(-5, 110),
    p(0, 0),
    p(0, -130),
    p(0, -270),
    p(0, -400),
  ];
  const routeTwoReference = [
    p(-520, 145),
    p(-220, 126),
    p(0, 110),
    p(440, 76),
  ];

  const result = buildMottHavenFiveSchematicLens({
    branchCoords,
    trunkCoords,
    parallelReferenceCoords: routeTwoReference,
    parallelOffsetM: 10,
    mergeDistanceM: 330,
    sampleM: 8,
  });

  assert.equal(result.diagnostics.ok, true);
  const topLocal = local(result.diagnostics.topPoint!);
  assert.ok(
    Math.abs(topLocal[1] - 100) < 15,
    `expected the 5 peel to start at the 2/4 crossing latitude, got local y=${topLocal[1].toFixed(1)}m`,
  );

  const entryLocal = local(result.diagnostics.entryPoint!);
  const slope = (76 - 110) / 440;
  const expectedEntryY = 110 + slope * entryLocal[0] - 10;
  assert.ok(
    Math.abs(entryLocal[1] - expectedEntryY) < 10,
    `expected the 5 entry to track the red 2 corridor, got y=${entryLocal[1].toFixed(1)} expected ${expectedEntryY.toFixed(1)}`,
  );

  const parallelReferenceDistanceM = result.diagnostics.parallelReferenceDistanceM;
  assert.ok(parallelReferenceDistanceM != null);
  assert.ok(
    parallelReferenceDistanceM < 15,
    `expected the crossing to be found on the 2 reference, got ${parallelReferenceDistanceM.toFixed(1)}m`,
  );
});

test("Mott Haven schematic turn diagnostics ignore preserved upstream branch geometry", () => {
  const branchCoords = [
    p(900, 150),
    p(700, 150),
    p(710, 150),
    p(690, 150),
    p(650, 130),
    p(430, 110),
    p(260, 90),
    p(40, -50),
  ];
  const trunkCoords = [
    p(-10, 230),
    p(-5, 110),
    p(0, 0),
    p(0, -130),
    p(0, -270),
    p(0, -400),
  ];
  const routeTwoReference = [
    p(-520, 145),
    p(-220, 126),
    p(0, 110),
    p(440, 76),
  ];

  const result = buildMottHavenFiveSchematicLens({
    branchCoords,
    trunkCoords,
    parallelReferenceCoords: routeTwoReference,
    parallelOffsetM: 10,
    mergeDistanceM: 330,
    sampleM: 8,
  });

  assert.equal(result.diagnostics.ok, true);
  assert.ok(
    result.diagnostics.maxTurnDeg! < 65,
    `expected the junction diagnostic to ignore preserved upstream branch kinks, got ${result.diagnostics.maxTurnDeg!.toFixed(1)}deg`,
  );
});

test("Mott Haven 6 schematic merge removes the lower teardrop and ends on the trunk", () => {
  const branchCoords = [
    p(760, 80),
    p(560, 60),
    p(360, 20),
    p(180, -80),
    p(-80, -300),
    p(-220, -500),
  ];
  const mainlineCoords = [
    p(0, 180),
    p(0, 60),
    p(0, -80),
    p(0, -220),
    p(0, -420),
    p(0, -620),
  ];

  const result = buildMottHavenSixSchematicMerge({
    branchCoords,
    mainlineCoords,
    mergeDistanceM: 430,
    entryEastM: 330,
    entryNorthM: 80,
    sampleM: 8,
  });

  assert.equal(result.diagnostics.ok, true);
  assert.ok(result.coordinates.length > 12);
  assert.ok(result.sharedMainlineCoords.length >= 2);
  assert.ok(
    result.diagnostics.mergeDistanceM! < 1,
    `expected the 6 merge to terminate on the trunk, got ${result.diagnostics.mergeDistanceM!.toFixed(1)}m`,
  );

  const localXs = result.coordinates.map((coord) => local(coord)[0]);
  assert.ok(
    Math.min(...localXs) > -80,
    `expected the old lower teardrop west endpoint to be removed, got min x=${Math.min(...localXs).toFixed(1)}m`,
  );

  const sharedStart = result.sharedMainlineCoords[0];
  assert.ok(sharedStart);
  assert.ok(
    distanceMeters(sharedStart, result.diagnostics.mergePoint!) < 1,
    "shared 4/6 mainline should begin exactly where the 6 branch merges",
  );
});
