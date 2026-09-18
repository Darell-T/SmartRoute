import assert from "node:assert/strict";
import test from "node:test";
import { buildOpenDataInputsStage } from "./opendata-inputs.ts";
import type { Position } from "../shared/types.ts";

function line(id: string, routeIds: string[], coordinates: Position[]) {
  return {
    geometry: { type: "LineString" as const, coordinates },
    properties: {
      opendata_line_id: id,
      route_ids: routeIds,
      length_m: 1200,
    },
  };
}

const NORTH: Position[] = [
  [-73.99, 40.75],
  [-73.99, 40.76],
  [-73.99, 40.77],
];

test("buildOpenDataInputsStage copies corridor ids and source geometry", () => {
  const result = buildOpenDataInputsStage({
    opendataLineFeatures: [line("opendata-00001", ["G"], NORTH)],
    geometrySourceName: "nyc_opendata_subway_service_lines",
    overlapMinRatio: 0.6,
    overlapSharedLenMinM: 250,
    containmentAvgDistanceMaxM: 15,
    tangentMaxDiffDeg: 30,
  });
  assert.equal(result.corridorFeatures.length, 1);
  assert.equal(result.corridorFeatures[0].properties.corridor_id, "opendata-00001");
  assert.deepEqual(result.corridorFeatures[0].geometry.coordinates, NORTH);
  assert.equal(result.corridorRows[0].geometry_source, "nyc_opendata_subway_service_lines");
  assert.deepEqual(result.matchedPairs, []);
  assert.equal(result.pairsConsidered, 0);
});

test("buildOpenDataInputsStage warns when disjoint-route lines overlap", () => {
  const result = buildOpenDataInputsStage({
    opendataLineFeatures: [
      line("left", ["G"], NORTH),
      line("right", ["L"], NORTH),
    ],
    geometrySourceName: "nyc_opendata_subway_service_lines",
    overlapMinRatio: 0.5,
    overlapSharedLenMinM: 10,
    containmentAvgDistanceMaxM: 50,
    tangentMaxDiffDeg: 90,
  });
  assert.equal(result.opendataOverlapWarnings.length, 1);
  assert.equal(result.opendataOverlapWarnings[0].properties.left_corridor_id, "left");
  assert.equal(result.opendataOverlapWarnings[0].properties.right_corridor_id, "right");
  assert.deepEqual(
    buildOpenDataInputsStage({
      opendataLineFeatures: [
        line("left", ["G"], NORTH),
        line("right", ["L"], NORTH),
      ],
      geometrySourceName: "nyc_opendata_subway_service_lines",
      overlapMinRatio: 0.5,
      overlapSharedLenMinM: 10,
      containmentAvgDistanceMaxM: 50,
      tangentMaxDiffDeg: 90,
    }).opendataOverlapWarnings[0].properties,
    result.opendataOverlapWarnings[0].properties,
  );
});

test("buildOpenDataInputsStage skips overlap warnings when route sets intersect", () => {
  const result = buildOpenDataInputsStage({
    opendataLineFeatures: [
      line("left", ["G"], NORTH),
      line("right", ["G", "F"], NORTH),
    ],
    geometrySourceName: "nyc_opendata_subway_service_lines",
    overlapMinRatio: 0.5,
    overlapSharedLenMinM: 10,
    containmentAvgDistanceMaxM: 50,
    tangentMaxDiffDeg: 90,
  });
  assert.deepEqual(result.opendataOverlapWarnings, []);
});

test("buildOpenDataInputsStage accepts an empty feature list", () => {
  const result = buildOpenDataInputsStage({
    opendataLineFeatures: [],
    geometrySourceName: "nyc_opendata_subway_service_lines",
    overlapMinRatio: 0.6,
    overlapSharedLenMinM: 250,
    containmentAvgDistanceMaxM: 15,
    tangentMaxDiffDeg: 30,
  });
  assert.deepEqual(result.corridorFeatures, []);
  assert.deepEqual(result.corridorRows, []);
  assert.deepEqual(result.opendataOverlapWarnings, []);
});

test("buildOpenDataInputsStage skips overlap that is too short or too far and marks shared rows", () => {
  const west: Position[] = [
    [-74.02, 40.75],
    [-74.02, 40.76],
    [-74.02, 40.77],
  ];
  const result = buildOpenDataInputsStage({
    opendataLineFeatures: [
      line("shared", ["F", "G"], NORTH),
      line("far", ["L"], west),
    ],
    geometrySourceName: "nyc_opendata_subway_service_lines",
    overlapMinRatio: 0.9,
    overlapSharedLenMinM: 5000,
    containmentAvgDistanceMaxM: 1,
    tangentMaxDiffDeg: 1,
  });
  assert.equal(result.corridorRows[0].is_shared, true);
  assert.deepEqual(result.opendataOverlapWarnings, []);
});
