"use client";

import { type Root } from "react-dom/client";
import maplibregl from "maplibre-gl";
import type { Layer } from "@deck.gl/core";
import { PathLayer, type PathLayerProps } from "@deck.gl/layers";
import type { Coordinates, LiveVehicle } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import artifactManifest from "@/lib/artifact-manifest.json";
import { getLineColor, type Trip } from "./map/route-layers";
import { SmartTrainMarker } from "./map/smart-train-marker";
import { ROUTE_WALK_LINE_LAYER_ID } from "./map/route-stops";
export function toLngLat(c: Coordinates): [number, number] {
  return [c.longitude, c.latitude];
}

export const DEBUG_LIVE_MAP = process.env.NODE_ENV !== "production";
// The visual.geojson network is the only subway renderer. Legacy ?subway-visual
// and ?subway-recon URL params are accepted as harmless no-ops (old QA links
// still load the same map).

// Content-hash cache-busting for the large static artifacts. The hash (from
// lib/artifact-manifest.json, regenerated at build) changes only when the file
// changes, so the browser/CDN can cache each version forever yet always picks
// up a rebuilt artifact -- unlike the old `?t=${Date.now()}` which re-fetched
// the multi-MB file on every page load.
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
      console.warn(
        "[jarvis-map/subway-visual] fetch failed; subway lines will not render",
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
      console.warn(
        "[jarvis-map/subway-station-anchors] fetch failed; falling back to raw station dots",
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
export const LIVE_TRAIN_ANIMATION_MS = 17_000;
export const LIVE_TRAIN_JITTER_METERS = 6;
export const LIVE_TRAIN_REPOSITION_METERS = 2500;
export const LIVE_TRAIN_BACKTRACK_JITTER_METERS = 120;
export const LIVE_TRAIN_BACKTRACK_REPOSITION_METERS = 650;
export const LIVE_TRAIN_OVERLAP_GAP_PIXELS = 60;
export const LIVE_TRAIN_STALE_TTL_SECONDS = 10 * 60;
export const ROUTE_SNAP_MAX_METERS = 700;
export const LIVE_TRAIN_SOURCE_ID = "sr-live-train-fleet";
export const LIVE_TRAIN_BLOCKED_ZONE_LAYER_ID = "sr-live-train-blocked-zone";
export const LIVE_TRAIN_STALE_HALO_LAYER_ID = "sr-live-train-stale-halo";
export const REMOVED_ACTIVE_TRAIN_LAYER_IDS = [
  "sr-live-train-headlight",
  "sr-live-train-cab-label",
  "sr-live-train-cab",
  "sr-live-train-stripe",
  "sr-live-train-roof",
  "sr-live-train-body",
  "sr-live-train-shadow",
];

export async function loadCanonicalSubwayNetwork() {
  const response = await fetch("/subway-network.canonical.geojson");
  if (!response.ok) {
    throw new Error(
      `Failed to load canonical subway network: ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as GeoJSON.FeatureCollection;
}

// The deck overlay is interleaved, so route layers share the depth buffer
// with the 3D building extrusions; a ground-level line gets depth-occluded
// wherever a building overlaps it (reads as a fragmented/dashed path).
// Route paths must always win the depth test.
export const ROUTE_PATH_DEPTH_PARAMETERS = {
  depthCompare: "always" as const,
  depthWriteEnabled: false,
};

export function selectedRouteLayers(trips: Trip[]) {
  return trips.map(
    (trip, i) =>
      new PathLayer<Trip>({
        id: `jr-selected-route-${i}`,
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
        // Interleaved deck re-resolves its layer order on every reconcile, so
        // reactively raising the MapLibre stop-dot layer above it loses the
        // race. Declaratively insert the route line BELOW the route-stop group
        // (walk dash + dots + labels) instead -- deck keeps it there. beforeId
        // is a @deck.gl/mapbox overlay prop, not in the base PathLayer type.
        beforeId: ROUTE_WALK_LINE_LAYER_ID,
      } as PathLayerProps<Trip> & { beforeId: string }),
  );
}

export function firstSymbolLayerId(m: maplibregl.Map) {
  return m.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
}

export function trainRoleFilter(role: LiveTrainFeatureRole, geometryType: "LineString" | "Point"): maplibregl.FilterSpecification {
  return ["all", ["==", ["geometry-type"], geometryType], ["==", ["get", "role"], role]];
}

export function ensureLiveTrainLayers(m: maplibregl.Map) {
  if (!m.getSource(LIVE_TRAIN_SOURCE_ID)) {
    m.addSource(LIVE_TRAIN_SOURCE_ID, {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }

  const beforeId = firstSymbolLayerId(m);

  for (const layerId of REMOVED_ACTIVE_TRAIN_LAYER_IDS) {
    if (m.getLayer(layerId)) {
      m.removeLayer(layerId);
    }
  }

  if (!m.getLayer(LIVE_TRAIN_BLOCKED_ZONE_LAYER_ID)) {
    m.addLayer({
      id: LIVE_TRAIN_BLOCKED_ZONE_LAYER_ID,
      type: "line",
      source: LIVE_TRAIN_SOURCE_ID,
      filter: trainRoleFilter("blocked-zone", "LineString"),
      layout: {
        "line-cap": "butt",
        "line-join": "round",
      },
      paint: {
        "line-color": "#EE352E",
        "line-opacity": 0.68,
        "line-width": [
          "interpolate", ["linear"], ["zoom"],
          12, 2.2,
          15, 3.2,
          17, 4.5,
        ],
        "line-dasharray": [1.4, 0.8],      },
    }, beforeId);
  }

  if (!m.getLayer(LIVE_TRAIN_STALE_HALO_LAYER_ID)) {
    m.addLayer({
      id: LIVE_TRAIN_STALE_HALO_LAYER_ID,
      type: "circle",
      source: LIVE_TRAIN_SOURCE_ID,
      filter: trainRoleFilter("stale-halo", "Point"),
      paint: {
        "circle-color": "rgba(238,53,46,0.14)",
        "circle-radius": [
          "interpolate", ["linear"], ["zoom"],
          12, 7,
          16, 11,
          18, 15,
        ],
        "circle-stroke-color": "#EE352E",
        "circle-stroke-opacity": 0.78,
        "circle-stroke-width": 1.4,      },
    }, beforeId);
  }

}

export function setLiveTrainLayerData(m: maplibregl.Map, features: LiveTrainFeature[]) {
  const source = m.getSource(LIVE_TRAIN_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  source.setData({
    type: "FeatureCollection",
    features,
  } satisfies LiveTrainFeatureCollection);
}

export function clearLiveTrainLayerData(m: maplibregl.Map) {
  setLiveTrainLayerData(m, []);
}

export interface VehicleMarkerEntry {
  marker: maplibregl.Marker;
  root: Root;
  currentLngLat: [number, number];
  targetLngLat: [number, number];
  bearing: number;
  lastTimestamp?: number;
  frame?: number;
  vehicle?: LiveVehicle;
  lastSnapshotKey?: string;
  lastSpeedLabel?: string;
  routeLine?: SubwayLineIndex;
  currentMeasure?: number;
  targetMeasure?: number;
  rawMeasure?: number;
  direction?: 1 | -1;
  lastTripId?: string;
  missingSnapshots: number;
  onFrame?: () => void;
  removed?: boolean;
}

export type SubwayLineFeature = GeoJSON.Feature<GeoJSON.LineString, {
  route_id?: string;
  shape_id?: string;
}>;

export interface PathMetrics {
  coordinates: [number, number][];
  cumulative: number[];
  totalLength: number;
}

export interface SubwayLineIndex extends PathMetrics {
  id: string;
  routeId: string;
  shapeId?: string;
  feature: SubwayLineFeature;
}

export interface TrackTarget {
  lngLat: [number, number];
  bearing: number;
  line?: SubwayLineIndex;
  measure?: number;
  rawMeasure?: number;
  direction?: 1 | -1;
  source: "route" | "fallback";
}

export interface RailTrackTarget extends TrackTarget {
  line: SubwayLineIndex;
  measure: number;
  rawMeasure: number;
  direction: 1 | -1;
  source: "route";
}

export interface ResolvedVehicleTarget {
  id: string;
  vehicle: LiveVehicle;
  target: TrackTarget;
}

export type SubwayNetworkIndex = Record<string, SubwayLineIndex[]>;

export type LiveTrainFeatureRole = "blocked-zone" | "stale-halo";

export type LiveTrainFeatureProperties = {
  id: string;
  routeId: string;
  routeColor: string;
  stale: boolean;
  role: LiveTrainFeatureRole;
};

export type LiveTrainFeature = GeoJSON.Feature<GeoJSON.LineString | GeoJSON.Point, LiveTrainFeatureProperties>;
export type LiveTrainFeatureCollection = GeoJSON.FeatureCollection<GeoJSON.LineString | GeoJSON.Point, LiveTrainFeatureProperties>;

// MTA GTFS-RT stop_ids end with "N" for northbound or "S" for southbound.
// This is the rider-facing compass direction and is stable across feeds;
// using it lets us group N-bound vs S-bound trains correctly regardless of
// how each polyline shape happened to be drawn in shapes.txt.
export function directionFromStopId(stopId?: string | null): "N" | "S" | null {
  if (!stopId) return null;
  const suffix = stopId.charAt(stopId.length - 1).toUpperCase();
  if (suffix === "N") return "N";
  if (suffix === "S") return "S";
  return null;
}

export function geometryRouteKey(routeId: string) {
  const upper = routeId.toUpperCase();
  if (upper === "6X") return "6";
  if (upper === "7X") return "7";
  if (upper === "FX") return "F";
  if (upper === "FS" || upper === "GS" || upper === "H") return "S";
  if (upper === "SIR") return "SI";
  return upper;
}

export function geometryRouteCandidates(routeId: string) {
  const primary = geometryRouteKey(routeId);
  const candidates = [primary];
  // The local subway geometry file currently omits dedicated B shapes.
  // B runs over D trackage in Manhattan/Bronx and Q trackage on Brighton,
  // so let B vehicles snap onto those shared alignments instead of falling
  // back to raw station coordinates off the rail centerline.
  if (primary === "B") {
    candidates.push("D", "Q");
  }
  return Array.from(new Set(candidates.filter(Boolean)));
}

export function easeInOutSine(t: number): number {
  return -(Math.cos(Math.PI * t) - 1) / 2;
}

export function bearingBetween(from: [number, number], to: [number, number]) {
  const lon1 = from[0] * Math.PI / 180;
  const lon2 = to[0] * Math.PI / 180;
  const lat1 = from[1] * Math.PI / 180;
  const lat2 = to[1] * Math.PI / 180;
  const y = Math.sin(lon2 - lon1) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(lon2 - lon1);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

export function blendBearing(current: number, next: number, amount = 0.28) {
  const delta = ((next - current + 540) % 360) - 180;
  return (current + delta * amount + 360) % 360;
}

export function distanceMeters(from: [number, number], to: [number, number]) {
  const radius = 6371000;
  const lat1 = from[1] * Math.PI / 180;
  const lat2 = to[1] * Math.PI / 180;
  const dLat = lat2 - lat1;
  const dLng = (to[0] - from[0]) * Math.PI / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * radius * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function metersPerPixelAtLat(lat: number, zoom: number) {
  return 156543.03392 * Math.cos(lat * Math.PI / 180) / 2 ** zoom;
}

export function buildPathMetrics(coordinates: [number, number][]): PathMetrics {
  const cumulative = [0];
  let totalLength = 0;
  for (let i = 0; i < coordinates.length - 1; i++) {
    totalLength += distanceMeters(coordinates[i], coordinates[i + 1]);
    cumulative.push(totalLength);
  }
  return { coordinates, cumulative, totalLength };
}

export function projectPointToSegment(point: [number, number], a: [number, number], b: [number, number]) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0
    ? 0
    : Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / lengthSq));
  const projected: [number, number] = [a[0] + dx * t, a[1] + dy * t];
  return { t, projected, distance: distanceMeters(point, projected) };
}

export function projectPointToIndexedLine(point: [number, number], line: PathMetrics) {
  let best: { distance: number; distanceAlong: number; projected: [number, number] } | null = null;

  for (let i = 0; i < line.coordinates.length - 1; i++) {
    const a = line.coordinates[i];
    const b = line.coordinates[i + 1];
    const segmentLength = line.cumulative[i + 1] - line.cumulative[i];
    const projected = projectPointToSegment(point, a, b);
    const distanceAlong = line.cumulative[i] + segmentLength * projected.t;
    if (!best || projected.distance < best.distance) {
      best = {
        distance: projected.distance,
        distanceAlong,
        projected: projected.projected,
      };
    }
  }

  return best ? { ...best, totalLength: line.totalLength } : null;
}

export function pointAlongMetrics(path: PathMetrics, distanceAlong: number): [number, number] | null {
  const line = path.coordinates;
  if (line.length === 0) return null;
  if (distanceAlong <= 0) return line[0];
  if (distanceAlong >= path.totalLength) return line[line.length - 1];

  for (let i = 0; i < line.length - 1; i++) {
    const a = line[i];
    const b = line[i + 1];
    const start = path.cumulative[i];
    const end = path.cumulative[i + 1];
    const segmentLength = end - start;
    if (end >= distanceAlong) {
      const t = segmentLength === 0 ? 0 : (distanceAlong - start) / segmentLength;
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
  }

  return line[line.length - 1];
}

export function sliceIndexedLineBetween(line: SubwayLineIndex, fromAlong: number, toAlong: number) {
  const startAlong = Math.max(0, Math.min(line.totalLength, fromAlong));
  const endAlong = Math.max(0, Math.min(line.totalLength, toAlong));
  const minAlong = Math.min(startAlong, endAlong);
  const maxAlong = Math.max(startAlong, endAlong);
  const start = pointAlongMetrics(line, startAlong);
  const end = pointAlongMetrics(line, endAlong);
  if (!start || !end) return null;

  const points: [number, number][] = [start];
  for (let i = 0; i < line.coordinates.length - 1; i++) {
    const nextWalked = line.cumulative[i + 1];
    if (nextWalked > minAlong && nextWalked < maxAlong) {
      points.push(line.coordinates[i + 1]);
    }
  }
  points.push(end);
  return startAlong <= endAlong ? points : points.reverse();
}

export function clampMeasure(line: SubwayLineIndex, measure: number) {
  return Math.max(0, Math.min(line.totalLength, measure));
}

export function syncRailTargetVisual(
  target: RailTrackTarget,
  measure = target.measure,
  direction = target.direction ?? 1,
): RailTrackTarget {
  const clampedMeasure = clampMeasure(target.line, measure);
  return {
    ...target,
    measure: clampedMeasure,
    direction,
    lngLat: pointAlongMetrics(target.line, clampedMeasure) || target.lngLat,
    bearing: bearingAlongLine(target.line, clampedMeasure, direction),
  };
}

export function liveTrainFeaturesForEntry(entry: VehicleMarkerEntry, zoom: number): LiveTrainFeature[] {
  const vehicle = entry.vehicle;
  const line = entry.routeLine;
  const measure = entry.currentMeasure;
  if (!vehicle || !line || measure == null) return [];
  if (!vehicle.stale) return [];

  const direction = entry.direction ?? 1;
  const headMeasure = clampMeasure(line, measure);
  const head = pointAlongMetrics(line, headMeasure);
  if (!head) return [];

  const routeColor = vehicle.color || getLineColor(vehicle.route_id || "");
  const baseProps = {
    id: vehicle.id,
    routeId: vehicle.route_id,
    routeColor,
    stale: true,
  };
  const makeLine = (
    role: LiveTrainFeatureRole,
    coordinates: [number, number][],
  ): LiveTrainFeature => ({
    type: "Feature",
    properties: {
      ...baseProps,
      role,
    },
    geometry: {
      type: "LineString",
      coordinates,
    },
  });
  const makePoint = (
    role: LiveTrainFeatureRole,
    coordinates: [number, number],
  ): LiveTrainFeature => ({
    type: "Feature",
    properties: {
      ...baseProps,
      role,
    },
    geometry: {
      type: "Point",
      coordinates,
    },
  });

  const features: LiveTrainFeature[] = [];
  const trainCenter = pointAlongMetrics(line, measure);
  const lat = trainCenter?.[1] ?? DEFAULT_LOCATION.lat;
  const blockedLength = Math.max(85, Math.min(420, metersPerPixelAtLat(lat, zoom) * 135));
  const blockedFrom = direction >= 0
    ? clampMeasure(line, measure - blockedLength)
    : clampMeasure(line, measure + blockedLength);
  const blocked = sliceIndexedLineBetween(line, blockedFrom, measure);
  if (blocked && blocked.length >= 2) {
    features.push(makeLine("blocked-zone", blocked));
  }
  features.push(makePoint("stale-halo", head));

  return features;
}

export function buildLiveTrainFeatures(markers: Map<string, VehicleMarkerEntry>, zoom: number) {
  const features: LiveTrainFeature[] = [];
  markers.forEach((entry) => {
    features.push(...liveTrainFeaturesForEntry(entry, zoom));
  });
  return features;
}

export function buildSubwayNetworkIndex(data: GeoJSON.FeatureCollection): SubwayNetworkIndex {
  const index: SubwayNetworkIndex = {};
  for (const feature of data.features) {
    if (feature.geometry?.type !== "LineString") continue;
    const routeId = geometryRouteKey(String(feature.properties?.route_id || ""));
    if (!routeId) continue;
    const coordinates = feature.geometry.coordinates as [number, number][];
    const metrics = buildPathMetrics(coordinates);
    if (metrics.totalLength <= 0) continue;
    const shapeId = String(feature.properties?.shape_id || (index[routeId]?.length ?? 0));
    (index[routeId] ||= []).push({
      id: `${routeId}:${shapeId}:${index[routeId]?.length ?? 0}`,
      routeId,
      shapeId,
      feature: feature as SubwayLineFeature,
      ...metrics,
    });
  }
  return index;
}

export function bearingAlongLine(line: SubwayLineIndex, measure: number, direction = 1) {
  const here = pointAlongMetrics(line, measure);
  const ahead = pointAlongMetrics(line, measure + 35 * direction);
  if (here && ahead && distanceMeters(here, ahead) > 1) {
    return bearingBetween(here, ahead);
  }
  const behind = pointAlongMetrics(line, measure - 35 * direction);
  if (here && behind && distanceMeters(behind, here) > 1) {
    return bearingBetween(behind, here);
  }
  return 0;
}

export function resolveVehicleTrack(vehicle: LiveVehicle, networkIndex: SubwayNetworkIndex): TrackTarget {
  const fallback: [number, number] = [vehicle.lng, vehicle.lat];
  const routeLines = geometryRouteCandidates(String(vehicle.route_id || ""))
    .flatMap((routeKey) => networkIndex[routeKey] || []);
  const fallbackBearing = segmentBearing(vehicle);
  const segment = vehicle.segment;
  if (
    !segment ||
    segment.from_lng == null ||
    segment.from_lat == null ||
    segment.to_lng == null ||
    segment.to_lat == null
  ) {
    let bestSnap: { line: SubwayLineIndex; projection: ReturnType<typeof projectPointToIndexedLine> } | null = null;
    for (const line of routeLines) {
      const projection = projectPointToIndexedLine(fallback, line);
      if (!projection || projection.distance > ROUTE_SNAP_MAX_METERS) continue;
      if (!bestSnap || projection.distance < bestSnap.projection!.distance) {
        bestSnap = { line, projection };
      }
    }
    if (bestSnap?.projection) {
      return syncRailTargetVisual({
        lngLat: bestSnap.projection.projected,
        line: bestSnap.line,
        measure: bestSnap.projection.distanceAlong,
        rawMeasure: bestSnap.projection.distanceAlong,
        direction: 1,
        bearing: bearingAlongLine(bestSnap.line, bestSnap.projection.distanceAlong),
        source: "route",
      });
    }
    return { lngLat: fallback, bearing: fallbackBearing, source: "fallback" };
  }

  if (!routeLines.length) return { lngLat: fallback, bearing: fallbackBearing, source: "fallback" };

  const from: [number, number] = [segment.from_lng, segment.from_lat];
  const to: [number, number] = [segment.to_lng, segment.to_lat];
  let best:
    | {
        score: number;
        line: SubwayLineIndex;
        fromAlong: number;
        toAlong: number;
      }
    | null = null;

  for (const feature of routeLines) {
    const line = feature;
    const fromProjection = projectPointToIndexedLine(from, line);
    const toProjection = projectPointToIndexedLine(to, line);
    if (!fromProjection || !toProjection) continue;
    const span = Math.abs(toProjection.distanceAlong - fromProjection.distanceAlong);
    if (fromProjection.distance > 700 || toProjection.distance > 700 || span < 15) continue;
    const score = fromProjection.distance + toProjection.distance + span * 0.003;
    if (!best || score < best.score) {
      best = {
        score,
        line,
        fromAlong: fromProjection.distanceAlong,
        toAlong: toProjection.distanceAlong,
      };
    }
  }

  if (!best) return { lngLat: fallback, bearing: fallbackBearing, source: "fallback" };
  const progress = Math.max(0, Math.min(1, segment.progress));
  const measure = best.fromAlong + (best.toAlong - best.fromAlong) * progress;
  const direction = best.toAlong >= best.fromAlong ? 1 : -1;
  return syncRailTargetVisual({
    lngLat: pointAlongMetrics(best.line, measure) || fallback,
    line: best.line,
    measure,
    rawMeasure: measure,
    direction,
    bearing: bearingAlongLine(best.line, measure, direction) ?? fallbackBearing,
    source: "route",
  });
}

export function isRailTrackTarget(target: TrackTarget): target is RailTrackTarget {
  return target.source === "route" && Boolean(target.line) && target.measure != null;
}

export function deconflictVehicleTargets(items: ResolvedVehicleTarget[], zoom: number) {
  // Group by (line, compass-direction). Prefer the rider-facing N/S suffix on
  // the GTFS stop_id over the polyline measure direction — different shape
  // features for the same route can be drawn in opposite coord order, so
  // target.direction (1 vs -1) can disagree between two trains that are
  // actually heading the same way. Using N/S keeps the group stable.
  const byGroup = new Map<string, ResolvedVehicleTarget[]>();
  for (const item of items) {
    if (!item.target.line || item.target.measure == null) continue;
    const compass = directionFromStopId(item.vehicle.stop_id);
    const groupDir = compass ?? (item.target.direction === -1 ? "neg" : "pos");
    const key = `${item.target.line.id}:${groupDir}`;
    const group = byGroup.get(key) || [];
    group.push(item);
    byGroup.set(key, group);
  }

  for (const group of byGroup.values()) {
    group.sort((a, b) => {
      const measureDelta = (a.target.measure ?? 0) - (b.target.measure ?? 0);
      return Math.abs(measureDelta) > 0.5 ? measureDelta : a.id.localeCompare(b.id);
    });

    let cluster: ResolvedVehicleTarget[] = [];
    const flushCluster = () => {
      if (cluster.length <= 1) {
        cluster = [];
        return;
      }

      const middle = cluster[Math.floor(cluster.length / 2)];
      const lat = middle.target.lngLat[1];
      const baseStep = Math.max(65, Math.min(260, metersPerPixelAtLat(lat, zoom) * 48));
      const step = Math.min(baseStep, 560 / Math.max(cluster.length - 1, 1));
      const center = (cluster.length - 1) / 2;

      cluster.forEach((item, index) => {
        if (!isRailTrackTarget(item.target)) return;
        const line = item.target.line;
        const measure = item.target.measure;
        const adjustedMeasure = Math.max(
          0,
          Math.min(line.totalLength, measure + (index - center) * step),
        );
        item.target = syncRailTargetVisual(item.target, adjustedMeasure);
      });

      cluster = [];
    };

    for (const item of group) {
      const previous = cluster[cluster.length - 1];
      if (!previous) {
        cluster = [item];
        continue;
      }

      const lat = item.target.lngLat[1];
      const threshold = Math.max(55, Math.min(220, metersPerPixelAtLat(lat, zoom) * 36));
      if (Math.abs((item.target.measure ?? 0) - (previous.target.measure ?? 0)) <= threshold) {
        cluster.push(item);
      } else {
        flushCluster();
        cluster = [item];
      }
    }
    flushCluster();
  }

  // Final pass per (line, direction): no two adjacent measures may sit inside
  // the zoom-aware minimum-gap band. Guarantees a trailing train can never
  // render visually past its leader on the same track & direction.
  for (const group of byGroup.values()) {
    group.sort((a, b) => (a.target.measure ?? 0) - (b.target.measure ?? 0));
    for (let i = 1; i < group.length; i++) {
      const prev = group[i - 1];
      const curr = group[i];
      if (!isRailTrackTarget(curr.target) || !isRailTrackTarget(prev.target)) continue;
      const line = curr.target.line;
      const lat = curr.target.lngLat[1];
      const minGap = metersPerPixelAtLat(lat, zoom) * LIVE_TRAIN_OVERLAP_GAP_PIXELS;
      const gap = curr.target.measure - prev.target.measure;
      if (gap < minGap) {
        const adjusted = Math.min(line.totalLength, prev.target.measure + minGap);
        curr.target = syncRailTargetVisual(curr.target, adjusted);
      }
    }
  }
}

export function speedLabel(from: [number, number], to: [number, number], fromTs?: number, toTs?: number) {
  if (!fromTs || !toTs || toTs <= fromTs) return "-- MPH";
  const mph = distanceMeters(from, to) / (toTs - fromTs) * 2.23694;
  if (!Number.isFinite(mph) || mph < 0.5) return "0 MPH";
  return `${Math.round(mph)} MPH`;
}

export function vehicleAgeSeconds(vehicle: LiveVehicle, nowSeconds: number) {
  if (typeof vehicle.age_seconds === "number") return vehicle.age_seconds;
  if (typeof vehicle.timestamp === "number" && vehicle.timestamp > 0) {
    return Math.max(0, nowSeconds - vehicle.timestamp);
  }
  return 0;
}

export function isExpiredStaleVehicle(vehicle: LiveVehicle, nowSeconds: number) {
  return Boolean(vehicle.stale) && vehicleAgeSeconds(vehicle, nowSeconds) >= LIVE_TRAIN_STALE_TTL_SECONDS;
}

// Two GTFS-RT feeds occasionally surface the same trip_id (e.g. ACE + BDFM for
// a shuttle). Keep only the freshest entity per trip so the same train never
// renders twice. Stop-pinned fallbacks without a trip_id are always kept.
export function dedupVehiclesByTripId(vehicles: LiveVehicle[]): LiveVehicle[] {
  const byTrip = new Map<string, LiveVehicle>();
  const untripped: LiveVehicle[] = [];
  for (const v of vehicles) {
    if (!v.trip_id) {
      untripped.push(v);
      continue;
    }
    const existing = byTrip.get(v.trip_id);
    if (!existing || (v.timestamp ?? 0) > (existing.timestamp ?? 0)) {
      byTrip.set(v.trip_id, v);
    }
  }
  return [...byTrip.values(), ...untripped];
}

export function stabilizeTrackTarget(
  entry: VehicleMarkerEntry,
  target: RailTrackTarget,
  vehicle: LiveVehicle,
) {
  const sameLine = entry.routeLine?.id === target.line.id;
  const sameTrip = !entry.lastTripId || !vehicle.trip_id || entry.lastTripId === vehicle.trip_id;
  const previousRawMeasure = entry.rawMeasure;
  const nextRawMeasure = target.rawMeasure ?? target.measure;
  const previousDirection = entry.direction;
  const nextDirection = target.direction ?? previousDirection ?? 1;

  if (!sameLine || !sameTrip || previousRawMeasure == null) {
    return {
      target,
      forceReposition: Boolean(entry.routeLine && !sameLine),
      clampedBacktrack: false,
    };
  }

  // Mid-trip direction flips are a feed artifact — a real subway train cannot
  // reverse on a track mid-run. Force a fade-reposition so the body never
  // visibly animates backwards along the polyline.
  //
  // Primary signal: the N/S suffix on the GTFS stop_id (rider-facing compass
  // direction, stable across shape features for the same route). Fall back
  // to the polyline measure-direction only when compass cannot be compared
  // on either side â€” different shape_ids for the same route can be drawn
  // with opposite coordinate winding, which makes measure-direction a false
  // positive when used alone.
  const previousCompass = directionFromStopId(entry.vehicle?.stop_id);
  const nextCompass = directionFromStopId(vehicle.stop_id);
  if (previousCompass && nextCompass) {
    if (previousCompass !== nextCompass) {
      return { target, forceReposition: true, clampedBacktrack: false };
    }
    // Same compass => definitely not a real direction flip; skip the
    // measure-direction fallback so a shape-winding flip no longer triggers
    // a reposition.
  } else if (previousDirection != null && previousDirection !== nextDirection) {
    return { target, forceReposition: true, clampedBacktrack: false };
  }

  const rawDelta = nextRawMeasure - previousRawMeasure;
  const directionalDelta = rawDelta * nextDirection;
  if (directionalDelta >= 0) {
    return { target, forceReposition: false, clampedBacktrack: false };
  }

  const backtrack = Math.abs(rawDelta);
  if (backtrack <= LIVE_TRAIN_BACKTRACK_JITTER_METERS) {
    const holdMeasure = entry.currentMeasure ?? target.measure;
    const heldTarget = syncRailTargetVisual(
      {
        ...target,
        rawMeasure: previousRawMeasure,
        bearing: entry.bearing,
      },
      holdMeasure,
      nextDirection,
    );
    return {
      target: {
        ...heldTarget,
        // Hold the current bearing rather than recomputing; recomputing would
        // let the body's nose swing even though we are visibly holding still.
        bearing: entry.bearing,
      },
      forceReposition: false,
      clampedBacktrack: true,
    };
  }

  // Any backtrack larger than the jitter window but still on the same trip is
  // a feed artifact — real trains don't reverse on shared track. Fade-
  // reposition so the body never animates backwards along the polyline.
  return {
    target,
    forceReposition: true,
    clampedBacktrack: false,
  };
}

export function segmentBearing(vehicle: LiveVehicle) {
  const segment = vehicle.segment;
  if (
    segment?.from_lng == null ||
    segment?.from_lat == null ||
    segment?.to_lng == null ||
    segment?.to_lat == null
  ) {
    return 0;
  }
  return bearingBetween([segment.from_lng, segment.from_lat], [segment.to_lng, segment.to_lat]);
}

export function vehicleSnapshotKey(vehicle: LiveVehicle, target: TrackTarget) {
  const segment = vehicle.segment;
  return [
    vehicle.id,
    vehicle.trip_id ?? "",
    vehicle.route_id,
    vehicle.stop_id ?? "",
    vehicle.status ?? "",
    vehicle.timestamp ?? "",
    vehicle.position_source ?? "",
    segment?.from_stop_id ?? "",
    segment?.to_stop_id ?? "",
    segment?.progress?.toFixed(4) ?? "",
    target.line?.id ?? "direct",
    (target.rawMeasure ?? target.measure)?.toFixed(2) ?? "",
    target.lngLat[0].toFixed(5),
    target.lngLat[1].toFixed(5),
  ].join("|");
}

export function setMarkerBearing(entry: VehicleMarkerEntry, bearing: number) {
  entry.bearing = Number.isFinite(bearing) ? bearing : entry.bearing;
  // CSS 0deg points the horizontal train body east, while map bearings use
  // 0deg as north. Subtract 90deg so the cab/light faces the track direction.
  entry.marker.getElement().style.setProperty("--train-bearing", `${entry.bearing - 90}deg`);
}

export function renderTrainMarker(
  entry: VehicleMarkerEntry,
  vehicle: LiveVehicle,
  selected: boolean,
  speed: string,
  onSelect: () => void,
) {
  setMarkerBearing(entry, entry.bearing);
  entry.root.render(
    <SmartTrainMarker
      vehicle={vehicle}
      selected={selected}
      speedLabel={speed}
      onSelect={onSelect}
    />,
  );
}

export function disposeVehicleMarker(entry: VehicleMarkerEntry) {
  if (entry.removed) return;
  entry.removed = true;
  if (entry.frame) {
    cancelAnimationFrame(entry.frame);
    entry.frame = undefined;
  }
  entry.onFrame = undefined;
  entry.marker.remove();
  queueMicrotask(() => {
    try {
      entry.root.unmount();
    } catch {
      // The map can switch scopes while React is still rendering the main tree.
      // Deferring teardown avoids the synchronous nested-root unmount warning.
    }
  });
}

export function animateMarkerAlong(
  entry: VehicleMarkerEntry,
  target: TrackTarget,
  path: [number, number][] | null,
  duration = LIVE_TRAIN_ANIMATION_MS,
  fromMeasure?: number,
) {
  if (!path || path.length < 2) {
    setMarkerImmediately(entry, target, true);
    return;
  }

  if (entry.frame) cancelAnimationFrame(entry.frame);
  const startedAt = performance.now();
  const animationPath = buildPathMetrics(path);
  const startMeasure = fromMeasure ?? entry.currentMeasure;
  entry.targetLngLat = target.lngLat;
  entry.targetMeasure = target.measure;
  entry.routeLine = target.line;
  entry.rawMeasure = target.rawMeasure ?? target.measure;
  entry.direction = target.direction ?? entry.direction ?? 1;

  function frame(now: number) {
    const rawT = Math.min((now - startedAt) / duration, 1);
    const t = easeInOutSine(rawT);
    const fallbackCurrent = pointAlongMetrics(animationPath, animationPath.totalLength * t) || target.lngLat;
    const fallbackLookAhead =
      pointAlongMetrics(animationPath, animationPath.totalLength * Math.min(t + 0.015, 1)) || target.lngLat;
    let current = fallbackCurrent;
    let nextBearing = bearingBetween(fallbackCurrent, fallbackLookAhead);
    if (target.measure != null && startMeasure != null) {
      const currentMeasure = startMeasure + (target.measure - startMeasure) * t;
      entry.currentMeasure = currentMeasure;
      if (entry.routeLine) {
        current = pointAlongMetrics(entry.routeLine, currentMeasure) || fallbackCurrent;
        nextBearing = bearingAlongLine(entry.routeLine, currentMeasure, entry.direction ?? 1);
      }
    }
    if (Number.isFinite(nextBearing)) {
      entry.bearing = blendBearing(entry.bearing, nextBearing, 0.38);
    }
    entry.currentLngLat = current;
    setMarkerBearing(entry, entry.bearing);
    entry.marker.setLngLat(current);
    entry.onFrame?.();
    if (rawT < 1) {
      entry.frame = requestAnimationFrame(frame);
    } else {
      setMarkerImmediately(entry, target, false);
    }
  }

  entry.frame = requestAnimationFrame(frame);
}

export function setMarkerImmediately(entry: VehicleMarkerEntry, target: TrackTarget, fade = true) {
  if (entry.frame) cancelAnimationFrame(entry.frame);
  entry.frame = undefined;
  entry.currentLngLat = target.lngLat;
  entry.targetLngLat = target.lngLat;
  entry.currentMeasure = target.measure;
  entry.targetMeasure = target.measure;
  entry.routeLine = target.line;
  entry.rawMeasure = target.rawMeasure ?? target.measure;
  entry.direction = target.direction ?? entry.direction ?? 1;
  setMarkerBearing(entry, target.bearing);
  entry.marker.setLngLat(target.lngLat);
  entry.onFrame?.();
  if (fade) {
    entry.marker.getElement().animate?.([{ opacity: 0.42 }, { opacity: 1 }], {
      duration: 280,
      easing: "ease-out",
    });
  }
}

