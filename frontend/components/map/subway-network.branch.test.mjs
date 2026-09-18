import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSubwayLaneFeaturesFromVisual,
  buildSubwayStopFeatures,
  emptySubwayStationMarkerCollections,
  ensureSubwayNetworkLayers,
  stationMarkerRouteIds,
  summarizeVisualLanes,
} from "./subway-network.ts";

function fakeMap() {
  const sources = new Map();
  const layers = new Map();
  const layout = new Map();
  return {
    getSource: (id) => sources.get(id),
    addSource: (id, spec) => sources.set(id, { ...spec, setData() {} }),
    getLayer: (id) => layers.get(id),
    addLayer: (layer, beforeId) => {
      layers.set(layer.id, { ...layer, beforeId });
    },
    moveLayer: () => {},
    getContainer: () => ({ clientWidth: 1440 }),
    getCanvas: () => ({ style: {} }),
    getStyle: () => ({ layers: [...layers.values()] }),
    setPaintProperty: () => {},
    getLayoutProperty: (id, property) => layout.get(`${id}:${property}`),
    setLayoutProperty: (id, property, value) => layout.set(`${id}:${property}`, value),
    layout,
  };
}

function lineFeature(properties, coordinates) {
  return {
    type: "Feature",
    properties,
    geometry: { type: "LineString", coordinates },
  };
}

test("unknown colors, southbound lanes, and unbaked slots stay rider-visible", () => {
  const lanes = buildSubwayLaneFeaturesFromVisual({
    type: "FeatureCollection",
    features: [
      lineFeature(
        { route_ids: ["Q"], corridor_id: "c1" },
        [[-73.99, 40.7], [-73.98, 40.71]],
      ),
      lineFeature(
        {
          visual_feature_type: "bundle_lane",
          route_ids: ["FOO"],
          color: "#123456",
          corridor_id: "c1",
          from_stop_name: "South",
          to_stop_name: "North",
        },
        [[-73.99, 40.8], [-73.985, 40.6]],
      ),
      lineFeature(
        {
          visual_feature_type: "bundle_lane",
          route_ids: ["N", "Q"],
          route_id: "Q",
          lane_offset_baked: false,
          corridor_id: "c2",
          color_route_ids: ["Q"],
        },
        [[-74.0, 40.72], [-73.99, 40.73]],
      ),
      lineFeature(
        {
          route_ids: ["A", "C"],
          corridor_id: "c3",
          lane_slot_source: "chain",
          lane_color_slots: { "#0A84FF": 0 },
          longest_member_length_m: 120,
        },
        [[-74.01, 40.74], [-74.0, 40.75]],
      ),
    ],
  });
  const unknown = lanes.features.find((feature) => feature.properties.color === "#123456");
  assert.ok(unknown);
  assert.equal(unknown.properties.visual_z_order, 100);
  assert.equal(unknown.properties.lane_slot, 0);
  assert.deepEqual(unknown.geometry.coordinates[0], [-73.985, 40.6]);
  assert.match(unknown.properties.stop_pair, /South/);

  const unbaked = lanes.features.find((feature) => feature.properties.color_route_ids?.includes("Q") && feature.properties.visual_feature_type === "bundle_lane");
  assert.equal(unbaked.properties.lane_slot, 0);
  assert.equal(unbaked.properties.representative_route_id, "Q");

  const chained = lanes.features.find((feature) => feature.properties.lane_slot_source === "chain");
  assert.ok(chained);
  assert.equal(chained.properties.length_m, 120);

  const summary = summarizeVisualLanes(lanes);
  assert.ok(summary.multiColorCorridors >= 1);
  assert.ok(summary.corridorsWithMultipleRoutes >= 1);
});

test("stop features skip non-points and missing route lists", () => {
  const stops = buildSubwayStopFeatures({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { station_id: "R30", route_ids: ["A"] },
        geometry: { type: "LineString", coordinates: [[-73.99, 40.7], [-73.98, 40.71]] },
      },
      {
        type: "Feature",
        properties: { station_id: "R31", route_ids: "A" },
        geometry: { type: "Point", coordinates: [-73.99, 40.69] },
      },
      {
        type: "Feature",
        properties: { station_id: "R32", route_ids: ["C"] },
        geometry: { type: "Point", coordinates: [-73.98, 40.68] },
      },
    ],
  });
  assert.equal(stops.features.length, 1);
  assert.equal(stops.features[0].properties.route_id, "C");
});

test("missing station markers stay empty and still install ambient layers", () => {
  assert.deepEqual(stationMarkerRouteIds(null), []);
  assert.deepEqual(stationMarkerRouteIds(undefined), []);
  const empty = emptySubwayStationMarkerCollections();
  assert.equal(stationMarkerRouteIds(empty).length, 0);

  const lanes = buildSubwayLaneFeaturesFromVisual({
    type: "FeatureCollection",
    features: [
      lineFeature({ route_ids: ["Q"] }, [[-73.99, 40.7], [-73.98, 40.71]]),
    ],
  });
  const map = fakeMap();
  ensureSubwayNetworkLayers(map, undefined, lanes, { type: "FeatureCollection", features: [] });
  assert.ok(map.getSource("sr-subway-network") || map.getLayer("sr-subway-fill") || map.getStyle().layers.length > 0);
});
