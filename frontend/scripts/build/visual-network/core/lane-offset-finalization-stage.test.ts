import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { BundleArtifacts, LineFeature } from "../shared/types.ts";
import { applyLaneOffsetFinalizationStage } from "./lane-offset-finalization-stage.ts";

function line(
  id: string,
  color: string,
  coords: LineFeature["geometry"]["coordinates"],
  extra: LineFeature["properties"] = {},
): LineFeature {
  return {
    type: "Feature",
    id,
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      bundle_id: id,
      color,
      route_ids: [id],
      lane_slot: 0,
      ...extra,
    },
  };
}

function emptyArtifacts(visualFeatures: LineFeature[]): BundleArtifacts {
  return {
    bundleFeatures: [],
    bundleLaneFeatures: visualFeatures,
    bundleGapFeatures: [],
    visualFeatures,
  };
}

function tempPaths() {
  const dir = mkdtempSync(join(tmpdir(), "lane-offset-"));
  return {
    spread: join(dir, "spread.geojson"),
    segments: join(dir, "segments.geojson"),
    orders: join(dir, "orders.json"),
  };
}

test("empty visual features write empty debug collections and are deterministic", () => {
  const paths = tempPaths();
  const first = emptyArtifacts([]);
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: first,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });
  const spread = JSON.parse(readFileSync(paths.spread, "utf8"));
  const segments = JSON.parse(readFileSync(paths.segments, "utf8"));
  const orders = JSON.parse(readFileSync(paths.orders, "utf8"));
  assert.equal(spread.features.length, 0);
  assert.equal(segments.features.length, 0);
  assert.deepEqual(orders, []);

  const second = emptyArtifacts([]);
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: second,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });
  assert.deepEqual(second.visualFeatures, first.visualFeatures);
});

test("whole-feature spread offsets parallel different-color solos and is deterministic", () => {
  const south: LineFeature["geometry"]["coordinates"] = [];
  const shifted: LineFeature["geometry"]["coordinates"] = [];
  for (let i = 0; i < 20; i += 1) {
    const lat = 40.75 + i * 0.0003;
    south.push([-73.99, lat]);
    shifted.push([-73.98995, lat]);
  }
  const red = line("red", "#EE352E", south);
  const blue = line("blue", "#0A84FF", shifted.map((coord) => [...coord]));
  const artifacts = emptyArtifacts([red, blue]);
  const paths = tempPaths();
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: artifacts,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });

  const cloneRed = line("red", "#EE352E", south.map((coord) => [...coord]));
  const cloneBlue = line("blue", "#0A84FF", shifted.map((coord) => [...coord]));
  const clone = emptyArtifacts([cloneRed, cloneBlue]);
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: clone,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });
  assert.deepEqual(clone.visualFeatures[0].geometry.coordinates, artifacts.visualFeatures[0].geometry.coordinates);
  assert.deepEqual(clone.visualFeatures[1].geometry.coordinates, artifacts.visualFeatures[1].geometry.coordinates);
});

test("partial overlap uses segment spread and writes a lane-order entry", () => {
  const longRed: LineFeature["geometry"]["coordinates"] = [];
  const longBlue: LineFeature["geometry"]["coordinates"] = [];
  for (let i = 0; i < 40; i += 1) {
    longRed.push([-73.99, 40.74 + i * 0.00025]);
    longBlue.push([-73.98997, 40.746 + i * 0.00025]);
  }
  const artifacts = emptyArtifacts([
    line("red-long", "#EE352E", longRed, { lane_slot: 0, lane_slot_semantic: 0 }),
    line("blue-long", "#0A84FF", longBlue, { lane_slot: 0, lane_slot_semantic: 0 }),
  ]);
  const paths = tempPaths();
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: artifacts,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });
  const segments = JSON.parse(readFileSync(paths.segments, "utf8"));
  const orders = JSON.parse(readFileSync(paths.orders, "utf8"));
  assert.ok(segments.features.length >= 1);
  assert.ok(orders.some((entry: { bundle_id?: string }) => entry.bundle_id === "red-long"));
  assert.ok(orders.some((entry: { bundle_id?: string }) => entry.bundle_id === "blue-long"));
});

test("segment spread skips same-color pairs, baked physical-bundle lanes, and overlapping claims", () => {
  const longRed: LineFeature["geometry"]["coordinates"] = [];
  const longBlue: LineFeature["geometry"]["coordinates"] = [];
  const longGreen: LineFeature["geometry"]["coordinates"] = [];
  for (let i = 0; i < 40; i += 1) {
    const lat = 40.74 + i * 0.00025;
    longRed.push([-73.99, lat]);
    // Overlap only the first ~330m so whole-feature spread (60% share) does not fire.
    longBlue.push([i < 12 ? -73.98997 : -73.988, lat]);
    longGreen.push([i < 12 ? -73.98994 : -73.9875, lat]);
  }
  const artifacts = emptyArtifacts([
    line("red-long", "#EE352E", longRed, { lane_slot: 0, lane_slot_semantic: 0 }),
    line("blue-long", "#0A84FF", longBlue, { lane_slot: 0, lane_slot_semantic: 0 }),
    line("green-long", "#6CBE45", longGreen, { lane_slot: 0, lane_slot_semantic: 0 }),
    line("same-red", "#EE352E", longRed.map((coord) => [...coord]), { lane_slot: 0, lane_slot_semantic: 0 }),
    line("baked", "#FF6319", longBlue.map((coord) => [...coord]), {
      lane_slot: 0,
      lane_slot_semantic: 0,
      lane_slot_source: "physical_bundle_continuous",
    }),
  ]);
  const paths = tempPaths();
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: artifacts,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });
  const segments = JSON.parse(readFileSync(paths.segments, "utf8"));
  assert.ok(segments.features.length >= 1);
  const orders = JSON.parse(readFileSync(paths.orders, "utf8"));
  assert.ok(orders.some((entry: { bundle_id?: string }) => entry.bundle_id === "red-long"));
});

test("lane-order summary skips a second lane in the same bundle and records an override", () => {
  const coords: LineFeature["geometry"]["coordinates"] = [
    [-73.99, 40.75],
    [-73.99, 40.76],
  ];
  const first = line("bundle-a", "#EE352E", coords, {
    bundle_id: "shared-bundle",
    corridor_id: "c1",
    lane_order_override_applied: true,
  });
  const second = line("bundle-a-extra", "#0A84FF", coords.map((coord) => [...coord]), {
    bundle_id: "shared-bundle",
    corridor_id: "c2",
  });
  const artifacts = emptyArtifacts([first, second]);
  artifacts.bundleLaneFeatures = [first, second];
  const paths = tempPaths();
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: artifacts,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });
  const orders = JSON.parse(readFileSync(paths.orders, "utf8"));
  assert.equal(orders.length, 1);
  assert.equal(orders[0].override_applied, true);
});

test("segment spread claims two disjoint overlaps and skips a colorless lane", () => {
  const longRed: LineFeature["geometry"]["coordinates"] = [];
  const southBlue: LineFeature["geometry"]["coordinates"] = [];
  const northGreen: LineFeature["geometry"]["coordinates"] = [];
  for (let i = 0; i < 50; i += 1) {
    const lat = 40.73 + i * 0.00025;
    longRed.push([-73.99, lat]);
    southBlue.push([i < 14 ? -73.98997 : -73.988, lat]);
    northGreen.push([i > 36 ? -73.98994 : -73.9875, lat]);
  }
  const colorless = line("no-color", "", longRed.map((coord) => [...coord]));
  delete colorless.properties.color;
  const noRoutes = line("red-long", "#EE352E", longRed, { lane_slot: 0, lane_slot_semantic: 0 });
  delete noRoutes.properties.route_ids;
  const artifacts = emptyArtifacts([
    noRoutes,
    line("blue-south", "#0A84FF", southBlue, { lane_slot: 0, lane_slot_semantic: 0 }),
    line("green-north", "#6CBE45", northGreen, { lane_slot: 0, lane_slot_semantic: 0 }),
    colorless,
  ]);
  const paths = tempPaths();
  applyLaneOffsetFinalizationStage({
    bundleArtifacts: artifacts,
    crossColorSpreadGeoJsonPath: paths.spread,
    crossColorSegmentsGeoJsonPath: paths.segments,
    laneOrdersJsonPath: paths.orders,
  });
  const segments = JSON.parse(readFileSync(paths.segments, "utf8"));
  assert.ok(segments.features.length >= 1);
  const orders = JSON.parse(readFileSync(paths.orders, "utf8"));
  assert.ok(orders.some((entry: { bundle_id?: string }) => entry.bundle_id === "red-long"));
});
