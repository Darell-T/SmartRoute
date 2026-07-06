"use client";

import maplibregl from "maplibre-gl";
import { PathLayer, type PathLayerProps } from "@deck.gl/layers";
import type { Coordinates } from "@/types";
import artifactManifest from "@/lib/artifact-manifest.json";
import { type Trip } from "@/components/map/route-layers";
import { ROUTE_WALK_LINE_LAYER_ID } from "@/components/map/route-stops";

export const DEBUG_LIVE_MAP = process.env.NODE_ENV !== "production";

export function toLngLat(c: Coordinates): [number, number] {
  return [c.longitude, c.latitude];
}

export function artifactUrl(name: string): string {
  const version = (artifactManifest as Record<string, string>)[name];
  return version ? `/${name}?v=${version}` : `/${name}`;
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
    const doc = (await response.json()) as GeoJSON.FeatureCollection;
    if (!doc || !Array.isArray(doc.features) || doc.features.length === 0) {
      return null;
    }
    return doc;
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
    const doc = (await response.json()) as GeoJSON.FeatureCollection;
    if (!doc || !Array.isArray(doc.features) || doc.features.length === 0) {
      return null;
    }
    return doc;
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

export function mapFeatureArrayProperty(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  if (typeof value === "string") {
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
  }
  return [];
}

export const ROUTE_PATH_DEPTH_PARAMETERS = {
  depthCompare: "always" as const,
  depthWriteEnabled: false,
};

export function selectedRouteLayers(trips: Trip[]) {
  return trips.map(
    (trip, i) =>
      new PathLayer<Trip>({
        id: `sr-selected-route-${i}`,
        data: [trip],
        getPath: (t) => t.path,
        getColor: (t) => [t.color[0], t.color[1], t.color[2], 255],
        getWidth: (t) => t.width,
        widthUnits: "pixels",
        widthMinPixels: 3,
        opacity: 1,
        capRounded: true,
        jointRounded: true,
        parameters: ROUTE_PATH_DEPTH_PARAMETERS,
        beforeId: ROUTE_WALK_LINE_LAYER_ID,
      } as PathLayerProps<Trip> & { beforeId: string }),
  );
}

export function firstSymbolLayerId(m: maplibregl.Map) {
  return m.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
}
