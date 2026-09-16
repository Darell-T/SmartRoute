import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { applyTerminalOverhangTrimStage } from "./terminal-overhang-trim-stage.ts";
import type { LineFeature } from "../shared/types.ts";

const LAT = 40.7;
const M_PER_DEG_LON = 111320 * Math.cos((LAT * Math.PI) / 180);
const lonAt = (m: number): number => -74 + m / M_PER_DEG_LON;

function lineFeature(routes: string[], fromM: number, toM: number, step = 50): LineFeature {
  const coordinates: Array<[number, number]> = [];
  for (let m = fromM; m < toM; m += step) coordinates.push([lonAt(m), LAT]);
  coordinates.push([lonAt(toM), LAT]);
  return {
    type: "Feature",
    properties: { route_ids: routes, visual_feature_type: "bundle_lane" },
    geometry: { type: "LineString", coordinates },
  };
}

function lengthM(feature: LineFeature): number {
  const cs = feature.geometry.coordinates;
  let total = 0;
  for (let i = 1; i < cs.length; i += 1) {
    total += Math.abs(cs[i][0] - cs[i - 1][0]) * M_PER_DEG_LON;
  }
  return total;
}

function writeStations(path: string, stations: Array<{ id: string; routes: string[]; atM: number }>): void {
  writeFileSync(path, `${JSON.stringify({
    type: "FeatureCollection",
    features: stations.map((station) => ({
      type: "Feature",
      properties: { station_id: station.id, name: station.id, route_ids: station.routes },
      geometry: { type: "Point", coordinates: [lonAt(station.atM), LAT] },
    })),
  })}\n`);
}

test("terminal overhang stage leaves empty features empty and is deterministic", () => {
  const dir = mkdtempSync(join(tmpdir(), "trim-stage-"));
  const stationsPath = join(dir, "stations.geojson");
  writeStations(stationsPath, []);
  const firstFeatures: LineFeature[] = [];
  const secondFeatures: LineFeature[] = [];
  const first = { visualFeatures: firstFeatures };
  const second = { visualFeatures: secondFeatures };
  const input = {
    stationsGeoJsonPath: stationsPath,
    branchesByRoute: new Map<string, Array<{ terminal_start: string; terminal_end: string }>>(),
    stopsById: new Map<string, { lon: number; lat: number }>(),
  };
  applyTerminalOverhangTrimStage({ bundleArtifacts: first, ...input });
  applyTerminalOverhangTrimStage({ bundleArtifacts: second, ...input });
  assert.deepEqual(first.visualFeatures, []);
  assert.deepEqual(second.visualFeatures, []);
});

test("terminal overhang stage trims free ends using GTFS terminals from the stations file", () => {
  const dir = mkdtempSync(join(tmpdir(), "trim-stage-"));
  const stationsPath = join(dir, "stations.geojson");
  writeStations(stationsPath, [
    { id: "s1", routes: ["A"], atM: 100 },
    { id: "s2", routes: ["A"], atM: 700 },
  ]);
  const first = { visualFeatures: [lineFeature(["A"], 0, 1000)] };
  const second = { visualFeatures: [lineFeature(["A"], 0, 1000)] };
  const input = {
    stationsGeoJsonPath: stationsPath,
    branchesByRoute: new Map([
      ["A", [{ terminal_start: "start", terminal_end: "end" }]],
    ]),
    stopsById: new Map([
      ["start", { lon: lonAt(100), lat: LAT }],
      ["end", { lon: lonAt(700), lat: LAT }],
    ]),
  };
  applyTerminalOverhangTrimStage({ bundleArtifacts: first, ...input });
  applyTerminalOverhangTrimStage({ bundleArtifacts: second, ...input });
  const len = lengthM(first.visualFeatures[0]);
  assert.ok(len > 600 && len < 680, `expected ~640m, got ${len}`);
  const trimmedRoutes = first.visualFeatures[0].properties.route_ids;
  assert.ok(Array.isArray(trimmedRoutes));
  assert.equal(trimmedRoutes[0], "A");
  assert.equal(JSON.stringify(first.visualFeatures), JSON.stringify(second.visualFeatures));
});

test("terminal overhang stage skips missing GTFS stops and leaves unmatched geometry", () => {
  const dir = mkdtempSync(join(tmpdir(), "trim-stage-"));
  const stationsPath = join(dir, "stations.geojson");
  writeStations(stationsPath, [{ id: "s1", routes: ["A"], atM: 100 }]);
  const coords: Array<[number, number]> = [[lonAt(0), LAT], [lonAt(80), LAT]];
  const bundleArtifacts = {
    visualFeatures: [{
      type: "Feature" as const,
      geometry: { type: "LineString" as const, coordinates: structuredClone(coords) },
      properties: { route_ids: ["A"], visual_feature_type: "bundle_lane" },
    }],
  };
  applyTerminalOverhangTrimStage({
    bundleArtifacts,
    stationsGeoJsonPath: stationsPath,
    branchesByRoute: new Map([["A", [{ terminal_start: "missing", terminal_end: "also-missing" }]]]),
    stopsById: new Map(),
  });
  assert.deepEqual(bundleArtifacts.visualFeatures[0].geometry.coordinates, coords);
});

test("terminal overhang stage drops a stationless spur and runs a second pass", () => {
  const dir = mkdtempSync(join(tmpdir(), "trim-stage-"));
  const stationsPath = join(dir, "stations.geojson");
  writeStations(stationsPath, [
    { id: "s1", routes: ["A"], atM: 100 },
    { id: "s2", routes: ["A"], atM: 950 },
  ]);
  const main = lineFeature(["A"], 0, 1000);
  const spur = lineFeature(["A"], 1000, 2400);
  const bundleArtifacts = { visualFeatures: [main, spur] };
  applyTerminalOverhangTrimStage({
    bundleArtifacts,
    stationsGeoJsonPath: stationsPath,
    branchesByRoute: new Map([["A", [{ terminal_start: "start", terminal_end: "end" }]]]),
    stopsById: new Map([
      ["start", { lon: lonAt(100), lat: LAT }],
      ["end", { lon: lonAt(950), lat: LAT }],
    ]),
  });
  assert.equal(bundleArtifacts.visualFeatures.length, 1);
  const leftoverRoutes = bundleArtifacts.visualFeatures[0].properties.route_ids;
  assert.ok(Array.isArray(leftoverRoutes));
  assert.equal(leftoverRoutes[0], "A");
  assert.ok(lengthM(bundleArtifacts.visualFeatures[0]) < 1000);
});

test("terminal overhang stage skips GTFS terminals with non-finite coordinates", () => {
  const dir = mkdtempSync(join(tmpdir(), "trim-stage-"));
  const stationsPath = join(dir, "stations.geojson");
  writeStations(stationsPath, [{ id: "s1", routes: ["A"], atM: 100 }]);
  const coords: Array<[number, number]> = [[lonAt(0), LAT], [lonAt(400), LAT]];
  const bundleArtifacts = { visualFeatures: [lineFeature(["A"], 0, 400)] };
  applyTerminalOverhangTrimStage({
    bundleArtifacts,
    stationsGeoJsonPath: stationsPath,
    branchesByRoute: new Map([["A", [{ terminal_start: "start", terminal_end: "end" }]]]),
    stopsById: new Map([
      ["start", { lon: Number.NaN, lat: LAT }],
      ["end", { lon: lonAt(400), lat: Number.POSITIVE_INFINITY }],
    ]),
  });
  assert.deepEqual(bundleArtifacts.visualFeatures[0].geometry.coordinates[0], coords[0]);
});
