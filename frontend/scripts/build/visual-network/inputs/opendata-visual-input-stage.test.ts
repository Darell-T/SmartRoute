import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { buildOpenDataVisualInputStage } from "./opendata-visual-input-stage.ts";

test("opendata visual input assigns corridor ids and is deterministic", () => {
  const dir = mkdtempSync(join(tmpdir(), "opendata-visual-"));
  const openDataLinesPath = join(dir, "lines.geojson");
  writeFileSync(
    openDataLinesPath,
    `${JSON.stringify({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: [
            [-73.99, 40.7],
            [-73.98, 40.71],
            [-73.97, 40.72],
          ],
        },
        properties: { objectid: "G", service: "G", service_name: "G service" },
      }],
    })}\n`,
  );
  const paths = {
    opendataLinesGeoJson: join(dir, "opendata.geojson"),
    edgesGeoJson: join(dir, "edges.geojson"),
    opendataOverlapsGeoJson: join(dir, "overlaps.geojson"),
  };
  const first = buildOpenDataVisualInputStage({
    openDataLinesPath,
    expectedOpenDataRouteIds: ["G"],
    expectedEdges: 1,
    topologyEdgeDiagnostics: { topology_edges_emitted: 1, topology_edges_dropped_missing_stop: 0 },
    openDataMinFragmentLengthM: 15,
    paths,
    parameters: {
      overlapMinRatio: 0.6,
      overlapSharedLenMinM: 250,
      containmentAvgDistanceMaxM: 15,
      tangentMaxDiffDeg: 30,
    },
  });
  const second = buildOpenDataVisualInputStage({
    openDataLinesPath,
    expectedOpenDataRouteIds: ["G"],
    expectedEdges: 1,
    topologyEdgeDiagnostics: { topology_edges_emitted: 1, topology_edges_dropped_missing_stop: 0 },
    openDataMinFragmentLengthM: 15,
    paths,
    parameters: {
      overlapMinRatio: 0.6,
      overlapSharedLenMinM: 250,
      containmentAvgDistanceMaxM: 15,
      tangentMaxDiffDeg: 30,
    },
  });
  assert.ok(first.corridorFeatures.length >= 1);
  assert.equal(first.corridorFeatures[0].properties.opendata_line_id, "opendata-00001");
  assert.deepEqual(second.corridorFeatures[0].geometry.coordinates, first.corridorFeatures[0].geometry.coordinates);
});

test("opendata visual input reports missing expected routes and zero expected edges", () => {
  const dir = mkdtempSync(join(tmpdir(), "opendata-visual-missing-"));
  const openDataLinesPath = join(dir, "lines.geojson");
  writeFileSync(
    openDataLinesPath,
    `${JSON.stringify({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: [
            [-73.99, 40.7],
            [-73.98, 40.71],
          ],
        },
        properties: { objectid: "G", service: "G", service_name: "G service" },
      }],
    })}\n`,
  );
  const result = buildOpenDataVisualInputStage({
    openDataLinesPath,
    expectedOpenDataRouteIds: ["G", "L"],
    expectedEdges: 0,
    topologyEdgeDiagnostics: { topology_edges_emitted: 0, topology_edges_dropped_missing_stop: 0 },
    openDataMinFragmentLengthM: 15,
    paths: {
      opendataLinesGeoJson: join(dir, "opendata.geojson"),
      edgesGeoJson: join(dir, "edges.geojson"),
      opendataOverlapsGeoJson: join(dir, "overlaps.geojson"),
    },
    parameters: {
      overlapMinRatio: 0.6,
      overlapSharedLenMinM: 250,
      containmentAvgDistanceMaxM: 15,
      tangentMaxDiffDeg: 30,
    },
  });
  assert.equal(result.corridorFeatures.length, 1);
  assert.equal(result.pairsConsidered, 0);
});
