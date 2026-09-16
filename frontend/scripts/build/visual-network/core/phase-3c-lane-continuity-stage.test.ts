import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { applyPhase3cLaneContinuityStage } from "./phase-3c-lane-continuity-stage.ts";
import { buildBundleArtifacts } from "./bundle-stage.ts";
import type { LineFeature } from "../shared/types.ts";

function corridor(corridorId: string, routeIds: string[]): LineFeature {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [-73.99, 40.75],
        [-73.99, 40.76],
      ],
    },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      from_stop_id: `${corridorId}-from`,
      to_stop_id: `${corridorId}-to`,
      length_m: 1113,
    },
  };
}

test("phase 3c on terminal-anchored solo lanes keeps bundle_lane identity and is deterministic", () => {
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3c-"));
  const stopsById = new Map([
    ["c-solo-from", { stop_id: "c-solo-from", name: "South", lat: 40.75, lon: -73.99, parent_station: null, location_type: 0 }],
    ["c-solo-to", { stop_id: "c-solo-to", name: "North", lat: 40.76, lon: -73.99, parent_station: null, location_type: 0 }],
  ]);
  const first = buildBundleArtifacts([corridor("c-solo", ["1"])], new Map());
  const second = buildBundleArtifacts([corridor("c-solo", ["1"])], new Map());
  applyPhase3cLaneContinuityStage({
    bundleArtifacts: first,
    stopsById,
    branchTransitionsGeoJsonPath: join(reportDir, "a.geojson"),
    branchTransitionMaxM: 35,
  });
  applyPhase3cLaneContinuityStage({
    bundleArtifacts: second,
    stopsById,
    branchTransitionsGeoJsonPath: join(reportDir, "b.geojson"),
    branchTransitionMaxM: 35,
  });
  assert.equal(first.bundleLaneFeatures.length, 1);
  assert.equal(first.bundleLaneFeatures[0].properties.visual_feature_type, "bundle_lane");
  assert.equal(first.bundleLaneFeatures[0].properties.route_id, "1");
  assert.equal(JSON.stringify(first.visualFeatures), JSON.stringify(second.visualFeatures));
});

function anchoredLane(
  bundleId: string,
  routeIds: string[],
  fromAnchor: string,
  toAnchor: string,
  coordinates: Array<[number, number]>,
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: {
      visual_feature_type: "bundle_lane",
      bundle_id: bundleId,
      corridor_id: bundleId,
      route_ids: routeIds,
      route_id: routeIds[0],
      color: "#EE352E",
      from_anchor_id: fromAnchor,
      to_anchor_id: toAnchor,
      from_stop_id: fromAnchor,
      to_stop_id: toAnchor,
    },
  };
}

test("phase 3c promotes a same-color continuation and warns on a disconnected Brooklyn Q", () => {
  const south = anchoredLane("b-south", ["1"], "A", "B", [
    [-73.99, 40.70],
    [-73.985, 40.70],
  ]);
  const north = anchoredLane("b-north", ["1"], "B", "C", [
    [-73.9849, 40.7001],
    [-73.98, 40.70],
  ]);
  const qWest = anchoredLane("q-west", ["Q"], "QW", "QX", [
    [-73.99, 40.64],
    [-73.97, 40.64],
  ]);
  const qEast = anchoredLane("q-east", ["Q"], "QY", "QZ", [
    [-73.90, 40.64],
    [-73.88, 40.64],
  ]);
  const artifacts = {
    bundleFeatures: [],
    bundleLaneFeatures: [south, north, qWest, qEast],
    bundleGapFeatures: [],
    visualFeatures: [south, north, qWest, qEast],
  };
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3c-trans-"));
  const stopsById = new Map([
    ["A", { stop_id: "A", name: "A", lat: 40.70, lon: -73.99, parent_station: null, location_type: 0 }],
    ["B", { stop_id: "B", name: "B", lat: 40.70, lon: -73.985, parent_station: null, location_type: 0 }],
    ["C", { stop_id: "C", name: "C", lat: 40.70, lon: -73.98, parent_station: null, location_type: 0 }],
  ]);
  applyPhase3cLaneContinuityStage({
    bundleArtifacts: artifacts,
    stopsById,
    branchTransitionsGeoJsonPath: join(reportDir, "transitions.geojson"),
    branchTransitionMaxM: 35,
  });
  const transitions = artifacts.bundleLaneFeatures.filter(
    (feature) => feature.properties.feature_type === "branch_transition",
  );
  assert.equal(transitions.length, 1);
  assert.equal(transitions[0].properties.transition_classification, "safe_same_route_continuation");
  assert.equal(transitions[0].properties.route_id, "1");
  const clone = {
    bundleFeatures: [],
    bundleLaneFeatures: [
      anchoredLane("b-south", ["1"], "A", "B", [[-73.99, 40.70], [-73.985, 40.70]]),
      anchoredLane("b-north", ["1"], "B", "C", [[-73.9849, 40.7001], [-73.98, 40.70]]),
    ],
    bundleGapFeatures: [],
    visualFeatures: [
      anchoredLane("b-south", ["1"], "A", "B", [[-73.99, 40.70], [-73.985, 40.70]]),
      anchoredLane("b-north", ["1"], "B", "C", [[-73.9849, 40.7001], [-73.98, 40.70]]),
    ],
  };
  applyPhase3cLaneContinuityStage({
    bundleArtifacts: clone,
    stopsById,
    branchTransitionsGeoJsonPath: join(reportDir, "transitions-b.geojson"),
    branchTransitionMaxM: 35,
  });
  assert.equal(
    clone.bundleLaneFeatures.filter((feature) => feature.properties.feature_type === "branch_transition").length,
    1,
  );
});

test("phase 3c sorts multiple transitions, drops a bogus one, and warns on Flatbush origins", () => {
  const south = anchoredLane("b-south", ["1"], "A", "B", [
    [-73.99, 40.70],
    [-73.985, 40.70],
  ]);
  const north = anchoredLane("b-north", ["1"], "B", "C", [
    [-73.9849, 40.7001],
    [-73.98, 40.70],
  ]);
  const east = anchoredLane("b-east", ["1"], "C", "D", [
    [-73.9799, 40.7001],
    [-73.97, 40.70],
  ]);
  const bogus = anchoredLane("bogus-t", ["Q"], "X", "Y", [
    [-73.90, 40.64],
    [-73.89, 40.64],
  ]);
  bogus.properties.lane_slot_source = "branch_transition";
  bogus.properties.bundle_id_from = "b-south";
  bogus.properties.bundle_id_to = "b-north";
  bogus.properties.color_route_ids = ["Q"];
  bogus.properties.color = "#FCCC0A";
  bogus.properties.transition_classification = "likely_branch_exit";
  bogus.properties.length_m = 40;
  const irtInside = anchoredLane("irt-isolated", ["2", "3"], "fe-z", "fe-w", [
    [-73.960, 40.661],
    [-73.955, 40.665],
  ]);
  const artifacts = {
    bundleFeatures: [],
    bundleLaneFeatures: [south, north, east, bogus, irtInside],
    bundleGapFeatures: [],
    visualFeatures: [south, north, east, bogus, irtInside],
  };
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3c-bogus-"));
  const stopsById = new Map([
    ["A", { stop_id: "A", name: "A", lat: 40.70, lon: -73.99, parent_station: null, location_type: 0 }],
    ["B", { stop_id: "B", name: "B", lat: 40.70, lon: -73.985, parent_station: null, location_type: 0 }],
    ["C", { stop_id: "C", name: "C", lat: 40.70, lon: -73.98, parent_station: null, location_type: 0 }],
    ["D", { stop_id: "D", name: "D", lat: 40.70, lon: -73.97, parent_station: null, location_type: 0 }],
  ]);
  applyPhase3cLaneContinuityStage({
    bundleArtifacts: artifacts,
    stopsById,
    branchTransitionsGeoJsonPath: join(reportDir, "transitions.geojson"),
    branchTransitionMaxM: 35,
  });
  assert.equal(artifacts.bundleLaneFeatures.some((feature) => feature.properties.bundle_id === "bogus-t"), false);
  const transitions = artifacts.bundleLaneFeatures.filter(
    (feature) => feature.properties.feature_type === "branch_transition",
  );
  assert.ok(transitions.length >= 2);
});

test("phase 3c drops a leftover bogus transition instead of leaving it for gate D2", () => {
  const south = anchoredLane("b-south", ["1"], "A", "B", [
    [-73.99, 40.70],
    [-73.985, 40.70],
  ]);
  const leftover = anchoredLane("leftover", ["Q"], "A", "B", [
    [-73.99, 40.70],
    [-73.985, 40.70],
  ]);
  leftover.properties.lane_slot_source = "branch_transition";
  leftover.properties.bundle_id_from = "missing-from";
  leftover.properties.bundle_id_to = "missing-to";
  leftover.properties.color_route_ids = ["Q"];
  leftover.properties.color = "#FCCC0A";
  leftover.properties.transition_classification = "safe_same_route_continuation";
  leftover.properties.length_m = 5;
  const artifacts = {
    bundleFeatures: [],
    bundleLaneFeatures: [south, leftover],
    bundleGapFeatures: [],
    visualFeatures: [south, leftover],
  };
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3c-fail-"));
  applyPhase3cLaneContinuityStage({
    bundleArtifacts: artifacts,
    stopsById: new Map(),
    branchTransitionsGeoJsonPath: join(reportDir, "transitions.geojson"),
    branchTransitionMaxM: 35,
  });
  assert.equal(artifacts.bundleLaneFeatures.some((feature) => feature.properties.bundle_id === "leftover"), false);
  assert.equal(artifacts.visualFeatures.some((feature) => feature.properties.bundle_id === "leftover"), false);
});

test("phase 3c promotes a likely branch exit and skips a coincident same-color pair", () => {
  const south = anchoredLane("b-south", ["1"], "A", "B", [
    [-73.99, 40.70],
    [-73.985, 40.70],
  ]);
  const north = anchoredLane("b-north", ["2"], "B", "C", [
    [-73.9847, 40.7002],
    [-73.98, 40.70],
  ]);
  const unlabeled = anchoredLane("no-bundle", ["1"], "A", "B", [
    [-73.99, 40.70],
    [-73.985, 40.70],
  ]);
  delete unlabeled.properties.bundle_id;
  delete unlabeled.properties.route_ids;
  const touchA = anchoredLane("touch-a", ["1"], "T1", "T2", [
    [-73.97, 40.71],
    [-73.969, 40.71],
  ]);
  const touchB = anchoredLane("touch-b", ["1"], "T2", "T3", [
    [-73.969, 40.71],
    [-73.968, 40.71],
  ]);
  const artifacts = {
    bundleFeatures: [],
    bundleLaneFeatures: [south, north, unlabeled, touchA, touchB],
    bundleGapFeatures: [],
    visualFeatures: [south, north, unlabeled, touchA, touchB],
  };
  const reportDir = mkdtempSync(join(tmpdir(), "phase-3c-exit-"));
  const transitionPath = join(reportDir, "transitions.geojson");
  applyPhase3cLaneContinuityStage({
    bundleArtifacts: artifacts,
    stopsById: new Map(),
    branchTransitionsGeoJsonPath: transitionPath,
    branchTransitionMaxM: 35,
  });
  const written = JSON.parse(readFileSync(transitionPath, "utf8"));
  assert.ok(
    written.features.some(
      (feature: { properties?: { transition_classification?: string } }) =>
        feature.properties?.transition_classification === "likely_branch_exit",
    ),
  );
  assert.equal(unlabeled.properties.bundle_id, undefined);
});
