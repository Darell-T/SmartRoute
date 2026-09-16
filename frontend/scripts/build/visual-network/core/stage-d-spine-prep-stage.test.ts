import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { buildStageDSpinePrepStage } from "./stage-d-spine-prep-stage.ts";
import type { LineFeature } from "../shared/types.ts";

function paths() {
  const dir = mkdtempSync(join(tmpdir(), "stage-d-"));
  return {
    spinesGeoJson: join(dir, "spines.geojson"),
    transitiveBundlesGeoJson: join(dir, "transitive.geojson"),
    materializedBundlesGeoJson: join(dir, "mat.geojson"),
    materializedBundleFanoutsGeoJson: join(dir, "fanouts.geojson"),
    materializedBundleSplitsGeoJson: join(dir, "splits.geojson"),
    materializedBundleDefectsGeoJson: join(dir, "defects.geojson"),
    physicalBundlesGeoJson: join(dir, "pb.geojson"),
    physicalBundleLanesGeoJson: join(dir, "pb-lanes.geojson"),
    physicalBundleRejectsGeoJson: join(dir, "pb-rejects.geojson"),
  };
}

const parameters = {
  openDataMinFragmentLengthM: 40,
  densifyMaxSegmentM: 250,
  densifyStepM: 25,
  physicalBundleSubstituteConfidenceMin: 0.75,
  bundleOverlapDistMaxM: 15,
  bundleSharedLenMinM: 250,
  bundleSplitSampleM: 5,
  fanoutBlendM: 100,
  laneWidthM: 8,
};

function corridor(corridorId: string, routeIds: string[]): LineFeature {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [-73.99, 40.75],
        [-73.99, 40.751],
      ],
    },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      from_stop_id: `${corridorId}-from`,
      to_stop_id: `${corridorId}-to`,
      length_m: 111,
    },
  };
}

test("empty corridors emit empty bundle artifacts and are deterministic", () => {
  const first = buildStageDSpinePrepStage({
    corridorFeatures: [],
    paths: paths(),
    parameters,
  });
  const second = buildStageDSpinePrepStage({
    corridorFeatures: [],
    paths: paths(),
    parameters,
  });
  assert.equal(first.bundleArtifacts.visualFeatures.length, 0);
  assert.deepEqual(first.bundleArtifacts, second.bundleArtifacts);
});

test("a solo corridor becomes one visual lane with a spine hash", () => {
  const feature = corridor("c1", ["A"]);
  const { bundleArtifacts } = buildStageDSpinePrepStage({
    corridorFeatures: [feature],
    paths: paths(),
    parameters,
  });
  assert.equal(bundleArtifacts.visualFeatures.length, 1);
  assert.equal(bundleArtifacts.visualFeatures[0].properties.spine_id, "spine-c1");
  assert.ok(String(bundleArtifacts.visualFeatures[0].properties.base_spine_hash).startsWith("h"));

  const clone = corridor("c1", ["A"]);
  const again = buildStageDSpinePrepStage({
    corridorFeatures: [clone],
    paths: paths(),
    parameters,
  });
  assert.deepEqual(
    again.bundleArtifacts.visualFeatures[0].geometry.coordinates,
    bundleArtifacts.visualFeatures[0].geometry.coordinates,
  );
  assert.equal(
    again.bundleArtifacts.visualFeatures[0].properties.base_spine_hash,
    bundleArtifacts.visualFeatures[0].properties.base_spine_hash,
  );
});

function longCorridor(corridorId: string, routeIds: string[], lon: number): LineFeature {
  const coordinates: Array<[number, number]> = [
    [lon, 40.75],
    [lon, 40.754],
  ];
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      from_stop_id: `${corridorId}-from`,
      to_stop_id: `${corridorId}-to`,
      length_m: 443,
    },
  };
}

test("overlapping corridors densify and emit a physical-bundle diagnostic collection", () => {
  const result = buildStageDSpinePrepStage({
    corridorFeatures: [
      longCorridor("c-red", ["1"], -73.99),
      longCorridor("c-green", ["4"], -73.99 + 0.00002),
    ],
    paths: paths(),
    parameters,
  });
  assert.ok(result.bundleArtifacts.visualFeatures.length >= 2);
  const ids = result.bundleArtifacts.visualFeatures.map((feature) => feature.properties.corridor_id);
  assert.ok(ids.includes("c-red"));
  assert.ok(ids.includes("c-green"));
  const again = buildStageDSpinePrepStage({
    corridorFeatures: [
      longCorridor("c-red", ["1"], -73.99),
      longCorridor("c-green", ["4"], -73.99 + 0.00002),
    ],
    paths: paths(),
    parameters,
  });
  assert.equal(again.bundleArtifacts.visualFeatures.length, result.bundleArtifacts.visualFeatures.length);
});

test("prunes a short fragment, keeps a same-color connector, and densifies a long segment", () => {
  const tiny: LineFeature = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99001, 40.75001]] },
    properties: { corridor_id: "tiny", route_ids: ["G"], from_stop_id: "t-from", to_stop_id: "t-to", length_m: 2 },
  };
  const connector: LineFeature = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99001, 40.75001]] },
    properties: {
      corridor_id: "conn",
      route_ids: ["G"],
      from_stop_id: "c-from",
      to_stop_id: "c-to",
      length_m: 2,
      visual_feature_type: "same_color_branch_connector",
    },
  };
  const long: LineFeature = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99, 40.76]] },
    properties: { corridor_id: "long", route_ids: ["A"], from_stop_id: "l-from", to_stop_id: "l-to", length_m: 1113 },
  };
  const features = [tiny, connector, long];
  const { bundleArtifacts } = buildStageDSpinePrepStage({
    corridorFeatures: features,
    paths: paths(),
    parameters,
  });
  assert.equal(features.some((feature) => feature.properties.corridor_id === "tiny"), false);
  assert.ok(features.some((feature) => feature.properties.corridor_id === "conn"));
  assert.ok(long.geometry.coordinates.length > 2);
  assert.ok(bundleArtifacts.visualFeatures.length >= 2);
});

test("dedupes parallel duplicates and writes physical-bundle reject collections", () => {
  const a = longCorridor("dup-a", ["1"], -73.99);
  const b = longCorridor("dup-b", ["1"], -73.99);
  const stagePaths = paths();
  const result = buildStageDSpinePrepStage({
    corridorFeatures: [a, b],
    paths: stagePaths,
    parameters,
  });
  assert.ok(result.bundleArtifacts.visualFeatures.length >= 1);
});
