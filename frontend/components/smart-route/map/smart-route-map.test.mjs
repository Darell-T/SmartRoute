import assert from "node:assert/strict";
import test from "node:test";

import {
  applyDarkMapTheme,
  artifactUrl,
  canonicalWaypointCoordinates,
  firstSymbolLayerId,
  journeyFitCoordinates,
  loadSubwayStationAnchorsOrNull,
  loadVisualSubwayNetworkOrNull,
  mapFeatureArrayProperty,
  toLngLat,
} from "./smart-route-map-helpers.ts";

function fakeMap(layers) {
  const layout = new Map();
  const paint = [];
  return {
    getStyle: () => ({ layers }),
    getLayer: (id) => layers.find((layer) => layer.id === id),
    setLayoutProperty: (id, property, value) => layout.set(`${id}:${property}`, value),
    setPaintProperty: (id, property, value) => paint.push({ id, property, value }),
    layout,
    paint,
  };
}

test("dark theme paints roads, water, parks, land, and hides POI labels", () => {
  const map = fakeMap([
    { id: "road-primary", type: "line" },
    { id: "water", type: "fill" },
    { id: "park", type: "fill" },
    { id: "landuse", type: "fill" },
    { id: "background", type: "background" },
    { id: "poi-label", type: "symbol" },
    { id: "place-label", type: "symbol" },
  ]);
  applyDarkMapTheme(map);
  assert.equal(map.layout.get("poi-label:visibility"), "none");
  assert.ok(map.paint.some((entry) => entry.id === "road-primary" && entry.property === "line-color"));
  assert.ok(map.paint.some((entry) => entry.id === "water" && entry.value === "#1B3A52"));
  assert.ok(map.paint.some((entry) => entry.id === "park" && entry.value === "#1C4327"));
  assert.ok(map.paint.some((entry) => entry.id === "background" && entry.value === "#0D1220"));
  assert.ok(map.paint.some((entry) => entry.id === "place-label" && entry.property === "text-opacity"));
});

test("journey fit coordinates keep origin, waypoints, and destination", () => {
  const fit = journeyFitCoordinates(
    [[[-73.99, 40.7], [-73.98, 40.71]]],
    [-74.0, 40.69],
    [{ lat: 40.72, lng: -73.97 }, { latitude: null, longitude: null }],
    { lat: 40.73, lng: -73.96 },
  );
  assert.deepEqual(fit.at(0), [-73.99, 40.7]);
  assert.deepEqual(fit.at(-1), [-73.96, 40.73]);
  assert.ok(fit.some((point) => point[0] === -74.0 && point[1] === 40.69));
  assert.equal(canonicalWaypointCoordinates({ lat: "bad" }), null);
});

test("map helpers parse artifact urls and feature properties", () => {
  assert.deepEqual(toLngLat({ longitude: -73.99, latitude: 40.7 }), [-73.99, 40.7]);
  assert.deepEqual(mapFeatureArrayProperty(["Q", "N"]), ["Q", "N"]);
  assert.deepEqual(mapFeatureArrayProperty('["Q","N"]'), ["Q", "N"]);
  assert.deepEqual(mapFeatureArrayProperty("Q, N"), ["Q", "N"]);
  assert.deepEqual(mapFeatureArrayProperty(null), []);
  assert.match(artifactUrl("subway-network.visual.geojson"), /subway-network\.visual\.geojson/);
  const map = fakeMap([{ id: "labels", type: "symbol" }, { id: "roads", type: "line" }]);
  assert.equal(firstSymbolLayerId(map), "labels");
  assert.equal(firstSymbolLayerId(fakeMap([{ id: "roads", type: "line" }])), undefined);
});

test("dark theme skips paint properties the style cannot accept", () => {
  const map = fakeMap([{ id: "water", type: "fill" }]);
  map.setPaintProperty = () => {
    throw new Error("missing property");
  };
  applyDarkMapTheme(map);
});

test("visual network loaders treat empty and failed fetches as absent", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 500, statusText: "err" });
  assert.equal(await loadVisualSubwayNetworkOrNull(), null);
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ type: "FeatureCollection", features: [] }),
  });
  assert.equal(await loadSubwayStationAnchorsOrNull(), null);
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => null,
  });
  assert.equal(await loadVisualSubwayNetworkOrNull(), null);
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ type: "FeatureCollection", features: { length: 2 } }),
  });
  assert.equal(await loadSubwayStationAnchorsOrNull(), null);
  globalThis.fetch = async () => {
    throw new Error("offline");
  };
  assert.equal(await loadVisualSubwayNetworkOrNull(), null);
  globalThis.fetch = original;
});

test("dark theme treats a style with no layers as a no-op", () => {
  const map = fakeMap(undefined);
  map.getStyle = () => ({});
  applyDarkMapTheme(map);
  assert.equal(map.paint.length, 0);
});
