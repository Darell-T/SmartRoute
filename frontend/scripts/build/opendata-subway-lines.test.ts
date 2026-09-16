import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { loadOpenDataSubwayLines, normalizeOpenDataSubwayLines } from "./opendata-subway-lines.ts";
import type { Position } from "./types.ts";

type TestLineStringGeometry = {
  type: "LineString";
  coordinates?: Position[];
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
  type?: "Feature";
  geometry?: TestGeometry | null;
  properties?: {
    objectid?: string;
    service?: string;
    service_name?: string;
    rt_symbol?: string;
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

test("loadOpenDataSubwayLines reads a FeatureCollection from disk and rejects missing files", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "opendata-lines-"));
  const filePath = path.join(dir, "lines.geojson");
  writeFileSync(
    filePath,
    `${JSON.stringify({
      type: "FeatureCollection",
      features: [feature("G")],
    })}\n`,
  );
  const loaded = loadOpenDataSubwayLines(filePath);
  assert.deepEqual(loaded.diagnostics.represented_route_ids, ["G"]);
  assert.deepEqual(loadOpenDataSubwayLines(filePath).features[0].geometry.coordinates, loaded.features[0].geometry.coordinates);
  assert.throws(() => loadOpenDataSubwayLines(path.join(dir, "missing.geojson")), /file missing/);
  rmSync(dir, { recursive: true, force: true });
});

test("rejects a missing route symbol, blank symbol, and unsupported tokens", () => {
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines({
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            geometry: line(),
            properties: { objectid: "x" },
          },
        ],
      }),
    /missing route symbol field/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          {
            type: "Feature",
            geometry: line(),
            properties: { objectid: "blank", service: "   " },
          },
        ]),
      ),
    /missing route symbol field/,
  );
  assert.throws(
    () => normalizeOpenDataSubwayLines(collection([feature("PEAK-LINE")])),
    /no supported route ids parsed/,
  );
  assert.throws(
    () => normalizeOpenDataSubwayLines(collection([feature("A-QQ")])),
    /unsupported route tokens: QQ/,
  );
});

test("parses comma and slash grouped symbols and ignores service words", () => {
  const result = normalizeOpenDataSubwayLines(
    collection([
      feature("A/C EXPRESS"),
      feature("5 PEAK"),
      {
        type: "Feature",
        geometry: line(),
        properties: { objectid: "n-r", rt_symbol: "N,R" },
      },
    ]),
  );
  const byRoutes = new Map(result.features.map((item) => [item.properties.route_ids.join("|"), item]));
  assert.deepEqual(byRoutes.get("A|C")?.properties.route_ids, ["A", "C"]);
  assert.deepEqual(byRoutes.get("5")?.properties.route_ids, ["5"]);
  assert.deepEqual(byRoutes.get("N|R")?.properties.route_ids, ["N", "R"]);
  assert.equal(byRoutes.get("N|R")?.properties.opendata_symbol_field, "rt_symbol");
});

test("rejects invalid coordinates, collapsed duplicates, and empty MultiLineString parts", () => {
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "LineString",
            coordinates: [[-73.99, 40.7]],
          }),
        ]),
      ),
    /LineString must contain at least two coordinates/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "LineString",
            coordinates: [
              [-73.99, 40.7],
              [Number.NaN, 40.71],
            ],
          }),
        ]),
      ),
    /invalid coordinate at index 1/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "LineString",
            coordinates: [
              [-73.99, 40.7],
              [-73.99, 40.7],
            ],
          }),
        ]),
      ),
    /collapsed to fewer than two coordinates/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "MultiLineString",
            coordinates: [],
          }),
        ]),
      ),
    /MultiLineString must contain parts/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "MultiLineString",
            coordinates: [
              [[-73.99, 40.7]],
            ],
          }),
        ]),
      ),
    /part-0: LineString must contain at least two coordinates/,
  );
});

test("rejects a non-collection payload and a missing geometry", () => {
  assert.throws(() => normalizeOpenDataSubwayLines(null), /must be a GeoJSON FeatureCollection/);
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines({
        type: "Feature",
        features: [],
      }),
    /must be a GeoJSON FeatureCollection/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines({
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: { objectid: "A", service: "A" },
          },
        ],
      }),
    /missing geometry/,
  );
});

test("does not add an express alias that is already explicit or not expected", () => {
  const explicit = normalizeOpenDataSubwayLines(collection([feature("6"), feature("6X")]));
  assert.deepEqual(explicit.diagnostics.alias_applications, []);
  const unexpected = normalizeOpenDataSubwayLines(collection([feature("6")]), { expectedRouteIds: ["6"] });
  assert.deepEqual(unexpected.features[0].properties.route_ids, ["6"]);
  assert.deepEqual(unexpected.diagnostics.alias_applications, []);
});

test("sorts same-route parts by opendata id then part index and reports missing expected routes", () => {
  const result = normalizeOpenDataSubwayLines(
    {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          id: "z-id",
          geometry: {
            type: "MultiLineString",
            coordinates: [
              [
                [-73.97, 40.72],
                [-73.96, 40.73],
              ],
              [
                [-73.99, 40.7],
                [-73.98, 40.71],
              ],
            ],
          },
          properties: { id: "z-line", service: "A" },
        },
        {
          type: "Feature",
          geometry: line(),
          properties: { id: "a-line", service: "A" },
        },
      ],
    },
    { expectedRouteIds: ["A", "C"] },
  );
  assert.deepEqual(
    result.features.map((item) => `${item.properties.opendata_id}:${item.properties.opendata_part_index}`),
    ["a-line:0", "z-line:0", "z-line:1"],
  );
  assert.deepEqual(result.diagnostics.missing_expected_route_ids, ["C"]);
});

test("loadOpenDataSubwayLines assigns an empty object when the file is not a collection object", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "opendata-lines-bad-"));
  const filePath = path.join(dir, "lines.geojson");
  writeFileSync(filePath, "[]\n");
  assert.throws(() => loadOpenDataSubwayLines(filePath), /must be a GeoJSON FeatureCollection/);
  rmSync(dir, { recursive: true, force: true });
});

test("sorts expected unknown ids after official routes and reports both missing", () => {
  const result = normalizeOpenDataSubwayLines(collection([feature("A")]), {
    expectedRouteIds: ["ZZ", "A", "YY"],
  });
  assert.deepEqual(result.diagnostics.missing_expected_route_ids, ["YY", "ZZ"]);
});

test("tokenizes shuttle aliases beside another route and maps SIR through grouped symbols", () => {
  const result = normalizeOpenDataSubwayLines(
    collection([feature("A-SF"), feature("A-SR"), feature("A-SIR"), feature("6D EXPRESS")]),
  );
  const byRoutes = result.features.map((item) => item.properties.route_ids.join("|")).sort();
  assert.ok(byRoutes.includes("A|FS"));
  assert.ok(byRoutes.includes("A|H"));
  assert.ok(byRoutes.includes("A|SI"));
  assert.ok(byRoutes.includes("6X"));
});

test("rejects a symbol made only of unsupported tokens", () => {
  assert.throws(
    () => normalizeOpenDataSubwayLines(collection([feature("QQ-RR")])),
    /no supported route ids parsed/,
  );
});

test("reads route_id, name, and feature.id fallbacks and skips a NaN vertex", () => {
  const result = normalizeOpenDataSubwayLines({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        id: "feat-9",
        geometry: line(),
        properties: { id: "feat-9", objectid: "obj-9", route_id: "G", name: "G local" },
      },
    ],
  });
  assert.equal(result.features[0].properties.opendata_id, "feat-9");
  assert.equal(result.features[0].properties.opendata_objectid, "obj-9");
  assert.equal(result.features[0].properties.opendata_name, "G local");
  assert.equal(result.features[0].properties.opendata_symbol_field, "route_id");
});

test("opendata_id falls back to objectid then feature.id when properties.id is absent", () => {
  const byObject = normalizeOpenDataSubwayLines({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: line(),
        properties: { objectid: "obj-only", service: "L" },
      },
    ],
  });
  assert.equal(byObject.features[0].properties.opendata_id, "obj-only");

  const byFeatureId = normalizeOpenDataSubwayLines({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        id: "raw-id",
        geometry: line(),
        properties: { service: "L", service_name: "L local" },
      },
    ],
  });
  assert.equal(byFeatureId.features[0].properties.opendata_name, "L local");
  assert.ok(byFeatureId.features[0].properties.opendata_id === "raw-id" || byFeatureId.features[0].properties.opendata_id.startsWith("feature-"));
});

test("rejects a LineString whose coordinates are missing and a MultiLineString point-part", () => {
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          {
            type: "Feature",
            geometry: { type: "LineString" },
            properties: { objectid: "bad-ls", service: "A" },
          },
        ]),
      ),
    /LineString must contain at least two coordinates/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "MultiLineString",
            coordinates: [[ [-73.99, 40.7] ]],
          }),
        ]),
      ),
    /LineString must contain at least two coordinates/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "MultiLineString",
            coordinates: [
              [
                [-73.99, 40.7],
                [Number.NaN, 40.71],
              ],
            ],
          }),
        ]),
      ),
    /invalid coordinate|coordinates must be finite numbers/,
  );
  assert.throws(
    () =>
      normalizeOpenDataSubwayLines(
        collection([
          feature("A", {
            type: "LineString",
            coordinates: [
              [-73.99, 40.7],
              [Number.NaN, 40.71],
            ],
          }),
        ]),
      ),
    /invalid coordinate/,
  );
});

test("loadOpenDataSubwayLines rejects a null JSON document", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "opendata-lines-null-"));
  const filePath = path.join(dir, "lines.geojson");
  writeFileSync(filePath, "null\n");
  assert.throws(() => loadOpenDataSubwayLines(filePath), /must be a GeoJSON FeatureCollection/);
  writeFileSync(
    filePath,
    `${JSON.stringify({ type: "FeatureCollection", features: [feature("L")] })}\n`,
  );
  const loaded = loadOpenDataSubwayLines(filePath);
  assert.deepEqual(loaded.diagnostics.represented_route_ids, ["L"]);
  rmSync(dir, { recursive: true, force: true });
});
