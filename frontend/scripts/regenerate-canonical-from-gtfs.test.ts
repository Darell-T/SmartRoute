import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildCanonicalFeatureCollection,
  normalizeColor,
  normalizeRouteId,
  parseCsv,
  sha256,
  validateFeatureCollection,
} from "./regenerate-canonical-from-gtfs.ts";

test("normalizes route aliases and route colors", () => {
  assert.equal(normalizeRouteId("6D"), "6X");
  assert.equal(normalizeRouteId("7d"), "7X");
  assert.equal(normalizeRouteId("FD"), "FX");
  assert.equal(normalizeRouteId("SIR"), "SI");
  assert.equal(normalizeRouteId("fs"), "S");

  assert.equal(normalizeColor("0039a6", "A"), "#0039A6");
  assert.equal(normalizeColor("#ee352e", "1"), "#EE352E");
  assert.equal(normalizeColor("", "unknown-route"), "#A7A9AC");
});

test("parseCsv handles quoted commas, escaped quotes, and CRLF", () => {
  const rows = parseCsv('route_id,route_short_name,route_desc\r\nA,A,"8 Av, express"\r\nB,B,"He said ""go"""\r\n');
  assert.deepEqual(rows, [
    { route_id: "A", route_short_name: "A", route_desc: "8 Av, express" },
    { route_id: "B", route_short_name: "B", route_desc: 'He said "go"' },
  ]);
});

test("buildCanonicalFeatureCollection filters, sorts, dedupes, and preserves route metadata", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: [
      { route_id: "A", route_short_name: "A", route_color: "0039A6" },
      { route_id: "6D", route_short_name: "6D", route_color: "EE352E" },
    ],
    tripsRows: [
      { route_id: "A", shape_id: "shape-a" },
      { route_id: "A", shape_id: "shape-a-duplicate" },
      { route_id: "6D", shape_id: "shape-6d" },
      { route_id: "missing-shape-route", shape_id: "" },
    ],
    shapesRows: [
      { shape_id: "shape-a", shape_pt_lat: "40.7000", shape_pt_lon: "-73.9900", shape_pt_sequence: "2" },
      { shape_id: "shape-a", shape_pt_lat: "40.7100", shape_pt_lon: "-73.9800", shape_pt_sequence: "3" },
      { shape_id: "shape-a", shape_pt_lat: "41.5000", shape_pt_lon: "-73.9800", shape_pt_sequence: "1" },
      { shape_id: "shape-a-duplicate", shape_pt_lat: "40.700001", shape_pt_lon: "-73.990001", shape_pt_sequence: "1" },
      { shape_id: "shape-a-duplicate", shape_pt_lat: "40.710001", shape_pt_lon: "-73.980001", shape_pt_sequence: "2" },
      { shape_id: "shape-6d", shape_pt_lat: "40.7200", shape_pt_lon: "-73.9700", shape_pt_sequence: "1" },
      { shape_id: "shape-6d", shape_pt_lat: "40.7300", shape_pt_lon: "-73.9600", shape_pt_sequence: "2" },
    ],
  });

  assert.equal(collection.type, "FeatureCollection");
  assert.equal(collection.metadata.dedupe.input_features, 3);
  assert.equal(collection.metadata.dedupe.dropped_features, 1);
  assert.deepEqual(
    collection.features.map((feature) => feature.properties.route_id),
    ["6X", "A"],
  );

  const express = collection.features[0];
  assert.equal(express.properties.display_route, "6X");
  assert.equal(express.properties.color, "#EE352E");
  assert.deepEqual(express.geometry.coordinates, [
    [-73.97, 40.72],
    [-73.96, 40.73],
  ]);

  const aTrain = collection.features[1];
  assert.equal(aTrain.properties.color, "#0039A6");
  assert.deepEqual(aTrain.geometry.coordinates, [
    [-73.99, 40.7],
    [-73.98, 40.71],
  ]);
});

test("validateFeatureCollection rejects incomplete route coverage before writing", () => {
  const collection = buildCanonicalFeatureCollection({
    routesRows: [{ route_id: "A", route_short_name: "A", route_color: "0039A6" }],
    tripsRows: [{ route_id: "A", shape_id: "shape-a" }],
    shapesRows: [
      { shape_id: "shape-a", shape_pt_lat: "40.7000", shape_pt_lon: "-73.9900", shape_pt_sequence: "1" },
      { shape_id: "shape-a", shape_pt_lat: "40.7100", shape_pt_lon: "-73.9800", shape_pt_sequence: "2" },
    ],
  });

  assert.throws(
    () => validateFeatureCollection(collection),
    /Expected routes missing from canonical output:/,
  );
});

test("sha256 is deterministic for a GTFS zip buffer", () => {
  assert.equal(
    sha256(Buffer.from("fixture-gtfs-zip")),
    "7df32c220de9142cf892f76902042296007a88987ec856da757b29a337732ffd",
  );
});
