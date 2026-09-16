import assert from "node:assert/strict";
import test from "node:test";

import { applyJoralemonGreenRiverSmoothing } from "./joralemon-green-river.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

const GREEN = "#00933C";

type TestFeatureProperties = {
  corridor_id: string;
  color?: string;
  route_ids?: string[];
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

  const firstInput = [feature(before)];
  const secondInput = [feature(before)];
  const options = {
    bbox: {
      minLon: -74.0115,
      maxLon: -74.0065,
      minLat: 40.6970,
      maxLat: 40.7000,
    },
    marginM: 260,
    sampleM: 6,
  };
  const { features, diagnostics } = applyJoralemonGreenRiverSmoothing(firstInput, options);
  const again = applyJoralemonGreenRiverSmoothing(secondInput, options);
  assert.equal(JSON.stringify(diagnostics), JSON.stringify(again.diagnostics));

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

test("Joralemon green river smoothing is a no-op for empty lists and unmatched colors", () => {
  const emptyFirst = applyJoralemonGreenRiverSmoothing([]);
  const emptySecond = applyJoralemonGreenRiverSmoothing([]);
  assert.equal(emptyFirst.diagnostics.applied, false);
  assert.deepEqual(emptyFirst.features, []);
  assert.equal(JSON.stringify(emptyFirst), JSON.stringify(emptySecond));

  const red: Feature<LineStringGeometry, TestFeatureProperties> = {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [-74.0085, 40.6982],
        [-74.0087, 40.6988],
      ],
    },
    properties: {
      corridor_id: "red",
      color: "#EE352E",
      route_ids: ["2"],
    },
  };
  const unmatched = applyJoralemonGreenRiverSmoothing([red]);
  assert.equal(unmatched.diagnostics.applied, false);
  assert.equal(unmatched.features[0], red);
  assert.deepEqual(unmatched.features[0].geometry.coordinates, red.geometry.coordinates);
});

test("Joralemon green river smoothing clamps replacement to the green 4/5 endpoints", () => {
  const before: Position[] = [
    [-74.0085, 40.6982],
    [-74.0087, 40.6988],
    [-74.0092, 40.6996],
    [-74.0100, 40.7010],
    [-74.0110, 40.7030],
    [-74.0120, 40.7050],
  ];
  const { features, diagnostics } = applyJoralemonGreenRiverSmoothing([feature(before)], {
    bbox: {
      minLon: -74.0115,
      maxLon: -74.0065,
      minLat: 40.6970,
      maxLat: 40.7000,
    },
    marginM: 800,
    sampleM: 6,
  });
  assert.equal(diagnostics.applied, true);
  assert.deepEqual(features[0].geometry.coordinates[0], before[0]);
  assert.deepEqual(features[0].geometry.coordinates.at(-1), before.at(-1));
  const joralemonRouteIds = features[0].properties.route_ids;
  assert.ok(Array.isArray(joralemonRouteIds));
  assert.equal(joralemonRouteIds.join(","), "4,5,6,6X");
});

test("Joralemon green river smoothing leaves a river window shorter than 40m unchanged", () => {
  const before: Position[] = [
    [-74.0085, 40.6982],
    [-74.00855, 40.69825],
    [-74.0086, 40.6983],
  ];
  const input = [feature(before)];
  const { features, diagnostics } = applyJoralemonGreenRiverSmoothing(input, {
    bbox: {
      minLon: -74.0115,
      maxLon: -74.0065,
      minLat: 40.6970,
      maxLat: 40.7000,
    },
    marginM: 0,
    sampleM: 6,
  });
  assert.equal(diagnostics.applied, false);
  assert.equal(features[0], input[0]);
  assert.deepEqual(features[0].geometry.coordinates, before);
});

test("Joralemon green river smoothing ignores green geometry that is not the 4/5 trunk", () => {
  const sixOnly: Feature<LineStringGeometry, TestFeatureProperties> = {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [-74.0085, 40.6982],
        [-74.0087, 40.6988],
        [-74.0110, 40.7020],
      ],
    },
    properties: {
      corridor_id: "green-6",
      color: GREEN,
      route_ids: ["6"],
    },
  };
  const { features, diagnostics } = applyJoralemonGreenRiverSmoothing([sixOnly]);
  assert.equal(diagnostics.applied, false);
  assert.equal(features[0], sixOnly);
});

test("Joralemon green river smoothing skips missing color, missing route ids, and default-bbox misses", () => {
  const noColor: Feature<LineStringGeometry, TestFeatureProperties> = {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [-74.0085, 40.6982],
        [-74.0087, 40.6988],
        [-74.0110, 40.7020],
      ],
    },
    properties: {
      corridor_id: "no-color",
      color: "",
      route_ids: ["4", "5"],
    },
  };
  delete noColor.properties.color;
  const noRoutes = feature([
    [-74.0085, 40.6982],
    [-74.0087, 40.6988],
    [-74.0110, 40.7020],
  ]);
  delete noRoutes.properties.route_ids;
  const { diagnostics } = applyJoralemonGreenRiverSmoothing([noColor, noRoutes]);
  assert.equal(diagnostics.applied, false);
  const distant = applyJoralemonGreenRiverSmoothing([
    feature([
      [-73.90, 40.70],
      [-73.90, 40.71],
    ]),
  ]);
  assert.equal(distant.diagnostics.applied, false);
});
