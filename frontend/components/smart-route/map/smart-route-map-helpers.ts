"use client";

import * as maplibregl from "maplibre-gl/dist/maplibre-gl.mjs";
import { z } from "zod";
import type { Coordinates } from "@/types";
import artifactManifest from "@/lib/artifact-manifest.json";

export const DEBUG_LIVE_MAP = process.env.NODE_ENV !== "production";
const ARTIFACT_VERSIONS = new Map<string, string>(Object.entries(artifactManifest));

export function toLngLat(c: Coordinates): [number, number] {
  return [c.longitude, c.latitude];
}

export function artifactUrl(name: string): string {
  const version = ARTIFACT_VERSIONS.get(name);
  return version ? `/${name}?v=${version}` : `/${name}`;
}

export function parseFeatureCollection<T>(data: T): GeoJSON.FeatureCollection | null {
  if (!(data instanceof Object) || !("type" in data) || !("features" in data)) return null;
  if (data.type !== "FeatureCollection" || !Array.isArray(data.features) || data.features.length === 0) {
    return null;
  }
  // SAFETY: MapLibre sources accept a FeatureCollection; type and features[] were checked above.
  return data as GeoJSON.FeatureCollection;
}

export async function loadVisualSubwayNetworkOrNull(): Promise<GeoJSON.FeatureCollection | null> {
  try {
    const response = await fetch(
      artifactUrl("subway-network.visual.geojson"),
      { cache: "force-cache" },
    );
    if (!response.ok) {
      throw new Error(
        `Failed to load visual subway network: ${response.status} ${response.statusText}`,
      );
    }
    return parseFeatureCollection(await response.json());
  } catch (error) {
    if (DEBUG_LIVE_MAP) {
      // eslint-disable-next-line no-console
      console.warn(
        "[smart-route-map/subway-visual] fetch failed; subway lines will not render",
        error,
      );
    }
    return null;
  }
}

export async function loadSubwayStationAnchorsOrNull(): Promise<GeoJSON.FeatureCollection | null> {
  try {
    const response = await fetch(
      artifactUrl("subway-network.station-anchors.geojson"),
      { cache: "force-cache" },
    );
    if (!response.ok) {
      throw new Error(
        `Failed to load station anchors: ${response.status} ${response.statusText}`,
      );
    }
    return parseFeatureCollection(await response.json());
  } catch (error) {
    if (DEBUG_LIVE_MAP) {
      // eslint-disable-next-line no-console
      console.warn(
        "[smart-route-map/subway-station-anchors] fetch failed; falling back to raw station dots",
        error,
      );
    }
    return null;
  }
}

const mapFeatureArrayPropertySchema = z
  .array(z.unknown())
  .transform((items) => items.map((item) => String(item)).filter(Boolean))
  .or(
    z.string().transform((value) => {
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed)) {
          return parsed.map((item) => String(item)).filter(Boolean);
        }
      } catch {
        // MapLibre may expose string properties as plain comma-separated text.
      }
      return value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    }),
  )
  .catch([]);

export function mapFeatureArrayProperty<T>(value: T): string[] {
  return mapFeatureArrayPropertySchema.parse(value);
}

export function firstSymbolLayerId(m: maplibregl.Map) {
  return m.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
}

const ROAD_LAYER = /road|street|tunnel|bridge|motorway|trunk|primary|secondary|tertiary/i;
const WATER_LAYER = /water|ocean|river|bay/i;
const PARK_LAYER = /park|wood|grass|forest|cemetery/i;
const LAND_LAYER = /land|landuse|sand/i;
const POI_LAYER = /poi/i;

function fillThemeActions(id: string): {
  paint: NonNullable<maplibregl.PaintPropertyEntry>[];
} | null {
  if (WATER_LAYER.test(id)) {
    return { paint: [{ name: "fill-color", value: "#1B3A52" }] };
  }
  if (PARK_LAYER.test(id)) {
    return {
      paint: [
        { name: "fill-color", value: "#1C4327" },
        { name: "fill-opacity", value: 0.72 },
      ],
    };
  }
  if (LAND_LAYER.test(id)) {
    return {
      paint: [
        { name: "fill-color", value: "#161E2E" },
        { name: "fill-opacity", value: 0.66 },
      ],
    };
  }
  return null;
}

function darkThemeActions(layer: { id: string; type: string }): {
  hide?: boolean;
  paint: NonNullable<maplibregl.PaintPropertyEntry>[];
} | null {
  const { id, type } = layer;
  if (type === "line" && ROAD_LAYER.test(id)) {
    return {
      paint: [
        { name: "line-color", value: "#2B3A4D" },
        { name: "line-opacity", value: 0.55 },
      ],
    };
  }
  if (type === "fill") return fillThemeActions(id);
  if (type === "background") {
    return { paint: [{ name: "background-color", value: "#0D1220" }] };
  }
  if (type !== "symbol") return null;
  if (POI_LAYER.test(id)) return { hide: true, paint: [] };
  return {
    paint: [
      { name: "text-opacity", value: 0.55 },
      { name: "text-halo-color", value: "#05070A" },
    ],
  };
}

export function applyDarkMapTheme(mapInstance: maplibregl.Map): void {
  for (const layer of mapInstance.getStyle().layers ?? []) {
    const actions = darkThemeActions(layer);
    if (!actions) continue;
    try {
      if (actions.hide) {
        mapInstance.setLayoutProperty(layer.id, "visibility", "none");
        continue;
      }
      for (const paint of actions.paint) {
        mapInstance.setPaintProperty(layer.id, paint.name, paint.value);
      }
    } catch {
      // This CARTO style revision lacks the targeted property; skip it.
    }
  }
}

export function canonicalWaypointCoordinates(waypoint: {
  lat?: number | null;
  lng?: number | null;
  latitude?: number | null;
  longitude?: number | null;
}): [number, number] | null {
  const lat = waypoint.lat ?? waypoint.latitude;
  const lng = waypoint.lng ?? waypoint.longitude;
  return lat != null && Number.isFinite(lat) &&
    lng != null && Number.isFinite(lng)
    ? [lng, lat]
    : null;
}

export function journeyFitCoordinates(
  stepCoords: [number, number][][],
  origin: [number, number] | null,
  waypoints: Array<{
    lat?: number | null;
    lng?: number | null;
    latitude?: number | null;
    longitude?: number | null;
  }> | undefined,
  destCoords: { lat: number; lng: number } | null | undefined,
): [number, number][] {
  const fitCoords = stepCoords.flat();
  if (origin) fitCoords.push(origin);
  for (const waypoint of waypoints ?? []) {
    const point = canonicalWaypointCoordinates(waypoint);
    if (point) fitCoords.push(point);
  }
  if (destCoords) fitCoords.push([destCoords.lng, destCoords.lat]);
  return fitCoords;
}
