import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildMottHavenFiveSchematicLens,
  buildMottHavenSixSchematicMerge,
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

function distanceMeters(a: Position, b: Position): number {
  const eastM = (a[0] - b[0]) * metersPerDegLng((a[1] + b[1]) / 2);
  const northM = (a[1] - b[1]) * M_PER_DEG_LAT;
  return Math.hypot(eastM, northM);
}

function requireDiagnosticNumber(value: number | undefined, label: string): number {
  if (value === undefined) throw new TypeError(`expected ${label}`);
  return value;
}

function requireDiagnosticPoint(value: Position | undefined, label: string): Position {
  if (!value) throw new TypeError(`expected ${label}`);
  return value;
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

  const again = buildMottHavenFiveSchematicLens({
    branchCoords,
    trunkCoords,
    mergeDistanceM: 310,
    sampleM: 8,
  });
  assert.equal(result.diagnostics.ok, true);
  assert.equal(JSON.stringify(result), JSON.stringify(again));
  assert.ok(result.coordinates.length > branchCoords.length * 4);
  assert.ok(requireDiagnosticNumber(result.diagnostics.topApproachLatSpreadM, "topApproachLatSpreadM") < 8);
  assert.ok(requireDiagnosticNumber(result.diagnostics.maxTrunkDistanceM, "maxTrunkDistanceM") > 145);
  assert.ok(requireDiagnosticNumber(result.diagnostics.mergeDistanceM, "mergeDistanceM") < 1);
  assert.ok(requireDiagnosticNumber(result.diagnostics.maxTurnDeg, "maxTurnDeg") < 55);

  const topHit = Math.min(...result.coordinates.map((coord) => distanceMeters(coord, trunkCoords[0])));
  assert.ok(topHit < 1, `expected the 5 to meet the 4/5 trunk at the top split, got ${topHit.toFixed(1)}m`);

  const xs = result.coordinates.map((coord) => local(coord)[0]);
  assert.ok(Math.min(...xs) < -180, "lens should reach the Walton-side west loop");

  const afterTop = result.coordinates.slice(result.diagnostics.prefixCutIndex);
  const midLens = afterTop.filter((coord) => {
    const [, y] = local(coord);
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
  const topApproachLatSpreadM = requireDiagnosticNumber(
    result.diagnostics.topApproachLatSpreadM,
    "topApproachLatSpreadM",
  );
  assert.ok(
    topApproachLatSpreadM < 5,
    `expected flat E 149 St entry, got ${topApproachLatSpreadM.toFixed(1)}m spread`,
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
  const topLocal = local(requireDiagnosticPoint(result.diagnostics.topPoint, "topPoint"));
  assert.ok(
    Math.abs(topLocal[1] - 100) < 15,
    `expected the 5 peel to start at the 2/4 crossing latitude, got local y=${topLocal[1].toFixed(1)}m`,
  );

  const entryLocal = local(requireDiagnosticPoint(result.diagnostics.entryPoint, "entryPoint"));
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
  const maxTurnDeg = requireDiagnosticNumber(result.diagnostics.maxTurnDeg, "maxTurnDeg");
  assert.ok(
    maxTurnDeg < 65,
    `expected the junction diagnostic to ignore preserved upstream branch kinks, got ${maxTurnDeg.toFixed(1)}deg`,
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

  const again = buildMottHavenSixSchematicMerge({
    branchCoords,
    mainlineCoords,
    mergeDistanceM: 430,
    entryEastM: 330,
    entryNorthM: 80,
    sampleM: 8,
  });
  assert.equal(result.diagnostics.ok, true);
  assert.equal(JSON.stringify(result), JSON.stringify(again));
  assert.ok(result.coordinates.length > 12);
  assert.ok(result.sharedMainlineCoords.length >= 2);
  const mergeDistanceM = requireDiagnosticNumber(result.diagnostics.mergeDistanceM, "mergeDistanceM");
  assert.ok(
    mergeDistanceM < 1,
    `expected the 6 merge to terminate on the trunk, got ${mergeDistanceM.toFixed(1)}m`,
  );

  const localXs = result.coordinates.map((coord) => local(coord)[0]);
  assert.ok(
    Math.min(...localXs) > -80,
    `expected the old lower teardrop west endpoint to be removed, got min x=${Math.min(...localXs).toFixed(1)}m`,
  );

  const sharedStart = result.sharedMainlineCoords[0];
  assert.ok(sharedStart);
  assert.ok(
    distanceMeters(sharedStart, requireDiagnosticPoint(result.diagnostics.mergePoint, "mergePoint")) < 1,
    "shared 4/6 mainline should begin exactly where the 6 branch merges",
  );
});

test("Mott Haven schematic builders reject empty and malformed polylines", () => {
  const missingBranch = buildMottHavenFiveSchematicLens({
    branchCoords: [],
    trunkCoords: [p(0, 0), p(0, -100)],
  });
  const missingTrunk = buildMottHavenFiveSchematicLens({
    branchCoords: [p(100, 0), p(0, 0)],
    trunkCoords: [p(0, 0)],
  });
  const missingSixBranch = buildMottHavenSixSchematicMerge({
    branchCoords: null,
    mainlineCoords: [p(0, 0), p(0, -100)],
  });
  assert.equal(missingBranch.diagnostics.ok, false);
  assert.equal(missingBranch.diagnostics.reason, "missing_branch");
  assert.equal(missingTrunk.diagnostics.ok, false);
  assert.equal(missingTrunk.diagnostics.reason, "missing_trunk");
  assert.equal(missingSixBranch.diagnostics.ok, false);
  assert.equal(missingSixBranch.diagnostics.reason, "missing_branch");
  assert.deepEqual(missingSixBranch.sharedMainlineCoords, []);

  const missingSixMainline = buildMottHavenSixSchematicMerge({
    branchCoords: [p(100, 0), p(0, 0)],
    mainlineCoords: [p(0, 0)],
  });
  assert.equal(missingSixMainline.diagnostics.ok, false);
  assert.equal(missingSixMainline.diagnostics.reason, "missing_mainline");
  assert.deepEqual(missingSixMainline.sharedMainlineCoords, []);

  const missingSixBranchEmpty = buildMottHavenSixSchematicMerge({
    branchCoords: [],
    mainlineCoords: [p(0, 0), p(0, -100)],
  });
  assert.equal(missingSixBranchEmpty.diagnostics.ok, false);
  assert.equal(missingSixBranchEmpty.diagnostics.reason, "missing_branch");
  assert.deepEqual(missingSixBranchEmpty.coordinates, []);
});

test("Mott Haven schematic builders still close on a trunk shorter than the requested merge distance", () => {
  const shortTrunk = [p(0, 0), p(0, -40)];
  const branchCoords = [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ];
  const five = buildMottHavenFiveSchematicLens({
    branchCoords,
    trunkCoords: shortTrunk,
    mergeDistanceM: 310,
    sampleM: 8,
  });
  assert.equal(five.diagnostics.ok, true);
  assert.ok(five.diagnostics.mergePoint);
  assert.ok(
    distanceMeters(five.diagnostics.mergePoint, shortTrunk[shortTrunk.length - 1]) < 1,
    "merge beyond the trunk must clamp to the trunk end",
  );

  const six = buildMottHavenSixSchematicMerge({
    branchCoords: [
      p(400, 80),
      p(250, 40),
      p(80, 10),
    ],
    mainlineCoords: shortTrunk,
    mergeDistanceM: 430,
    entryEastM: 120,
    entryNorthM: 20,
    sampleM: 8,
  });
  assert.equal(six.diagnostics.ok, true);
  assert.ok(six.diagnostics.mergePoint);
  assert.ok(
    distanceMeters(six.diagnostics.mergePoint, shortTrunk[shortTrunk.length - 1]) < 1,
    "6 merge beyond the mainline must clamp to the mainline end",
  );
  assert.ok(six.sharedMainlineCoords.length >= 1);
});

test("Mott Haven schematic builders use defaults and keep a prefix already on the entry", () => {
  const empty = buildMottHavenFiveSchematicLens();
  assert.equal(empty.diagnostics.ok, false);
  assert.equal(empty.diagnostics.reason, "missing_branch");

  const trunkCoords = [p(0, 0), p(0, -100), p(0, -220), p(0, -340)];
  const onEntry = buildMottHavenFiveSchematicLens({
    branchCoords: [p(420, 0), p(200, 0), p(40, -40)],
    trunkCoords,
    mergeDistanceM: 0,
    sampleM: 10,
    eastEntryM: 420,
  });
  assert.equal(onEntry.diagnostics.ok, true);
  assert.ok(onEntry.coordinates.length >= 2);

  const farReference = buildMottHavenFiveSchematicLens({
    branchCoords: [p(900, 70), p(650, 40), p(430, -10), p(250, -25), p(90, -45), p(20, -35)],
    trunkCoords,
    parallelReferenceCoords: [p(2000, 2000), p(2100, 2100)],
    mergeDistanceM: 300,
    sampleM: 10,
  });
  assert.equal(farReference.diagnostics.ok, true);
  assert.equal(farReference.diagnostics.parallelReferenceUsed, false);

  const duplicateTrunk = [p(0, 0), p(0, 0), p(0, -120), p(0, -240)];
  const withDup = buildMottHavenFiveSchematicLens({
    branchCoords: [p(400, 0), p(40, -40)],
    trunkCoords: duplicateTrunk,
    mergeDistanceM: 200,
    sampleM: 8,
  });
  assert.equal(withDup.diagnostics.ok, true);

  const sixOnMerge = buildMottHavenSixSchematicMerge({
    branchCoords: [p(80, 10), p(0, -40)],
    mainlineCoords: [p(0, 0), p(0, -40), p(0, -80)],
    mergeDistanceM: 45,
    entryEastM: 0,
    entryNorthM: 0,
    sampleM: 8,
  });
  assert.equal(sixOnMerge.diagnostics.ok, true);
  assert.ok(sixOnMerge.coordinates.length >= 2);
});
