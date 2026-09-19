import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { applyRouteContinuityRepairStage } from "./route-continuity-repair-stage.ts";
import type { LineFeature } from "../shared/types.ts";

function writeCanonical(path: string, features: unknown[]): void {
  writeFileSync(path, `${JSON.stringify({ type: "FeatureCollection", features })}\n`);
}

function line(id: string, routes: string[], coordinates: Array<[number, number]>): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { corridor_id: id, route_ids: routes },
  };
}

const GAPS = {
  bridgeMinGapM: 1,
  bridgeMaxGapM: 80,
  bridgeSubsetConnectorMaxGapM: 80,
  offRevenueMaxM: 40,
};

test("route continuity repair is a no-op when visualFeatures is missing", () => {
  const bundleArtifacts = {};
  applyRouteContinuityRepairStage({
    bundleArtifacts,
    canonicalGeoJsonPath: join(tmpdir(), "unused.geojson"),
    ...GAPS,
  });
  assert.equal("visualFeatures" in bundleArtifacts, false);
});

test("route continuity repair leaves empty features empty against an empty GTFS document", () => {
  const dir = mkdtempSync(join(tmpdir(), "continuity-"));
  const canonicalPath = join(dir, "canonical.geojson");
  writeCanonical(canonicalPath, []);
  const firstFeatures: LineFeature[] = [];
  const secondFeatures: LineFeature[] = [];
  const first = { visualFeatures: firstFeatures };
  const second = { visualFeatures: secondFeatures };
  applyRouteContinuityRepairStage({ bundleArtifacts: first, canonicalGeoJsonPath: canonicalPath, ...GAPS });
  applyRouteContinuityRepairStage({ bundleArtifacts: second, canonicalGeoJsonPath: canonicalPath, ...GAPS });
  assert.deepEqual(first.visualFeatures, []);
  assert.deepEqual(second.visualFeatures, []);
});

test("route continuity repair ignores malformed GTFS members and keeps exact unmatched coords", () => {
  const dir = mkdtempSync(join(tmpdir(), "continuity-"));
  const canonicalPath = join(dir, "canonical.geojson");
  writeCanonical(canonicalPath, [
    null,
    { type: "Feature", geometry: { type: "Point", coordinates: [-73.99, 40.75] }, properties: { route_id: "1" } },
    { type: "Feature", geometry: { type: "LineString", coordinates: [[-73.99]] }, properties: { route_id: "1" } },
    {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [[-73.99, 40.75], [-73.99, 40.76]],
      },
      properties: { route_id: "1" },
    },
  ]);
  const coords: Array<[number, number]> = [[-73.98, 40.74], [-73.98, 40.741], [-73.98, 40.742]];
  const first = { visualFeatures: [line("keep", ["2"], structuredClone(coords))] };
  const second = { visualFeatures: [line("keep", ["2"], structuredClone(coords))] };
  applyRouteContinuityRepairStage({ bundleArtifacts: first, canonicalGeoJsonPath: canonicalPath, ...GAPS });
  applyRouteContinuityRepairStage({ bundleArtifacts: second, canonicalGeoJsonPath: canonicalPath, ...GAPS });
  assert.equal(first.visualFeatures[0].properties.corridor_id, "keep");
  assert.deepEqual(first.visualFeatures[0].geometry.coordinates, coords);
  assert.equal(JSON.stringify(first.visualFeatures), JSON.stringify(second.visualFeatures));
});

test("route continuity repair bridges a same-route gap and snaps an off-revenue wander", () => {
  const dir = mkdtempSync(join(tmpdir(), "continuity-gap-"));
  const canonicalPath = join(dir, "canonical.geojson");
  writeCanonical(canonicalPath, [
    {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.99, 40.75],
          [-73.99, 40.752],
          [-73.99, 40.754],
        ],
      },
      properties: { route_id: "1" },
    },
  ]);
  const south = line("south", ["1"], [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  const north = line("north", ["1"], [
    [-73.99, 40.7514],
    [-73.99, 40.7525],
  ]);
  const wander = line("wander", ["1"], [
    [-73.9892, 40.753],
    [-73.9892, 40.7535],
    [-73.99, 40.754],
  ]);
  const artifacts = { visualFeatures: [south, north, wander] };
  applyRouteContinuityRepairStage({
    bundleArtifacts: artifacts,
    canonicalGeoJsonPath: canonicalPath,
    ...GAPS,
  });
  assert.ok(artifacts.visualFeatures.some((feature) => feature.properties.corridor_id === "south"));
  assert.ok(artifacts.visualFeatures.some((feature) => feature.properties.corridor_id === "north"));
});

test("route continuity repair matches one of two GTFS tracks and skips object route ids", () => {
  const dir = mkdtempSync(join(tmpdir(), "continuity-tracks-"));
  const canonicalPath = join(dir, "canonical.geojson");
  writeCanonical(canonicalPath, [
    {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.99, 40.75],
          [-73.99, 40.752],
          [-73.99, 40.754],
        ],
      },
      properties: { route_id: 1 },
    },
    {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.98, 40.74],
          [-73.98, 40.742],
          [-73.98, 40.744],
        ],
      },
      properties: { route_id: 1 },
    },
    {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.97, 40.73],
          [-73.97, 40.732],
        ],
      },
      properties: { route_id: { id: "1" } },
    },
    { type: "Feature", geometry: { type: "LineString", coordinates: [[-73.96, 40.72], [-73.96, 40.73]] } },
  ]);
  const wander = line("wander", ["1"], [
    [-73.9892, 40.753],
    [-73.9892, 40.7535],
    [-73.9892, 40.754],
    [-73.99, 40.754],
  ]);
  const short = line("short", ["1"], [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  const artifacts = { visualFeatures: [wander, short] };
  applyRouteContinuityRepairStage({
    bundleArtifacts: artifacts,
    canonicalGeoJsonPath: canonicalPath,
    ...GAPS,
  });
  assert.equal(artifacts.visualFeatures[0].properties.corridor_id, "wander");
  assert.deepEqual(short.geometry.coordinates, [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
});

for (const reversed of [false, true]) {
  test(`a partial visual branch selects a covering GTFS shape over a short-turn shuttle (reversed=${reversed})`, () => {
    const dir = mkdtempSync(join(tmpdir(), "continuity-short-turn-"));
    const canonicalPath = join(dir, "canonical.geojson");
    const full: Array<[number, number]> = [[-73.93, 40.88], [-73.93, 40.70]];
    const shortTurn: Array<[number, number]> = [[-73.93, 40.88], [-73.93, 40.84]];
    writeCanonical(canonicalPath, [shortTurn, reversed ? full.toReversed() : full].map((coordinates) => ({
      type: "Feature",
      geometry: { type: "LineString", coordinates },
      properties: { route_id: "5" },
    })));
    const coords: Array<[number, number]> = [
      [-73.93, 40.88], [-73.93, 40.86], [-73.93, 40.84], [-73.93, 40.82], [-73.93, 40.817],
    ];
    const branch = line("five-to-junction", ["5"], structuredClone(coords));
    applyRouteContinuityRepairStage({ bundleArtifacts: { visualFeatures: [branch] }, canonicalGeoJsonPath: canonicalPath, ...GAPS });
    assert.deepEqual(branch.geometry.coordinates, coords, "the branch must retain its junction instead of ending at the shuttle terminus");
    assert.equal(branch.properties.off_revenue_rerouted, undefined);
  });
}
