import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { runValidationReportingStage } from "./validation-reporting-stage.ts";
import type { BundleArtifacts, LineFeature, PointFeat } from "../shared/types.ts";
import type { ValidationReportingPaths } from "./validation-reporting-stage.ts";

function line(
  id: string,
  routeId: string,
  fromStop: string,
  toStop: string,
  coordinates: Array<[number, number]>,
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: {
      corridor_id: id,
      edge_id: id,
      route_id: routeId,
      route_ids: [routeId],
      from_stop_id: fromStop,
      to_stop_id: toStop,
      from_stop_name: fromStop,
      to_stop_name: toStop,
      source_edge_ids: [],
    },
  };
}

function emptyBundles(): BundleArtifacts {
  return {
    bundleFeatures: [],
    bundleLaneFeatures: [],
    bundleGapFeatures: [],
    visualFeatures: [],
  };
}

function stubPoint(): PointFeat {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [-73.99, 40.75] },
    properties: {},
  };
}

function pathsFor(dir: string): ValidationReportingPaths {
  return {
    corridorsGeoJson: path.join(dir, "corridors.geojson"),
    corridorsJson: path.join(dir, "corridors.json"),
    junctionAnchorsGeoJson: path.join(dir, "junction-anchors.geojson"),
    junctionSnapsGeoJson: path.join(dir, "junction-snaps.geojson"),
    materializedBundlesGeoJson: path.join(dir, "materialized-bundles.geojson"),
    materializedBundleFanoutsGeoJson: path.join(dir, "materialized-fanouts.geojson"),
    materializedBundleSplitsGeoJson: path.join(dir, "materialized-splits.geojson"),
    materializedBundleDefectsGeoJson: path.join(dir, "materialized-defects.geojson"),
    bundlesGeoJson: path.join(dir, "bundles.geojson"),
    bundleLanesGeoJson: path.join(dir, "bundle-lanes.geojson"),
    bundleGapsGeoJson: path.join(dir, "bundle-gaps.geojson"),
    missingRouteLanesGeoJson: path.join(dir, "missing-route-lanes.geojson"),
    renderLaneContinuityJson: path.join(dir, "render-lane-continuity.json"),
    anomaliesGeoJson: path.join(dir, "anomalies.geojson"),
    anomaliesJson: path.join(dir, "anomalies.json"),
    routeComponentsJson: path.join(dir, "route-components.json"),
  };
}

const parameters = {
  resampleIntervalM: 25,
  hausdorffMaxM: 15,
  overlapMinRatio: 0.6,
  tangentMaxDiffDeg: 30,
  containmentAvgDistanceMaxM: 15,
  containmentOverlapMinRatio: 0.85,
  gridCellM: 250,
  junctionSnapMaxM: 25,
  maxSegmentAnomalyM: 250,
  sparseLongSliceM: 600,
  projectionAnomalyM: 125,
  openDataMinFragmentLengthM: 0,
};

test("runValidationReportingStage writes camelCase bundle lanes and connected-route stats", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "vn-validation-"));
  const edgeA = line("e1", "G", "G01", "G02", [
    [-73.99, 40.75],
    [-73.991, 40.751],
    [-73.992, 40.752],
  ]);
  const edgeB = line("e2", "G", "G02", "G03", [
    [-73.992, 40.752],
    [-73.993, 40.753],
    [-73.994, 40.754],
  ]);
  const result = runValidationReportingStage({
    edgeFeatures: [edgeA, edgeB],
    corridorFeatures: [edgeA, edgeB],
    corridorRows: [
      { is_shared: false, route_ids: ["G"] },
      { is_shared: false, route_ids: ["G"] },
    ],
    pairsConsidered: 0,
    pairsMatched: 0,
    matchedPairs: [],
    junctionSnapDiagnostics: { anchorFeatures: [], snapFeatures: [] },
    laneChainDiagnostics: { lane_group_count: 0, chain_slot_feature_count: 0 },
    bundleArtifacts: emptyBundles(),
    edgeById: new Map([
      ["e1", edgeA],
      ["e2", edgeB],
    ]),
    stopsById: new Map(),
    paths: pathsFor(dir),
    parameters,
  });

  assert.equal(result.perRouteStats.length, 1);
  assert.equal(result.perRouteStats[0].route_id, "G");
  assert.equal(result.perRouteStats[0].passed, true);
  assert.deepEqual(result.validationFailures, []);

  const corridors = JSON.parse(readFileSync(path.join(dir, "corridors.geojson"), "utf8"));
  assert.equal(corridors.type, "FeatureCollection");
  assert.equal(corridors.features[0].properties.corridor_id, "e1");
  const bundles = JSON.parse(readFileSync(path.join(dir, "bundles.geojson"), "utf8"));
  assert.equal(bundles.metadata.summary.remaining_unbundled_corridors, 0);
  const components = JSON.parse(readFileSync(path.join(dir, "route-components.json"), "utf8"));
  assert.equal(components.summary.routes_failed, 0);

  const second = runValidationReportingStage({
    edgeFeatures: [edgeA, edgeB],
    corridorFeatures: [edgeA, edgeB],
    corridorRows: [
      { is_shared: false, route_ids: ["G"] },
      { is_shared: false, route_ids: ["G"] },
    ],
    pairsConsidered: 0,
    pairsMatched: 0,
    matchedPairs: [],
    junctionSnapDiagnostics: { anchorFeatures: [], snapFeatures: [] },
    laneChainDiagnostics: { lane_group_count: 0, chain_slot_feature_count: 0 },
    bundleArtifacts: emptyBundles(),
    edgeById: new Map([
      ["e1", edgeA],
      ["e2", edgeB],
    ]),
    stopsById: new Map(),
    paths: pathsFor(dir),
    parameters,
  });
  assert.deepEqual(second.perRouteStats[0].component_count, result.perRouteStats[0].component_count);
  rmSync(dir, { recursive: true, force: true });
});

test("runValidationReportingStage records a disconnected route as a failure without overwriting useful diagnostics", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "vn-validation-fail-"));
  const left = line("e1", "7", "701", "702", [
    [-73.99, 40.75],
    [-73.9901, 40.7501],
    [-73.9902, 40.7502],
  ]);
  const right = line("e2", "7", "799", "800", [
    [-73.8, 40.8],
    [-73.8001, 40.8001],
    [-73.8002, 40.8002],
  ]);
  const result = runValidationReportingStage({
    edgeFeatures: [left, right],
    corridorFeatures: [left, right],
    corridorRows: [{ is_shared: false, route_ids: ["7"] }],
    pairsConsidered: 0,
    pairsMatched: 0,
    matchedPairs: [],
    junctionSnapDiagnostics: { anchorFeatures: [], snapFeatures: [] },
    laneChainDiagnostics: { lane_group_count: 0, chain_slot_feature_count: 0 },
    bundleArtifacts: emptyBundles(),
    edgeById: new Map([
      ["e1", left],
      ["e2", right],
    ]),
    stopsById: new Map(),
    paths: pathsFor(dir),
    parameters,
  });
  assert.equal(result.validationFailures.length, 1);
  assert.equal(result.validationFailures[0].route_id, "7");
  assert.equal(result.validationFailures[0].component_count, 2);
  const doc = JSON.parse(readFileSync(path.join(dir, "route-components.json"), "utf8"));
  assert.equal(doc.summary.routes_failed, 1);
  rmSync(dir, { recursive: true, force: true });
});

test("runValidationReportingStage writes a missing Q Prospect lane and shared-corridor counts", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "vn-validation-missing-"));
  const qCoords: Array<[number, number]> = [];
  for (let i = 0; i <= 40; i += 1) {
    qCoords.push([-73.99 + i * 0.0005, 40.65]);
  }
  const edgeA = line("e1", "Q", "Q01", "Q02", qCoords.slice(0, 21));
  edgeA.properties.from_stop_name = "Seventh Av";
  edgeA.properties.to_stop_name = "Prospect Park";
  const edgeB = line("e2", "Q", "Q02", "Q03", qCoords.slice(20));
  edgeB.properties.from_stop_name = "Prospect Park";
  edgeB.properties.to_stop_name = "Parkside Av";
  const corridor = line("c-q", "Q", "Q01", "Q03", qCoords);
  corridor.properties.route_ids = ["Q", "B"];
  const result = runValidationReportingStage({
    edgeFeatures: [edgeA, edgeB],
    corridorFeatures: [corridor],
    corridorRows: [{ is_shared: true, route_ids: ["Q", "B"] }],
    pairsConsidered: 1,
    pairsMatched: 0,
    matchedPairs: [],
    junctionSnapDiagnostics: { anchorFeatures: [], snapFeatures: [] },
    laneChainDiagnostics: { lane_group_count: 0, chain_slot_feature_count: 0 },
    bundleArtifacts: emptyBundles(),
    edgeById: new Map([
      ["e1", edgeA],
      ["e2", edgeB],
    ]),
    stopsById: new Map([
      ["Q02", { stop_id: "Q02", name: "Prospect Park", lat: 40.65, lon: -73.98 }],
    ]),
    paths: pathsFor(dir),
    parameters,
  });
  assert.equal(result.validationFailures.length, 0);
  const missing = JSON.parse(readFileSync(path.join(dir, "missing-route-lanes.geojson"), "utf8"));
  assert.ok(missing.features.some((feature: { properties: { stop_id: string } }) => feature.properties.stop_id === "Q02"));
  const continuity = JSON.parse(readFileSync(path.join(dir, "render-lane-continuity.json"), "utf8"));
  assert.ok(continuity.summary.q_prospect_brighton_missing_count >= 1);
  const anomalies = JSON.parse(readFileSync(path.join(dir, "anomalies.json"), "utf8"));
  assert.equal(anomalies.summary.shared_corridor_count, 1);
  rmSync(dir, { recursive: true, force: true });
});

test("runValidationReportingStage exits 1 on a sparse long visual defect", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "vn-validation-hard-"));
  const sparse = line("sparse", "2", "201", "247", [
    [-73.99, 40.7],
    [-73.95, 40.8],
  ]);
  const previousExit = process.exit;
  // SAFETY: the stage calls process.exit(1) on a hard visual defect.
  process.exit = ((code?: number) => {
    throw new Error(`exit ${code}`);
  }) as typeof process.exit;
  try {
    assert.throws(
      () =>
        runValidationReportingStage({
          edgeFeatures: [sparse],
          corridorFeatures: [sparse],
          corridorRows: [{ is_shared: false, route_ids: ["2"] }],
          pairsConsidered: 0,
          pairsMatched: 0,
          matchedPairs: [],
          junctionSnapDiagnostics: { anchorFeatures: [], snapFeatures: [] },
          laneChainDiagnostics: { lane_group_count: 0, chain_slot_feature_count: 0 },
          bundleArtifacts: emptyBundles(),
          edgeById: new Map(),
          stopsById: new Map(),
          paths: pathsFor(dir),
          parameters,
        }),
      /exit 1/,
    );
  } finally {
    process.exit = previousExit;
    rmSync(dir, { recursive: true, force: true });
  }
});

test("runValidationReportingStage keeps same_color_branch_connector fragments and writes anomaly shape ids", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "vn-validation-connector-"));
  const connector = line("conn", "G", "G01", "G02", [
    [-73.99, 40.75],
    [-73.99001, 40.75001],
  ]);
  connector.properties.visual_feature_type = "same_color_branch_connector";
  const edge = line("e1", "G", "G01", "G02", [
    [-73.99, 40.75],
    [-73.9903, 40.7503],
    [-73.9906, 40.7506],
    [-73.9909, 40.7509],
  ]);
  edge.properties["shape_id"] = "gtfs-g";
  edge.properties.from_projection_dist_m = 200;
  const corridor = line("c-g", "G", "G01", "G02", [
    [-73.99, 40.75],
    [-73.9903, 40.7503],
    [-73.9906, 40.7506],
    [-73.9909, 40.7509],
  ]);
  corridor.properties.source_edge_ids = ["e1"];
  const result = runValidationReportingStage({
    edgeFeatures: [edge],
    corridorFeatures: [connector, corridor],
    corridorRows: [{ is_shared: false, route_ids: ["G"] }],
    pairsConsidered: 0,
    pairsMatched: 0,
    matchedPairs: [],
    junctionSnapDiagnostics: { anchorFeatures: [stubPoint()], snapFeatures: [] },
    laneChainDiagnostics: { lane_group_count: 1, chain_slot_feature_count: 2 },
    bundleArtifacts: {
      bundleFeatures: [corridor],
      bundleLaneFeatures: [corridor],
      bundleGapFeatures: [],
      visualFeatures: [corridor],
    },
    edgeById: new Map([["e1", edge]]),
    stopsById: new Map(),
    paths: pathsFor(dir),
    parameters: { ...parameters, openDataMinFragmentLengthM: 40 },
  });
  assert.equal(result.perRouteStats[0].passed, true);
  const anomalies = JSON.parse(readFileSync(path.join(dir, "anomalies.geojson"), "utf8"));
  assert.ok(anomalies.features.some((feature: { properties: { corridor_id: string } }) => feature.properties.corridor_id === "c-g"));
  rmSync(dir, { recursive: true, force: true });
});
