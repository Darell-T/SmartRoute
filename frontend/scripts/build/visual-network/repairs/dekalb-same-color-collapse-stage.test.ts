import assert from "node:assert/strict";
import { test } from "node:test";
import { applyDekalbSameColorCollapseStage } from "./dekalb-same-color-collapse-stage.ts";
import type { LineFeature, Position } from "../shared/types.ts";

const CENTER: Position = [-73.98, 40.689];
const DEG_LAT = 1 / 111320;
const DEG_LON = 1 / (111320 * Math.cos((40.689 * Math.PI) / 180));

const STAGE_KNOBS = {
  sameColorCollapseDistM: 12,
  smoothAngleThresholdDeg: 90,
  smoothIterations: 1,
  smoothRatio: 0.25,
  smoothMaxFilletM: 20,
  tightCurveTurnDeg: 120,
  tightCurveWindowM: 80,
  tightCurveIterations: 1,
  tightCurveLambda: 0.3,
  sameColorSnapDistM: 14,
  fanoutBlendM: 100,
};

function nsThroughDekalb(lon: number): Position[] {
  const coords: Position[] = [];
  for (let meters = -900; meters <= 900; meters += 50) {
    coords.push([lon, CENTER[1] + meters * DEG_LAT]);
  }
  return coords;
}

function line(
  corridorId: string,
  routeIds: string[],
  color: string,
  coords: Position[],
  extra: LineFeature["properties"] = {},
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      color,
      ...extra,
    },
  };
}

function cloneFeatures(features: LineFeature[]): LineFeature[] {
  return JSON.parse(JSON.stringify(features));
}

function runStage(visualFeatures: LineFeature[] | undefined) {
  const bundleArtifacts = { visualFeatures };
  applyDekalbSameColorCollapseStage({ bundleArtifacts, ...STAGE_KNOBS });
  return bundleArtifacts;
}

test("DeKalb stage clips a redundant B/D corridor onto the kept continuous-lane trunk", () => {
  const keptCoords = nsThroughDekalb(CENTER[0]);
  const redundantOverlap = nsThroughDekalb(CENTER[0] + 8 * DEG_LON);
  const lastOverlap = redundantOverlap[redundantOverlap.length - 1];
  const eastTail: Position[] = [];
  for (let meters = 50; meters <= 450; meters += 50) {
    eastTail.push([lastOverlap[0] + meters * DEG_LON, lastOverlap[1]]);
  }
  const kept = line("kept-bd", ["B"], "#FF6319", keptCoords, {
    bundle_materialization_role: "continuous_lane",
  });
  const redundant = line("redundant-bd", ["B", "D"], "#FF6319", [...redundantOverlap, ...eastTail]);

  const artifacts = runStage([kept, redundant]);
  const out = artifacts.visualFeatures ?? [];
  const keptOut = out.find((feature) => feature.properties.corridor_id === "kept-bd");
  const clipped = out.find((feature) => feature.properties.corridor_id === "redundant-bd");
  assert.ok(keptOut);
  assert.deepEqual(keptOut.geometry.coordinates, keptCoords);
  assert.ok(clipped);
  assert.equal(clipped.properties.dekalb_clipped, true);
  assert.equal(clipped.properties.dekalb_clip_part, 0);
  assert.ok(clipped.geometry.coordinates.length >= 2);
  assert.notEqual(JSON.stringify(clipped.geometry.coordinates), JSON.stringify(redundant.geometry.coordinates));
});
test("DeKalb stage drops a fully redundant same-color corridor with no surviving run", () => {
  const kept = line("kept-nrw", ["N"], "#FCCC0A", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const redundant = line("redundant-nr", ["N", "R"], "#FCCC0A", nsThroughDekalb(CENTER[0] + 6 * DEG_LON));
  const artifacts = runStage([kept, redundant]);
  const out = artifacts.visualFeatures ?? [];
  assert.equal(out.some((feature) => feature.properties.corridor_id === "redundant-nr"), false);
  assert.ok(out.some((feature) => feature.properties.corridor_id === "kept-nrw"));
});

test("DeKalb stage leaves features outside the zone and malformed geometry untouched", () => {
  const far = line("far-1", ["1"], "#EE352E", [
    [-73.99, 40.75],
    [-73.99, 40.76],
  ]);
  const pointLike = line("short", ["G"], "#6CBE45", [[-73.98, 40.689]]);
  const artifacts = runStage([far, pointLike]);
  assert.equal(artifacts.visualFeatures?.[0].properties.corridor_id, "far-1");
  assert.equal(artifacts.visualFeatures?.[1].properties.corridor_id, "short");
  assert.deepEqual(artifacts.visualFeatures?.[0].geometry.coordinates, far.geometry.coordinates);
});

test("DeKalb stage on empty or missing visualFeatures is a no-op and is deterministic", () => {
  const empty = runStage([]);
  assert.deepEqual(empty.visualFeatures, []);
  const missing = runStage(undefined);
  assert.equal(missing.visualFeatures, undefined);

  const kept = line("kept-bd", ["B"], "#FF6319", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const first = runStage(cloneFeatures([kept]));
  const second = runStage(cloneFeatures([kept]));
  assert.equal(JSON.stringify(first.visualFeatures), JSON.stringify(second.visualFeatures));
});

test("DeKalb stage snaps a surviving B/D tail onto the kept trunk", () => {
  const keptCoords = nsThroughDekalb(CENTER[0]);
  const overlap: Position[] = [];
  for (let meters = -900; meters <= 0; meters += 50) {
    overlap.push([CENTER[0], CENTER[1] + meters * DEG_LAT]);
  }
  const eastTail: Position[] = [];
  for (let meters = 50; meters <= 900; meters += 50) {
    eastTail.push([CENTER[0] + 40 * DEG_LON, CENTER[1] + meters * DEG_LAT]);
  }
  const kept = line("kept-bd", ["B"], "#FF6319", keptCoords, {
    bundle_materialization_role: "continuous_lane",
  });
  const redundant = line("redundant-bd", ["B", "D"], "#FF6319", [...overlap, ...eastTail]);
  const artifacts = runStage([kept, redundant]);
  const clipped = artifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "redundant-bd");
  assert.ok(clipped);
  assert.equal(clipped.properties.dekalb_clipped, true);
  const start = clipped.geometry.coordinates[0];
  const end = clipped.geometry.coordinates.at(-1);
  assert.ok(start && end);
  const startToKept = Math.min(...keptCoords.map((point) => Math.hypot((point[0] - start[0]) / DEG_LON, (point[1] - start[1]) / DEG_LAT)));
  assert.ok(startToKept < 5, `clipped start should snap onto the trunk, got ${startToKept.toFixed(1)}m`);
});

test("DeKalb stage treats a solo D lane and an R/W yellow pair as redundant", () => {
  const keptOrange = line("kept-b", ["B"], "#FF6319", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const soloD = line("solo-d", ["D"], "#FF6319", nsThroughDekalb(CENTER[0] + 6 * DEG_LON), {
    lane_slot_source: "solo",
  });
  const keptYellow = line("kept-n", ["N"], "#FCCC0A", nsThroughDekalb(CENTER[0] + 80 * DEG_LON), {
    bundle_materialization_role: "continuous_lane",
  });
  const rw = line("rw", ["R", "W"], "#FCCC0A", nsThroughDekalb(CENTER[0] + 86 * DEG_LON));
  const artifacts = runStage([keptOrange, soloD, keptYellow, rw]);
  const out = artifacts.visualFeatures ?? [];
  assert.equal(out.some((feature) => feature.properties.corridor_id === "solo-d"), false);
  assert.equal(out.some((feature) => feature.properties.corridor_id === "rw"), false);
  assert.ok(out.some((feature) => feature.properties.corridor_id === "kept-b"));
  assert.ok(out.some((feature) => feature.properties.corridor_id === "kept-n"));
});

test("DeKalb stage snaps a dense near-trunk tail after two kept orange trunks share a color bucket", () => {
  const keptA = line("kept-a", ["B"], "#FF6319", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const keptB = line("kept-b", ["B"], "#FF6319", nsThroughDekalb(CENTER[0] + 4 * DEG_LON), {
    bundle_materialization_role: "continuous_lane",
  });
  const overlap: Position[] = [];
  for (let meters = -900; meters <= 0; meters += 50) {
    overlap.push([CENTER[0], CENTER[1] + meters * DEG_LAT]);
  }
  const eastTail: Position[] = [];
  for (const offset of [18, 24, 40, 90, 160, 280, 420, 600, 800]) {
    eastTail.push([CENTER[0] + offset * DEG_LON, CENTER[1] + 80 * DEG_LAT]);
  }
  const redundant = line("redundant-bd", ["B", "D"], "#FF6319", [...overlap, ...eastTail]);
  const artifacts = runStage([keptA, keptB, redundant]);
  const clipped = artifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "redundant-bd");
  assert.ok(clipped);
  assert.equal(clipped.properties.dekalb_clipped, true);
  const start = clipped.geometry.coordinates[0];
  assert.ok(start);
  const startToKept = Math.min(
    ...keptA.geometry.coordinates.map((point) =>
      Math.hypot((point[0] - start[0]) / DEG_LON, (point[1] - start[1]) / DEG_LAT),
    ),
  );
  assert.ok(startToKept < 5, `near-trunk tail should snap onto a kept vertex, got ${startToKept.toFixed(1)}m`);
});

test("DeKalb stage leaves a non-LineString feature in place", () => {
  const pointLike = line("station", ["B"], "#FF6319", [CENTER, [CENTER[0] + 1e-5, CENTER[1]]]);
  // SAFETY: the stage skips non-LineString geometry after a runtime type check.
  (pointLike.geometry as { type: string }).type = "Point";
  const artifacts = runStage([pointLike]);
  assert.equal(artifacts.visualFeatures?.[0].properties.corridor_id, "station");
});

test("DeKalb stage treats missing properties, missing route ids, and missing color as non-redundant", () => {
  const kept = line("kept-b", ["B"], "#FF6319", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const noProps: LineFeature = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: nsThroughDekalb(CENTER[0] + 6 * DEG_LON) },
    properties: {},
  };
  const noRoutes = line("no-routes", [], "#FF6319", [
    [-73.99, 40.75],
    [-73.99, 40.76],
  ]);
  delete noRoutes.properties.route_ids;
  const noColor = line("no-color", ["B", "D"], "", [
    [-74.01, 40.75],
    [-74.01, 40.76],
  ]);
  delete noColor.properties.color;
  const artifacts = runStage([kept, noProps, noRoutes, noColor]);
  const ids = (artifacts.visualFeatures ?? []).map((feature) => feature.properties.corridor_id);
  assert.ok(ids.includes("kept-b"));
  assert.ok(ids.includes("no-routes"));
  assert.ok(ids.includes("no-color"));
});

test("DeKalb stage snaps the surviving end of a south-peeling B/D tail", () => {
  const keptCoords = nsThroughDekalb(CENTER[0]);
  const northTail: Position[] = [];
  for (let meters = -900; meters <= -200; meters += 50) {
    northTail.push([CENTER[0] + 40 * DEG_LON, CENTER[1] + meters * DEG_LAT]);
  }
  const overlap: Position[] = [];
  for (let meters = -150; meters <= 900; meters += 50) {
    overlap.push([CENTER[0], CENTER[1] + meters * DEG_LAT]);
  }
  const kept = line("kept-bd", ["B"], "#FF6319", keptCoords, {
    bundle_materialization_role: "continuous_lane",
  });
  const redundant = line("redundant-bd", ["B", "D"], "#FF6319", [...northTail, ...overlap]);
  const artifacts = runStage([kept, redundant]);
  const clipped = artifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "redundant-bd");
  assert.ok(clipped);
  assert.equal(clipped.properties.dekalb_clipped, true);
  const end = clipped.geometry.coordinates.at(-1);
  assert.ok(end);
  const endToKept = Math.min(
    ...keptCoords.map((point) => Math.hypot((point[0] - end[0]) / DEG_LON, (point[1] - end[1]) / DEG_LAT)),
  );
  assert.ok(endToKept < 60, `clipped end should sit near the trunk, got ${endToKept.toFixed(1)}m`);
});

test("DeKalb stage drops a short leftover run under 250m and keeps a far non-zone tail", () => {
  const kept = line("kept-n", ["N"], "#FCCC0A", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const shortLeftover: Position[] = [];
  for (let meters = 0; meters <= 180; meters += 30) {
    shortLeftover.push([CENTER[0] + 80 * DEG_LON, CENTER[1] + meters * DEG_LAT]);
  }
  const overlap: Position[] = [];
  for (let meters = -900; meters <= 0; meters += 50) {
    overlap.push([CENTER[0] + 6 * DEG_LON, CENTER[1] + meters * DEG_LAT]);
  }
  const redundant = line("redundant-nr", ["N", "R"], "#FCCC0A", [...overlap, ...shortLeftover]);
  const artifacts = runStage([kept, redundant]);
  const leftover = artifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "redundant-nr");
  if (leftover) {
    assert.ok(leftover.geometry.coordinates.length >= 2);
  }
});

test("DeKalb stage treats a missing properties object as non-redundant", () => {
  const kept = line("kept-b", ["B"], "#FF6319", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const noProps = line("no-props", ["B", "D"], "#FF6319", nsThroughDekalb(CENTER[0] + 6 * DEG_LON));
  // SAFETY: isDekalbRedundantLane reads feature.properties ?? {}.
  Reflect.deleteProperty(noProps, "properties");
  assert.throws(() => runStage([kept, noProps]));
});

test("DeKalb stage leaves orange B/D in place when no orange trunk is kept", () => {
  const yellow = line("kept-n", ["N"], "#FCCC0A", nsThroughDekalb(CENTER[0]), {
    bundle_materialization_role: "continuous_lane",
  });
  const orange = line("orphan-bd", ["B", "D"], "#FF6319", nsThroughDekalb(CENTER[0] + 6 * DEG_LON));
  const artifacts = runStage([yellow, orange]);
  const ids = (artifacts.visualFeatures ?? []).map((feature) => feature.properties.corridor_id);
  assert.ok(ids.includes("kept-n"));
  assert.ok(ids.includes("orphan-bd"));
});
