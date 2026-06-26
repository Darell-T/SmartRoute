import assert from "node:assert/strict";
import test from "node:test";

import { applyJoralemonGreenRiverSmoothing } from "./joralemon-green-river.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

const GREEN = "#00933C";

type TestFeatureProperties = {
  corridor_id: string;
  color: string;
  route_ids: string[];
  joralemon_green_river_smoothed?: boolean;
  joralemon_green_river_start_arc_m?: number;
  joralemon_green_river_end_arc_m?: number;
  joralemon_green_river_replaced_length_m?: number;
};

function feature(coords: Position[]): Feature<LineStringGeometry, TestFeatureProperties> {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: coords,
    },
    properties: {
      corridor_id: "opendata-00009",
      color: GREEN,
      route_ids: ["4", "5", "6", "6X"],
    },
  };
}

function bearingDeg(a: Position, b: Position): number {
  const lat = ((a[1] + b[1]) / 2) * Math.PI / 180;
  const dx = (b[0] - a[0]) * 111320 * Math.cos(lat);
  const dy = (b[1] - a[1]) * 110574;
  return Math.atan2(dy, dx) * 180 / Math.PI;
}

function turnDeg(a: Position, b: Position, c: Position): number {
  let turn = bearingDeg(b, c) - bearingDeg(a, b);
  while (turn > 180) turn -= 360;
  while (turn < -180) turn += 360;
  return Math.abs(turn);
}

function maxTurn(coords: Position[]): number {
  let max = 0;
  for (let index = 1; index < coords.length - 1; index += 1) {
    max = Math.max(max, turnDeg(coords[index - 1], coords[index], coords[index + 1]));
  }
  return max;
}

test("Joralemon green river smoothing removes the local water-crossing wiggle", () => {
  const before: Position[] = [
    [-73.9850, 40.6905],
    [-73.9940, 40.6935],
    [-74.0030, 40.6965],
    [-74.0078, 40.6979],
    [-74.0085, 40.6982],
    [-74.0081, 40.6985],
    [-74.0087, 40.6988],
    [-74.0110, 40.7020],
    [-74.0130, 40.7060],
    [-74.0100, 40.7110],
  ];

  const { features, diagnostics } = applyJoralemonGreenRiverSmoothing([feature(before)], {
    bbox: {
      minLon: -74.0115,
      maxLon: -74.0065,
      minLat: 40.6970,
      maxLat: 40.7000,
    },
    marginM: 260,
    sampleM: 6,
  });

  const after = features[0].geometry.coordinates;
  assert.ok(features[0]);
  assert.equal(diagnostics.applied, true);
  assert.equal(features[0].properties.joralemon_green_river_smoothed, true);
  assert.deepEqual(after[0], before[0]);
  assert.deepEqual(after.at(-1), before.at(-1));
  assert.ok(after.length > before.length, "smoothed crossing should be sampled densely");
  assert.ok(
    maxTurn(after) < maxTurn(before) * 0.45,
    `expected max turn to drop substantially: before=${maxTurn(before)} after=${maxTurn(after)}`,
  );
});
