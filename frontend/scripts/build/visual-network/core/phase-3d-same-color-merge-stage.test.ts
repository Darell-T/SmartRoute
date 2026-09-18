import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { applyPhase3dSameColorMergeStage } from "./phase-3d-same-color-merge-stage.ts";
import type { LineFeature, Position } from "../shared/types.ts";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / 84410;

function makePolylineNS(startLon: number, startLat: number, lengthM: number, segmentCount = 8): Position[] {
  const coords: Position[] = [];
  const stepLat = (lengthM * DEG_PER_M_LAT) / segmentCount;
  for (let i = 0; i <= segmentCount; i += 1) {
    coords.push([startLon, startLat + i * stepLat]);
  }
  return coords;
}

function makePolylineEW(startLon: number, startLat: number, lengthM: number, segmentCount = 4): Position[] {
  const coords: Position[] = [];
  const stepLon = (lengthM * DEG_PER_M_LON) / segmentCount;
  for (let i = 0; i <= segmentCount; i += 1) {
    coords.push([startLon + i * stepLon, startLat]);
  }
  return coords;
}

function divergingBranchCoords(): Position[] {
  const overlap = makePolylineNS(-73.99, 40.7, 600, 4);
  const last = overlap[overlap.length - 1];
  return [...overlap.slice(0, -1), ...makePolylineEW(last[0], last[1], 200, 2)];
}

function corridor(
  corridorId: string,
  routeIds: string[],
  coords: Position[],
  extra: LineFeature["properties"] = {},
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      ...extra,
    },
  };
}

function cloneCorridors(features: LineFeature[]): LineFeature[] {
  return JSON.parse(JSON.stringify(features));
}

test("phase 3d clips a diverging same-color branch onto the trunk and writes debug geojson", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const features = [
    corridor("trunk-1", ["1"], makePolylineNS(-73.99, 40.7, 1500, 8), { color: "#EE352E" }),
    corridor("branch-1", ["2"], divergingBranchCoords(), { color: "#EE352E" }),
  ];
  const debugPath = join(reportDir, "same-color-merges.geojson");

  applyPhase3dSameColorMergeStage({
    corridorFeatures: features,
    sameColorMergesGeoJsonPath: debugPath,
  });

  const mergedTrunk = features.find((feature) => feature.properties.corridor_id === "trunk-1");
  const clippedBranch = features.find((feature) => feature.properties.corridor_id === "branch-1");
  assert.ok(mergedTrunk);
  assert.ok(clippedBranch);
  const mergedRouteIds = mergedTrunk.properties.route_ids;
  assert.ok(Array.isArray(mergedRouteIds));
  assert.deepEqual([...mergedRouteIds].sort(), ["1", "2"]);
  assert.equal(clippedBranch.properties.clipped_to_branch_only, true);
  assert.ok(clippedBranch.geometry.coordinates.length >= 2);

  const written = JSON.parse(readFileSync(debugPath, "utf8"));
  assert.equal(written.type, "FeatureCollection");
  assert.equal(written.features[0].properties.visual_feature_type, "same_color_merge");
  assert.equal(written.features[0].properties.trunk_corridor_id, "trunk-1");
  assert.deepEqual(written.features[0].properties.branches_clipped, ["branch-1"]);
});

test("phase 3d is deterministic and stamps missing corridor color from the first route id", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const source = [
    corridor("trunk-1", ["1"], makePolylineNS(-73.99, 40.7, 1500, 8)),
    corridor("branch-1", ["2"], divergingBranchCoords()),
  ];
  const first = cloneCorridors(source);
  const second = cloneCorridors(source);
  applyPhase3dSameColorMergeStage({
    corridorFeatures: first,
    sameColorMergesGeoJsonPath: join(reportDir, "a.geojson"),
  });
  applyPhase3dSameColorMergeStage({
    corridorFeatures: second,
    sameColorMergesGeoJsonPath: join(reportDir, "b.geojson"),
  });
  assert.equal(first[0].properties.color, "#EE352E");
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.equal(readFileSync(join(reportDir, "a.geojson"), "utf8"), readFileSync(join(reportDir, "b.geojson"), "utf8"));
});

test("phase 3d skips a merge that would drop a route's only corridor", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const features = [
    corridor("trunk-conn", ["1"], makePolylineNS(-73.99, 40.7, 1500, 8), { color: "#EE352E" }),
    corridor("branch-conn", ["2"], makePolylineNS(-73.99, 40.7, 400, 4), { color: "#EE352E" }),
  ];
  applyPhase3dSameColorMergeStage({
    corridorFeatures: features,
    sameColorMergesGeoJsonPath: join(reportDir, "skip.geojson"),
  });
  assert.deepEqual(
    features.map((feature) => feature.properties.corridor_id),
    ["trunk-conn", "branch-conn"],
  );
  const written = JSON.parse(readFileSync(join(reportDir, "skip.geojson"), "utf8"));
  assert.equal(written.features[0].properties.visual_feature_type, "same_color_merge_skipped");
  assert.equal(written.features[0].properties.reason, "would_break_route_connectivity");
});

test("phase 3d on empty corridors writes an empty feature collection", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const features: LineFeature[] = [];
  applyPhase3dSameColorMergeStage({
    corridorFeatures: features,
    sameColorMergesGeoJsonPath: join(reportDir, "empty.geojson"),
  });
  assert.deepEqual(features, []);
  const written = JSON.parse(readFileSync(join(reportDir, "empty.geojson"), "utf8"));
  assert.equal(written.type, "FeatureCollection");
  assert.deepEqual(written.features, []);
});

test("phase 3d drops a fully contained branch when another corridor still carries the route", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const features = [
    corridor("trunk-drop", ["1"], makePolylineNS(-73.99, 40.7, 1500, 8), { color: "#EE352E" }),
    corridor("branch-drop", ["2"], makePolylineNS(-73.99, 40.7, 400, 4), { color: "#EE352E" }),
    corridor("decoy-2", ["2"], makePolylineNS(-73.95, 40.65, 500, 5), { color: "#EE352E" }),
  ];
  applyPhase3dSameColorMergeStage({
    corridorFeatures: features,
    sameColorMergesGeoJsonPath: join(reportDir, "drop.geojson"),
  });
  assert.equal(
    features.some((feature) => feature.properties.corridor_id === "branch-drop"),
    false,
    "contained branch should be removed once a decoy still carries route 2",
  );
  assert.ok(features.some((feature) => feature.properties.corridor_id === "decoy-2"));
  const written = JSON.parse(readFileSync(join(reportDir, "drop.geojson"), "utf8"));
  const dropped = written.features.find(
    (row: { properties?: { visual_feature_type?: string } }) =>
      row.properties?.visual_feature_type === "same_color_merge",
  );
  assert.ok(dropped);
  assert.deepEqual(dropped.properties.branches_dropped, ["branch-drop"]);
});

test("phase 3d still merges a corridor that omits corridor_id", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const unlabeled = corridor("will-delete", ["1"], makePolylineNS(-73.99, 40.7, 1500, 8), { color: "#EE352E" });
  delete unlabeled.properties.corridor_id;
  const features = [
    unlabeled,
    corridor("branch-1", ["2"], divergingBranchCoords(), { color: "#EE352E" }),
  ];
  applyPhase3dSameColorMergeStage({
    corridorFeatures: features,
    sameColorMergesGeoJsonPath: join(reportDir, "noid.geojson"),
  });
  assert.equal(features[0].properties.color, "#EE352E");
  const written = JSON.parse(readFileSync(join(reportDir, "noid.geojson"), "utf8"));
  assert.ok(Array.isArray(written.features));
});

test("phase 3d stamps gray when a corridor has no color and no route id", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const features: LineFeature[] = [
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: makePolylineNS(-73.99, 40.7, 120, 2) },
      properties: { corridor_id: "gray-1" },
    },
  ];
  applyPhase3dSameColorMergeStage({
    corridorFeatures: features,
    sameColorMergesGeoJsonPath: join(reportDir, "gray.geojson"),
  });
  assert.equal(features[0].properties.color, "#808183");
});

test("phase 3d stamps gray when route_ids is missing and keeps an unlabeled drop companion", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3d-"));
  const features: LineFeature[] = [
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: makePolylineNS(-73.99, 40.7, 120, 2) },
      properties: { corridor_id: "gray-2" },
    },
    corridor("trunk-keep", ["1"], makePolylineNS(-73.98, 40.7, 1500, 8), { color: "#EE352E" }),
    corridor("branch-keep", ["2"], divergingBranchCoords(), { color: "#EE352E" }),
  ];
  delete features[0].properties.route_ids;
  applyPhase3dSameColorMergeStage({
    corridorFeatures: features,
    sameColorMergesGeoJsonPath: join(reportDir, "gray2.geojson"),
  });
  assert.equal(features[0].properties.color, "#808183");
  assert.ok(features.some((feature) => feature.properties.corridor_id === "trunk-keep"));
});
