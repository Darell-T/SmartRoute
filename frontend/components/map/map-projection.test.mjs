import assert from "node:assert/strict";
import test from "node:test";
import polyline from "@mapbox/polyline";

import { ensureBuildingsLayer, BUILDINGS_LAYER_ID } from "./buildings-layer.ts";
import { flyToRoute } from "./camera.ts";
import { buildTrips, getLineColor } from "./route-layers.ts";
import {
  buildSubwayLaneFeaturesFromVisual,
  buildSubwayStopFeatures,
  emptySubwayStationMarkerCollections,
  ensureSubwayNetworkLayers,
  setSubwayNetworkHidden,
  splitStationAnchorFeatureCollections,
  stationMarkerRouteIds,
  summarizeVisualLanes,
} from "./subway-network.ts";
import {
  bringRouteStopsToTop,
  clearRouteStopData,
  ensureRouteStopLayers,
  setRouteStopData,
} from "./route-stops.ts";
import {
  buildRouteStopFeatures,
  buildTransitPathFeatures,
  buildWalkFeatures,
  interpolateAlongLine,
} from "./route-stops-features.ts";
import { addStationBadge, clearBadges } from "./station-badges.ts";
import maplibregl from "maplibre-gl";

function fakeMap(width = 1440) {
  const sources = new Map();
  const layers = new Map();
  const layout = new Map();
  const boundsCalls = [];
  return {
    getSource: (id) => sources.get(id),
    addSource: (id, spec) => sources.set(id, { ...spec, setData() {} }),
    getLayer: (id) => layers.get(id),
    addLayer: (layer, beforeId) => {
      layers.set(layer.id, { ...layer, beforeId });
    },
    moveLayer: () => {},
    getContainer: () => ({ clientWidth: width }),
    fitBounds: (bounds, options) => boundsCalls.push({ bounds, options }),
    boundsCalls,
    getCanvas: () => ({ style: {} }),
    getStyle: () => ({ layers: [...layers.values()] }),
    setPaintProperty: () => {},
    getLayoutProperty: (id, property) => layout.get(`${id}:${property}`),
    setLayoutProperty: (id, property, value) => layout.set(`${id}:${property}`, value),
    layout,
  };
}

test("getLineColor uses official MTA colors", () => {
  assert.equal(getLineColor("Q").toUpperCase(), "#FCCC0A");
});

test("buildTrips keeps walk and subway geometry in step order", () => {
  const encoded = polyline.encode([
    [40.7, -73.99],
    [40.71, -73.98],
  ]);
  const built = buildTrips([
    { type: "WALK", polyline: { encodedPolyline: encoded } },
    { type: "SUBWAY", train_line: "Q", polyline: { encodedPolyline: encoded } },
  ]);
  assert.equal(built.stepCoords.length, 2);
  assert.ok(built.trips.length >= 1);
});

test("buildSubwayLaneFeaturesFromVisual emits one color lane per corridor", () => {
  const lanes = buildSubwayLaneFeaturesFromVisual({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: {
          route_ids: ["Q"],
          from_stop_name: "Church Av",
          to_stop_name: "Times Sq",
        },
        geometry: {
          type: "LineString",
          coordinates: [
            [-73.99, 40.7],
            [-73.98, 40.71],
          ],
        },
      },
      {
        type: "Feature",
        properties: {
          visual_feature_type: "bundle_lane",
          route_ids: ["N", "Q"],
          route_id: "Q",
          color: "#FCCC0A",
          lane_slot: 1,
          lane_offset_baked: true,
          from_stop_name: "Canal St",
          to_stop_name: "Union Sq",
        },
        geometry: {
          type: "LineString",
          coordinates: [
            [-74.0, 40.72],
            [-73.99, 40.73],
          ],
        },
      },
    ],
  });
  assert.ok(lanes.features.length >= 2);
  assert.equal(lanes.features[0].geometry.type, "LineString");
  const bundle = lanes.features.find((feature) => feature.properties.visual_feature_type === "bundle_lane");
  assert.ok(bundle);
  assert.equal(bundle.properties.lane_slot, 0);
  assert.match(bundle.properties.stop_pair, /Canal St/);
  const skipped = buildSubwayLaneFeaturesFromVisual({
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: { route_ids: ["Q"] }, geometry: { type: "Point", coordinates: [-73.99, 40.7] } },
      { type: "Feature", properties: { route_ids: ["Q"] }, geometry: { type: "LineString", coordinates: [[-73.99, 40.7]] } },
      { type: "Feature", properties: { route_ids: [] }, geometry: { type: "LineString", coordinates: [[-73.99, 40.7], [-73.98, 40.71]] } },
      {
        type: "Feature",
        properties: { route_ids: ["A", "Q"], lane_color_slots: { "#EE352E": 0, "#FCCC0A": 1 }, lane_order_basis: ["A", "Q"] },
        geometry: { type: "LineString", coordinates: [[-74.1, 40.6], [-74.2, 40.5]] },
      },
    ],
  });
  assert.ok(skipped.features.some((feature) => feature.properties.route_ids.includes("A")));
});

test("ensureBuildingsLayer is a no-op without a MapTiler key", () => {
  const previous = process.env.NEXT_PUBLIC_MAPTILER_API_KEY;
  delete process.env.NEXT_PUBLIC_MAPTILER_API_KEY;
  const map = fakeMap();
  ensureBuildingsLayer(map);
  assert.equal(map.getLayer(BUILDINGS_LAYER_ID), undefined);
  if (previous !== undefined) process.env.NEXT_PUBLIC_MAPTILER_API_KEY = previous;
});

test("ensureBuildingsLayer installs and reorders fill-extrusion when a key is set", () => {
  const previous = process.env.NEXT_PUBLIC_MAPTILER_API_KEY;
  process.env.NEXT_PUBLIC_MAPTILER_API_KEY = "test-key";
  const map = fakeMap();
  map.addLayer({ id: "subway-glow" });
  ensureBuildingsLayer(map, "subway-glow");
  assert.ok(map.getSource("sr-buildings-src"));
  assert.ok(map.getLayer(BUILDINGS_LAYER_ID));
  ensureBuildingsLayer(map, "subway-glow");
  if (previous === undefined) delete process.env.NEXT_PUBLIC_MAPTILER_API_KEY;
  else process.env.NEXT_PUBLIC_MAPTILER_API_KEY = previous;
});

test("flyToRoute ignores empty coordinates and stays a flat 2D fit", () => {
  const map = fakeMap();
  flyToRoute(map, []);
  assert.equal(map.boundsCalls.length, 0);
  flyToRoute(map, [[-73.99, 40.7], [-73.98, 40.71]]);
  assert.equal(map.boundsCalls.length, 1);
  assert.equal(map.boundsCalls[0].options.pitch, 0);
  assert.equal(map.boundsCalls[0].options.easing(1), 1);
  assert.ok(map.boundsCalls[0].options.easing(0.5) > 0.5);
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  globalThis.window = {
    innerWidth: 390,
    matchMedia: () => ({ matches: true }),
    getComputedStyle: () => ({ getPropertyValue: () => "180" }),
  };
  globalThis.document = { documentElement: {} };
  const mobile = fakeMap(390);
  flyToRoute(mobile, [[-73.99, 40.7], [-73.98, 40.71]], { duration: 400 });
  assert.equal(mobile.boundsCalls[0].options.duration, 0);
  assert.equal(mobile.boundsCalls[0].options.padding.bottom, 212);
  globalThis.window = previousWindow;
  globalThis.document = previousDocument;
});

test("route preview markers create a current-location element", async () => {
  const created = [];
  globalThis.document = {
    getElementById: () => null,
    createElement(tag) {
      const el = {
        id: "",
        className: "",
        innerHTML: "",
        textContent: "",
        dataset: {},
        style: { cssText: "", setProperty() {} },
        setAttribute() {},
        appendChild() {},
      };
      created.push({ tag, el });
      return el;
    },
    head: { appendChild() {} },
  };
  const { createCurrentLocationDot, createDestinationPin, createWaypointMarker } = await import(
    "../smart-route/map/route-preview-markers.ts"
  );
  const dot = createCurrentLocationDot();
  assert.equal(dot.className, "sr-current-location-marker");
  const pin = createDestinationPin();
  assert.ok(pin);
  const waypoint = createWaypointMarker("Jay St", 2);
  assert.ok(waypoint);
});

test("route stop layer helpers are idempotent on a fake map", () => {
  const encoded = polyline.encode([
    [40.7, -73.99],
    [40.71, -73.98],
  ]);
  const map = fakeMap();
  ensureRouteStopLayers(map);
  ensureRouteStopLayers(map);
  setRouteStopData(map, [
    { type: "WALK", polyline: { encodedPolyline: encoded } },
    { type: "SUBWAY", train_line: "Q", polyline: { encodedPolyline: encoded } },
  ]);
  bringRouteStopsToTop(map);
  clearRouteStopData(map);
  clearBadges([]);
});

test("addStationBadge mounts a subway chip and clearBadges removes it", () => {
  const previousDocument = globalThis.document;
  globalThis.document = {
    createElement(tag) {
      return {
        tagName: tag,
        style: { cssText: "" },
        innerHTML: "",
      };
    },
  };
  const previousMarker = maplibregl.Marker;
  const removed = [];
  maplibregl.Marker = class {
    constructor(opts) {
      this.element = opts.element;
      this.anchor = opts.anchor;
    }
    setLngLat(coords) {
      this.coords = coords;
      return this;
    }
    addTo() {
      return this;
    }
    remove() {
      removed.push(this);
    }
  };
  const marker = addStationBadge(
    fakeMap(),
    [-73.99, 40.7],
    "Jay St",
    "Q",
    "#FCCC0A",
    0,
    true,
  );
  assert.equal(marker.anchor, "bottom");
  const bus = addStationBadge(
    fakeMap(),
    [-73.99, 40.7],
    "Jay St",
    "B54",
    "#FF6319",
    1,
    false,
  );
  assert.equal(bus.anchor, "top");
  const bag = [marker, bus];
  clearBadges(bag);
  assert.equal(removed.length, 2);
  assert.equal(bag.length, 0);
  maplibregl.Marker = previousMarker;
  globalThis.document = previousDocument;
});

test("subway stop features, anchors, and ambient layers install on a fake map", () => {
  const lanes = buildSubwayLaneFeaturesFromVisual({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { route_ids: ["Q"] },
        geometry: {
          type: "LineString",
          coordinates: [
            [-73.99, 40.7],
            [-73.98, 40.71],
          ],
        },
      },
    ],
  });
  const summary = summarizeVisualLanes(lanes);
  assert.ok(summary.renderFeatures >= 1);
  assert.ok(summary.distinctRoutes >= 1);
  const stops = buildSubwayStopFeatures({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: {
          station_id: "R30",
          name: "Jay St",
          route_ids: ["A", "C"],
          is_transfer: true,
        },
        geometry: { type: "Point", coordinates: [-73.99, 40.69] },
      },
    ],
  });
  assert.equal(stops.features.length, 2);
  const markers = splitStationAnchorFeatureCollections({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { marker_type: "single_stop_dot", route_ids: ["Q"] },
        geometry: { type: "Point", coordinates: [-73.99, 40.7] },
      },
      {
        type: "Feature",
        properties: { marker_type: "station_label" },
        geometry: { type: "Point", coordinates: [-73.99, 40.7] },
      },
      {
        type: "Feature",
        properties: { marker_type: "station_route_badge", route_id: "Q" },
        geometry: { type: "Point", coordinates: [-73.99, 40.7] },
      },
    ],
  });
  assert.equal(markers.dots.features.length, 1);
  assert.equal(markers.labels.features.length, 1);
  assert.ok(stationMarkerRouteIds(markers).includes("Q"));
  const empty = emptySubwayStationMarkerCollections();
  assert.equal(empty.dots.features.length, 0);
  const map = fakeMap();
  ensureSubwayNetworkLayers(map, undefined, lanes, stops, markers);
  ensureSubwayNetworkLayers(map, undefined, lanes, stops, markers);
  setSubwayNetworkHidden(map, true);
  setSubwayNetworkHidden(map, false);
});

test("route stop builders snap located stops and interpolate names along the polyline", () => {
  const encoded = polyline.encode([
    [40.7, -73.99],
    [40.71, -73.98],
    [40.72, -73.97],
  ]);
  const walk = { type: "WALK", polyline: { encodedPolyline: encoded } };
  const located = {
    type: "SUBWAY",
    train_line: "Q",
    polyline: { encodedPolyline: encoded },
    intermediate_stop_locations: [
      { name: "DeKalb", lat: 40.705, lng: -73.985 },
      { name: "Canal", lat: "bad", lng: -73.98 },
    ],
  };
  const named = {
    type: "SUBWAY",
    train_line: "B",
    polyline: { encodedPolyline: encoded },
    intermediate_stops: ["Pacific", "DeKalb", "Canal"],
  };
  const counted = {
    type: "BUS",
    route_id: "B54",
    line_color: "#0057B8",
    polyline: { encodedPolyline: encoded },
    stop_count: 3,
  };
  const paths = buildTransitPathFeatures([walk, located, counted]);
  assert.equal(paths.features.length, 2);
  assert.equal(paths.features[1].properties.width, 5);
  const stops = buildRouteStopFeatures([walk, located, named, counted]);
  assert.ok(stops.features.length >= 3);
  assert.equal(stops.features[0].properties.name, "");
  const walks = buildWalkFeatures([walk, located, { type: "WALK" }]);
  assert.equal(walks.features.length, 1);
  const [lng, lat] = interpolateAlongLine(
    [
      [-73.99, 40.7],
      [-73.98, 40.71],
    ],
    0.5,
  );
  assert.ok(Number.isFinite(lng) && Number.isFinite(lat));
  assert.deepEqual(interpolateAlongLine([[-73.99, 40.7]], 0.4), [-73.99, 40.7]);
  assert.deepEqual(buildTransitPathFeatures(undefined).features, []);
  assert.deepEqual(buildRouteStopFeatures(undefined).features, []);
  assert.deepEqual(buildWalkFeatures(undefined).features, []);
});
