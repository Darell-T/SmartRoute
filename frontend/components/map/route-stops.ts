// MapLibre layers for the picked route: intermediate stop dots + labels
// (collision-handled symbols) and dashed walk segments. Persistent --
// driven purely by step data, unlike the animation-time DOM markers.
// Feature building lives in route-stops-features.ts (pure, node-testable).

import maplibregl from "maplibre-gl";
import type { RouteStep } from "@/types";
import { getLineColor } from "./route-layers";
import {
  buildRouteStopFeatures,
  buildWalkFeatures,
} from "./route-stops-features";

const ROUTE_STOPS_SOURCE_ID = "sr-route-stops";
const ROUTE_WALK_SOURCE_ID = "sr-route-walk";
export const ROUTE_WALK_LINE_LAYER_ID = "sr-route-walk-line";
const ROUTE_STOP_DOT_LAYER_ID = "sr-route-stop-dot";
const ROUTE_STOP_LABEL_LAYER_ID = "sr-route-stop-label";

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

function routeStopColorFor(step: RouteStep): string {
  if (step.type === "BUS") return step.line_color || "#0057B8";
  return step.line_color || getLineColor(step.train_line || step.route_id || "");
}

function ensureSource(map: maplibregl.Map, id: string) {
  if (!map.getSource(id)) {
    map.addSource(id, { type: "geojson", data: EMPTY });
  }
}

/** Idempotent: create sources + layers for route stops and walk dashes.
 *  Call from style.load so they survive style reloads. */
export function ensureRouteStopLayers(map: maplibregl.Map) {
  ensureSource(map, ROUTE_WALK_SOURCE_ID);
  ensureSource(map, ROUTE_STOPS_SOURCE_ID);

  if (!map.getLayer(ROUTE_WALK_LINE_LAYER_ID)) {
    map.addLayer({
      id: ROUTE_WALK_LINE_LAYER_ID,
      type: "line",
      source: ROUTE_WALK_SOURCE_ID,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        // Apple-style dotted walk segment.
        "line-color": "#FFFFFF",
        "line-opacity": 0.85,
        "line-width": 3,
        "line-dasharray": [0.1, 1.8],
      },
    });
  }

  if (!map.getLayer(ROUTE_STOP_DOT_LAYER_ID)) {
    map.addLayer({
      id: ROUTE_STOP_DOT_LAYER_ID,
      type: "circle",
      source: ROUTE_STOPS_SOURCE_ID,
      paint: {
        // White stop dots with a thin dark ring -- they read as clean stop
        // markers sitting on top of the colored route line, and stay legible
        // against both the line and the dark map. Larger than ambient network
        // dots and with no zoom fade so they persist while a route is active.
        "circle-color": "#FFFFFF",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 3, 14, 4.5, 17, 6],
        "circle-stroke-color": "#0A0D13",
        "circle-stroke-width": 1.5,
      },
    });
  }

  if (!map.getLayer(ROUTE_STOP_LABEL_LAYER_ID)) {
    map.addLayer({
      id: ROUTE_STOP_LABEL_LAYER_ID,
      type: "symbol",
      source: ROUTE_STOPS_SOURCE_ID,
      layout: {
        // text-font omitted: inherit the style's default glyphs, same as
        // the ambient station labels. MapLibre's collision handling keeps
        // label density sane; dots remain even where labels drop out.
        "text-field": ["get", "name"],
        "text-size": 10.5,
        "text-anchor": "left",
        "text-offset": [0.8, 0],
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": "rgba(255,255,255,0.92)",
        "text-halo-color": "rgba(10,13,19,0.85)",
        "text-halo-width": 1.4,
      },
    });
  }
}

export function setRouteStopData(map: maplibregl.Map, steps: RouteStep[]) {
  ensureRouteStopLayers(map);
  const stops = map.getSource(ROUTE_STOPS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  const walk = map.getSource(ROUTE_WALK_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  stops?.setData(buildRouteStopFeatures(steps, routeStopColorFor));
  walk?.setData(buildWalkFeatures(steps));
}

export function clearRouteStopData(map: maplibregl.Map) {
  const stops = map.getSource(ROUTE_STOPS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  const walk = map.getSource(ROUTE_WALK_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  stops?.setData(EMPTY);
  walk?.setData(EMPTY);
}

/** The deck.gl overlay is interleaved and inserts its route path layers at
 *  the top of the stack; call this after every deck layer swap so stop
 *  dots/labels stay above the colored path. */
export function bringRouteStopsToTop(map: maplibregl.Map) {
  for (const id of [ROUTE_WALK_LINE_LAYER_ID, ROUTE_STOP_DOT_LAYER_ID, ROUTE_STOP_LABEL_LAYER_ID]) {
    if (map.getLayer(id)) {
      map.moveLayer(id);
    }
  }
}
