import assert from "node:assert/strict";
import test from "node:test";
import { normalizeOpenDataSubwayLines } from "./opendata-subway-lines.ts";
import type { Position } from "./types.ts";

type TestLineStringGeometry = {
  type: "LineString";
  coordinates: Position[];
};

type TestMultiLineStringGeometry = {
  type: "MultiLineString";
  coordinates: Position[][];
};

type TestPointGeometry = {
  type: "Point";
  coordinates: Position;
};

type TestGeometry = TestLineStringGeometry | TestMultiLineStringGeometry | TestPointGeometry;

type TestFeature = {
  type: "Feature";
  geometry: TestGeometry;
  properties: {
    objectid: string;
    service: string;
    service_name: string;
  };
};

type TestCollection = {
  type: "FeatureCollection";
  features: TestFeature[];
};

function feature(symbol: string, geometry: TestGeometry = line()): TestFeature {
  return {
    type: "Feature",
    geometry,
    properties: {
      objectid: symbol,
      service: symbol,
      service_name: `${symbol} service`,
    },
  };
}

function line(): TestLineStringGeometry {
  return {
    type: "LineString",
    coordinates: [
      [-73.99, 40.7],
      [-73.98, 40.71],
      [-73.97, 40.72],
    ],
  };
}

function collection(features: TestFeature[]): TestCollection {
  return { type: "FeatureCollection", features };
}

test("parses grouped numeric route symbols", () => {
  const result = normalizeOpenDataSubwayLines(collection([feature("1-2-3")]));
  assert.deepEqual(result.features[0].properties.route_ids, ["1", "2", "3"]);
});

test("parses grouped letter route symbols", () => {
  const result = normalizeOpenDataSubwayLines(collection([feature("A-C")]));
  assert.deepEqual(result.features[0].properties.route_ids, ["A", "C"]);
});

test("normalizes MTA variant route ids", () => {
  const result = normalizeOpenDataSubwayLines(
    collection([
      feature("6D"),
      feature("7D"),
      feature("FD"),
      feature("SIR"),
    ]),
  );
  const routeIds = result.features.map((f) => f.properties.route_ids[0]).sort();
  assert.deepEqual(routeIds, ["6X", "7X", "FX", "SI"].sort());
});

test("maps OpenData shuttle service names to project shuttle route ids", () => {
  const result = normalizeOpenDataSubwayLines(
    collection([feature("SF"), feature("ST"), feature("SR")]),
  );
  const routeIds = result.features.map((f) => f.properties.route_ids[0]).sort();
  assert.deepEqual(routeIds, ["FS", "GS", "H"].sort());
});

test("adds expected express aliases when OpenData omits explicit express service", () => {
  const result = normalizeOpenDataSubwayLines(
    collection([feature("6"), feature("7"), feature("F")]),
    { expectedRouteIds: ["6", "6X", "7", "7X", "F", "FX"] },
  );
  assert.deepEqual(result.features[0].properties.route_ids, ["6", "6X"]);
  assert.deepEqual(result.features[1].properties.route_ids, ["7", "7X"]);
  assert.deepEqual(result.features[2].properties.route_ids, ["F", "FX"]);
  assert.deepEqual(
    result.diagnostics.alias_applications.map((row) => row.alias_route_id).sort(),
    ["6X", "7X", "FX"].sort(),
  );
});

test("rejects ambiguous raw S shuttle symbol", () => {
  assert.throws(
    () => normalizeOpenDataSubwayLines(collection([feature("S")])),
    /ambiguous shuttle route symbol/,
  );
});

test("flattens MultiLineString into deterministic LineString parts", () => {
  const result = normalizeOpenDataSubwayLines(
    collection([
      feature("A", {
        type: "MultiLineString",
        coordinates: [
          [
            [-73.99, 40.7],
            [-73.98, 40.71],
          ],
          [
            [-73.97, 40.72],
            [-73.96, 40.73],
          ],
        ],
      }),
    ]),
  );
  assert.equal(result.features.length, 2);
  assert.equal(result.features[0].geometry.type, "LineString");
  assert.equal(result.features[0].properties.opendata_part_index, 0);
  assert.equal(result.features[1].properties.opendata_part_index, 1);
});

test("rejects invalid geometries", () => {
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "Point",
            coordinates: [-73.99, 40.7],
          }),
        ]),
      ),
    /unsupported geometry type/,
  );
});

test("drops tiny fragments when requested", () => {
  const result = normalizeOpenDataSubwayLines(
    collection([
      feature("E", {
        type: "MultiLineString",
        coordinates: [
          [
            [-74.0076594407752, 40.715415136351695],
            [-74.00768650220247, 40.71538010042101],
          ],
          [
            [-74.0076594407752, 40.715415136351695],
            [-74.00977740865635, 40.712466547821165],
          ],
        ],
      }),
    ]),
    { minFragmentLengthM: 15 },
  );

  assert.equal(result.features.length, 1);
  assert.equal(result.features[0].properties.route_ids[0], "E");
  assert.equal(result.features[0].properties.opendata_part_index, 1);
  assert.equal(result.diagnostics.dropped_short_fragment_count, 1);
});
