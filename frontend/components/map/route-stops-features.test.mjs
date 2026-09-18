import assert from "node:assert/strict";
import test from "node:test";

import polyline from "@mapbox/polyline";

import {
  buildRouteStopFeatures,
  buildTransitPathFeatures,
  buildWalkFeatures,
  interpolateAlongLine,
} from "./route-stops-features.ts";
import {
  applyDarkMapTheme,
  artifactUrl,
  canonicalWaypointCoordinates,
  journeyFitCoordinates,
  mapFeatureArrayProperty,
} from "../smart-route/map/smart-route-map-helpers.ts";

const encodedLine = polyline.encode([
  [0, 0],
  [0, 10],
]);
const encodedPoint = polyline.encode([[2, 3]]);

function fakeMap(layers) {
  const layout = new Map();
  const paint = [];
  return {
    getStyle: () => ({ layers }),
    setLayoutProperty: (id, property, value) => layout.set(`${id}:${property}`, value),
    setPaintProperty: (id, property, value) => paint.push({ id, property, value }),
    layout,
    paint,
  };
}

test("interpolateAlongLine handles missing, zero-length, endpoint, and midpoint coordinates", () => {
  assert.deepEqual(interpolateAlongLine([], 0.5), [0, 0]);
  assert.deepEqual(interpolateAlongLine([[3, 4]], 0.5), [3, 4]);
  assert.deepEqual(interpolateAlongLine([[3, 4], [3, 4]], 0.5), [3, 4]);
  assert.deepEqual(interpolateAlongLine([[0, 0], [10, 0]], 1), [10, 0]);
  assert.deepEqual(interpolateAlongLine([[0, 0], [10, 0]], 0.25), [2.5, 0]);
  assert.deepEqual(interpolateAlongLine([[0, 0], [10, 0]], 2), [10, 0]);
});

test("buildTransitPathFeatures keeps valid transit geometry and default styling", () => {
  assert.deepEqual(buildTransitPathFeatures(undefined).features, []);

  const result = buildTransitPathFeatures([
    { type: "WALK", polyline: { encodedPolyline: encodedLine } },
    { type: "BUS" },
    { type: "BUS", polyline: { encodedPolyline: encodedPoint } },
    {
      type: "BUS",
      line_color: "#123456",
      polyline: { encodedPolyline: encodedLine },
    },
    { type: "BUS", polyline: { encodedPolyline: encodedLine } },
    { type: "SUBWAY", polyline: { encodedPolyline: encodedLine } },
  ]);

  assert.equal(result.features.length, 3);
  assert.deepEqual(
    result.features.map((feature) => feature.properties),
    [
      { color: "#123456", width: 5 },
      { color: "#0057B8", width: 5 },
      { color: "#8B939E", width: 6 },
    ],
  );
  assert.deepEqual(result.features[0].geometry.coordinates, [[0, 0], [10, 0]]);
});

test("buildWalkFeatures returns only walk lines with at least two coordinates", () => {
  assert.deepEqual(buildWalkFeatures(undefined).features, []);

  const result = buildWalkFeatures([
    { type: "BUS", polyline: { encodedPolyline: encodedLine } },
    { type: "WALK" },
    { type: "WALK", polyline: { encodedPolyline: encodedPoint } },
    { type: "WALK", polyline: { encodedPolyline: encodedLine } },
  ]);

  assert.equal(result.features.length, 1);
  assert.deepEqual(result.features[0], {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [[0, 0], [10, 0]] },
    properties: {},
  });
});

test("buildRouteStopFeatures keeps valid located stops and snaps them onto route geometry", () => {
  const result = buildRouteStopFeatures([
    {
      type: "SUBWAY",
      train_line: "q",
      line_color: "#fccc0a",
      polyline: { encodedPolyline: encodedLine },
      intermediate_stop_locations: [
        { name: "First", lat: 2, lng: -3 },
        { name: "Middle", lat: 2, lng: 5 },
        { name: "Invalid", lat: "2", lng: 7 },
        { name: "Last", lat: -2, lng: 13 },
      ],
    },
  ]);

  assert.equal(result.features.length, 3);
  assert.deepEqual(
    result.features.map((feature) => feature.geometry.coordinates),
    [[0, 0], [5, 0], [10, 0]],
  );
  assert.deepEqual(
    result.features.map((feature) => feature.properties),
    [
      { name: "", color: "#fccc0a", line: "Q", interpolated: false },
      { name: "Middle", color: "#fccc0a", line: "Q", interpolated: false },
      { name: "", color: "#fccc0a", line: "Q", interpolated: false },
    ],
  );
});

test("buildRouteStopFeatures uses raw located coordinates without a polyline", () => {
  const result = buildRouteStopFeatures([
    {
      type: "BUS",
      route_id: "b63",
      intermediate_stop_locations: [{ name: "Church Av", lat: 40.65, lng: -73.97 }],
    },
  ]);

  assert.deepEqual(result.features[0].geometry.coordinates, [-73.97, 40.65]);
  assert.deepEqual(result.features[0].properties, {
    name: "",
    color: "#0057B8",
    line: "B63",
    interpolated: false,
  });
});

test("buildRouteStopFeatures interpolates named stops and clears endpoint names", () => {
  const result = buildRouteStopFeatures([
    {
      type: "SUBWAY",
      route_id: "N",
      polyline: { encodedPolyline: encodedLine },
      intermediate_stops: ["Origin", "Canal St", "Destination"],
    },
  ]);

  assert.deepEqual(
    result.features.map((feature) => feature.geometry.coordinates),
    [[0, 0], [5, 0], [10, 0]],
  );
  assert.deepEqual(
    result.features.map((feature) => feature.properties.name),
    ["", "Canal St", ""],
  );
  assert.ok(result.features.every((feature) => feature.properties.interpolated));
});

test("buildRouteStopFeatures derives counted dots and rejects empty stop data", () => {
  const counted = buildRouteStopFeatures([
    {
      type: "BUS",
      route_id: "M15",
      stop_count: 2,
      polyline: { encodedPolyline: encodedLine },
    },
  ]);
  const empty = buildRouteStopFeatures([
    { type: "WALK", polyline: { encodedPolyline: encodedLine } },
    {
      type: "SUBWAY",
      route_id: "Q",
      stop_count: 0,
      intermediate_stops: [],
      polyline: { encodedPolyline: encodedLine },
    },
    { type: "SUBWAY", route_id: "N" },
  ]);

  assert.equal(counted.features.length, 3);
  assert.deepEqual(
    counted.features.map((feature) => feature.geometry.coordinates),
    [[0, 0], [5, 0], [10, 0]],
  );
  assert.ok(counted.features.every((feature) => feature.properties.name === ""));
  assert.deepEqual(empty.features, []);
});

test("applyDarkMapTheme classifies fill aliases and skips unrelated layers", () => {
  const map = fakeMap([
    { id: "cemetery-area", type: "fill" },
    { id: "wood-area", type: "fill" },
    { id: "ocean-area", type: "fill" },
    { id: "river-area", type: "fill" },
    { id: "sand-area", type: "fill" },
    { id: "landuse-area", type: "fill" },
    { id: "building", type: "fill" },
    { id: "rail-line", type: "line" },
    { id: "station-circle", type: "circle" },
    { id: "place-label", type: "symbol" },
  ]);

  applyDarkMapTheme(map);

  for (const id of ["cemetery-area", "wood-area"]) {
    assert.ok(map.paint.some((paint) => paint.id === id && paint.value === "#1C4327"));
  }
  for (const id of ["ocean-area", "river-area"]) {
    assert.ok(map.paint.some((paint) => paint.id === id && paint.value === "#1B3A52"));
  }
  for (const id of ["sand-area", "landuse-area"]) {
    assert.ok(map.paint.some((paint) => paint.id === id && paint.value === "#161E2E"));
  }
  assert.ok(map.paint.some(
    (paint) => paint.id === "place-label" && paint.property === "text-opacity",
  ));
  assert.ok(!map.paint.some(
    (paint) => ["building", "rail-line", "station-circle"].includes(paint.id),
  ));
});

test("journeyFitCoordinates omits absent optional coordinates", () => {
  assert.deepEqual(
    journeyFitCoordinates([[[-73.9, 40.7]]], null, undefined, null),
    [[-73.9, 40.7]],
  );
  assert.deepEqual(journeyFitCoordinates([], null, undefined, undefined), []);
  assert.deepEqual(
    canonicalWaypointCoordinates({ latitude: 40.72, longitude: -73.98 }),
    [-73.98, 40.72],
  );
});

test("map helper parsing filters empty values and leaves unknown artifacts unversioned", () => {
  assert.deepEqual(mapFeatureArrayProperty(["", "Q", 0]), ["Q", "0"]);
  assert.deepEqual(mapFeatureArrayProperty('["","N"]'), ["N"]);
  assert.deepEqual(mapFeatureArrayProperty('{"route":"Q"}'), ['{"route":"Q"}']);
  assert.equal(artifactUrl("unknown.geojson"), "/unknown.geojson");
});
