"use client";

import { useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Layer } from "@deck.gl/core";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathLayer } from "@deck.gl/layers";

import type { TransitRouteData, Coordinates, LiveVehicle } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import { createOrb, createOrbMarker } from "./map/orbs";
import { flyToRoute, startRotation, stopRotation, flyToOrigin } from "./map/camera";
import { addStationBadge, addIntermediateStopLabels, clearBadges } from "./map/station-badges";
import { buildTrips, getLineColor, type Trip } from "./map/route-layers";
import { createBuildingsLayer, shouldShowBuildings } from "./map/buildings-layer";
import { existingDeckBeforeId } from "./map/deck-layer-order";
import { createBuildingLighting } from "./map/lighting";
import {
  buildSubwayPulseTrips,
  createSubwayPulseLayers,
  SUBWAY_PULSE_LOOP_MS,
  type SubwayPulseTrip,
} from "./map/subway-pulse-layer";
import {
  ensureIncidentMapLibreLayers,
  INCIDENT_MAPLIBRE_LAYER_ID,
  setIncidentMapLibreData,
} from "./map/incidents/incident-maplibre-layer";
import {
  incidentFeatureToPopupViewModel,
  renderIncidentPopupHtml,
} from "./map/incidents/incidentPopup";
import type { MapIncident } from "./map/incidents/incidentMarkerTypes";
import { SmartTrainMarker } from "./map/smart-train-marker";
import {
  addSubwayNetwork,
  addSubwayStops,
  FIRST_SUBWAY_NETWORK_LAYER_ID,
  SUBWAY_NETWORK_LINE_LAYER_ID,
  setSubwayNetworkData,
  setSubwayNetworkFocus,
  setSubwayGroupEndpointData,
  setSubwayRouteIdentityData,
  setSubwayStopsData,
  type SubwayNetworkFocusInput,
} from "./map/subway-network";
import {
  loadVisualNetwork,
  type NetworkRenderMode,
} from "./map/route-geometry/loadVisualNetwork";
import { useSubwayStops } from "@/lib/use-subway-stops";

function toLngLat(c: Coordinates): [number, number] {
  return [c.longitude, c.latitude];
}

const DEBUG_LIVE_MAP = process.env.NODE_ENV !== "production";
const LIVE_TRAIN_ANIMATION_MS = 17_000;
const LIVE_TRAIN_JITTER_METERS = 6;
const LIVE_TRAIN_REPOSITION_METERS = 2500;
const LIVE_TRAIN_BACKTRACK_JITTER_METERS = 120;
const LIVE_TRAIN_BACKTRACK_REPOSITION_METERS = 650;
const LIVE_TRAIN_OVERLAP_GAP_PIXELS = 60;
const LIVE_TRAIN_STALE_TTL_SECONDS = 10 * 60;
const ROUTE_SNAP_MAX_METERS = 700;
const LIVE_TRAIN_SOURCE_ID = "sr-live-train-fleet";
const LIVE_TRAIN_BLOCKED_ZONE_LAYER_ID = "sr-live-train-blocked-zone";
const LIVE_TRAIN_STALE_HALO_LAYER_ID = "sr-live-train-stale-halo";
const EMPTY_FOCUSED_ROUTE_IDS: string[] = [];
const REMOVED_ACTIVE_TRAIN_LAYER_IDS = [
  "sr-live-train-headlight",
  "sr-live-train-cab-label",
  "sr-live-train-cab",
  "sr-live-train-stripe",
  "sr-live-train-roof",
  "sr-live-train-body",
  "sr-live-train-shadow",
];

function selectedRouteLayers(trips: Trip[]) {
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
      }),
  );
}

function firstSymbolLayerId(m: maplibregl.Map) {
  return m.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
}

function trainRoleFilter(role: LiveTrainFeatureRole, geometryType: "LineString" | "Point"): maplibregl.FilterSpecification {
  return ["all", ["==", ["geometry-type"], geometryType], ["==", ["get", "role"], role]];
}

function ensureLiveTrainLayers(m: maplibregl.Map) {
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

function setLiveTrainLayerData(m: maplibregl.Map, features: LiveTrainFeature[]) {
  const source = m.getSource(LIVE_TRAIN_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  source.setData({
    type: "FeatureCollection",
    features,
  } satisfies LiveTrainFeatureCollection);
}

function clearLiveTrainLayerData(m: maplibregl.Map) {
  setLiveTrainLayerData(m, []);
}

interface VehicleMarkerEntry {
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

type SubwayLineFeature = GeoJSON.Feature<GeoJSON.LineString, {
  route_id?: string;
  shape_id?: string;
}>;

interface PathMetrics {
  coordinates: [number, number][];
  cumulative: number[];
  totalLength: number;
}

interface SubwayLineIndex extends PathMetrics {
  id: string;
  routeId: string;
  shapeId?: string;
  feature: SubwayLineFeature;
}

interface TrackTarget {
  lngLat: [number, number];
  bearing: number;
  line?: SubwayLineIndex;
  measure?: number;
  rawMeasure?: number;
  direction?: 1 | -1;
  source: "route" | "fallback";
}

interface RailTrackTarget extends TrackTarget {
  line: SubwayLineIndex;
  measure: number;
  rawMeasure: number;
  direction: 1 | -1;
  source: "route";
}

interface ResolvedVehicleTarget {
  id: string;
  vehicle: LiveVehicle;
  target: TrackTarget;
}

type SubwayNetworkIndex = Record<string, SubwayLineIndex[]>;

type LiveTrainFeatureRole = "blocked-zone" | "stale-halo";

type LiveTrainFeatureProperties = {
  id: string;
  routeId: string;
  routeColor: string;
  stale: boolean;
  role: LiveTrainFeatureRole;
};

type LiveTrainFeature = GeoJSON.Feature<GeoJSON.LineString | GeoJSON.Point, LiveTrainFeatureProperties>;
type LiveTrainFeatureCollection = GeoJSON.FeatureCollection<GeoJSON.LineString | GeoJSON.Point, LiveTrainFeatureProperties>;

// MTA GTFS-RT stop_ids end with "N" for northbound or "S" for southbound.
// This is the rider-facing compass direction and is stable across feeds;
// using it lets us group N-bound vs S-bound trains correctly regardless of
// how each polyline shape happened to be drawn in shapes.txt.
function directionFromStopId(stopId?: string | null): "N" | "S" | null {
  if (!stopId) return null;
  const suffix = stopId.charAt(stopId.length - 1).toUpperCase();
  if (suffix === "N") return "N";
  if (suffix === "S") return "S";
  return null;
}

function geometryRouteKey(routeId: string) {
  const upper = routeId.toUpperCase();
  if (upper === "6X") return "6";
  if (upper === "7X") return "7";
  if (upper === "FX") return "F";
  if (upper === "FS" || upper === "GS" || upper === "H") return "S";
  if (upper === "SIR") return "SI";
  return upper;
}

function telemetryRouteKey(routeId: string | null | undefined) {
  const upper = String(routeId || "").trim().toUpperCase();
  if (upper === "6D") return "6X";
  if (upper === "7D") return "7X";
  if (upper === "FD" || upper === "FX") return "F";
  if (upper === "FS" || upper === "GS" || upper === "H") return "S";
  if (upper === "SIR") return "SI";
  return upper;
}

function routeTelemetryIds(routeData?: TransitRouteData | null) {
  const routeIds = new Set<string>();
  for (const step of routeData?.steps ?? []) {
    const routeId = telemetryRouteKey(step.route_id || step.train_line);
    if (routeId) routeIds.add(routeId);
  }
  return routeIds;
}

function geometryRouteCandidates(routeId: string) {
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

function easeInOutSine(t: number): number {
  return -(Math.cos(Math.PI * t) - 1) / 2;
}

function bearingBetween(from: [number, number], to: [number, number]) {
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

function blendBearing(current: number, next: number, amount = 0.28) {
  const delta = ((next - current + 540) % 360) - 180;
  return (current + delta * amount + 360) % 360;
}

function distanceMeters(from: [number, number], to: [number, number]) {
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

function metersPerPixelAtLat(lat: number, zoom: number) {
  return 156543.03392 * Math.cos(lat * Math.PI / 180) / 2 ** zoom;
}

function buildPathMetrics(coordinates: [number, number][]): PathMetrics {
  const cumulative = [0];
  let totalLength = 0;
  for (let i = 0; i < coordinates.length - 1; i++) {
    totalLength += distanceMeters(coordinates[i], coordinates[i + 1]);
    cumulative.push(totalLength);
  }
  return { coordinates, cumulative, totalLength };
}

function projectPointToSegment(point: [number, number], a: [number, number], b: [number, number]) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0
    ? 0
    : Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / lengthSq));
  const projected: [number, number] = [a[0] + dx * t, a[1] + dy * t];
  return { t, projected, distance: distanceMeters(point, projected) };
}

function projectPointToIndexedLine(point: [number, number], line: PathMetrics) {
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

function pointAlongMetrics(path: PathMetrics, distanceAlong: number): [number, number] | null {
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

function sliceIndexedLineBetween(line: SubwayLineIndex, fromAlong: number, toAlong: number) {
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

function clampMeasure(line: SubwayLineIndex, measure: number) {
  return Math.max(0, Math.min(line.totalLength, measure));
}

function syncRailTargetVisual(
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

function liveTrainFeaturesForEntry(entry: VehicleMarkerEntry, zoom: number): LiveTrainFeature[] {
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

function buildLiveTrainFeatures(markers: Map<string, VehicleMarkerEntry>, zoom: number) {
  const features: LiveTrainFeature[] = [];
  markers.forEach((entry) => {
    features.push(...liveTrainFeaturesForEntry(entry, zoom));
  });
  return features;
}

function buildSubwayNetworkIndex(data: GeoJSON.FeatureCollection): SubwayNetworkIndex {
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

function bearingAlongLine(line: SubwayLineIndex, measure: number, direction = 1) {
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

function resolveVehicleTrack(vehicle: LiveVehicle, networkIndex: SubwayNetworkIndex): TrackTarget {
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

function isRailTrackTarget(target: TrackTarget): target is RailTrackTarget {
  return target.source === "route" && Boolean(target.line) && target.measure != null;
}

function deconflictVehicleTargets(items: ResolvedVehicleTarget[], zoom: number) {
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

function speedLabel(from: [number, number], to: [number, number], fromTs?: number, toTs?: number) {
  if (!fromTs || !toTs || toTs <= fromTs) return "-- MPH";
  const mph = distanceMeters(from, to) / (toTs - fromTs) * 2.23694;
  if (!Number.isFinite(mph) || mph < 0.5) return "0 MPH";
  return `${Math.round(mph)} MPH`;
}

function vehicleAgeSeconds(vehicle: LiveVehicle, nowSeconds: number) {
  if (typeof vehicle.age_seconds === "number") return vehicle.age_seconds;
  if (typeof vehicle.timestamp === "number" && vehicle.timestamp > 0) {
    return Math.max(0, nowSeconds - vehicle.timestamp);
  }
  return 0;
}

function isExpiredStaleVehicle(vehicle: LiveVehicle, nowSeconds: number) {
  return Boolean(vehicle.stale) && vehicleAgeSeconds(vehicle, nowSeconds) >= LIVE_TRAIN_STALE_TTL_SECONDS;
}

// Two GTFS-RT feeds occasionally surface the same trip_id (e.g. ACE + BDFM for
// a shuttle). Keep only the freshest entity per trip so the same train never
// renders twice. Stop-pinned fallbacks without a trip_id are always kept.
function dedupVehiclesByTripId(vehicles: LiveVehicle[]): LiveVehicle[] {
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

function stabilizeTrackTarget(
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

function segmentBearing(vehicle: LiveVehicle) {
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

function vehicleSnapshotKey(vehicle: LiveVehicle, target: TrackTarget) {
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

function setMarkerBearing(entry: VehicleMarkerEntry, bearing: number) {
  entry.bearing = Number.isFinite(bearing) ? bearing : entry.bearing;
  // CSS 0deg points the horizontal train body east, while map bearings use
  // 0deg as north. Subtract 90deg so the cab/light faces the track direction.
  entry.marker.getElement().style.setProperty("--train-bearing", `${entry.bearing - 90}deg`);
}

function renderTrainMarker(
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

function disposeVehicleMarker(entry: VehicleMarkerEntry) {
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

function animateMarkerAlong(
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

function setMarkerImmediately(entry: VehicleMarkerEntry, target: TrackTarget, fade = true) {
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

interface JarvisMapProps {
  onLocationUpdate?: (coords: { lng: number; lat: number }) => void;
  routeData?: TransitRouteData | null;
  isSpeaking?: boolean;
  destCoords?: { lat: number; lng: number } | null;
  mode?: "planner" | "liveFeed";
  vehicles?: LiveVehicle[];
  liveVehicleScopeKey?: string;
  focusedRouteIds?: string[];
  incidentRouteIds?: string[];
  incidents?: MapIncident[];
  /** Called with map controls once the map is ready */
  onMapReady?: (actions: {
    recenter: () => void;
    zoomIn: () => void;
    zoomOut: () => void;
    resetNorth: () => void;
  }) => void;
}

export function JarvisMap({
  onLocationUpdate,
  routeData,
  isSpeaking,
  destCoords,
  mode = "planner",
  vehicles = [],
  liveVehicleScopeKey,
  focusedRouteIds,
  incidentRouteIds = [],
  incidents = [],
  onMapReady,
}: JarvisMapProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const destMarker = useRef<maplibregl.Marker | null>(null);
  const onLocationUpdateRef = useRef(onLocationUpdate);
  const mapReadyRef = useRef(false);
  const rotationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rotationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const originRef = useRef<[number, number] | null>(null);
  const stationMarkersRef = useRef<maplibregl.Marker[]>([]);
  const vehicleMarkersRef = useRef<Map<string, VehicleMarkerEntry>>(new Map());
  const liveTrainLayerFrameRef = useRef<number | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [selectedMapRouteIds, setSelectedMapRouteIds] = useState<string[]>([]);
  const [subwayNetworkIndex, setSubwayNetworkIndex] = useState<SubwayNetworkIndex>({});
  const mapZoomRef = useRef(14.5);
  const initialFlyDoneRef = useRef(false);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const routeDeckLayersRef = useRef<Layer[]>([]);
  const ambientPulseTripsRef = useRef<SubwayPulseTrip[]>([]);
  const ambientPulseLayerRef = useRef<Layer[] | null>(null);
  const ambientPulseFrameRef = useRef<number | null>(null);
  const ambientPulseStartRef = useRef<number | null>(null);
  const emphasizedPulseRouteIdsRef = useRef<Set<string>>(new Set());
  const subwayNetworkFocusStateRef = useRef<SubwayNetworkFocusInput>({
    selectedRouteIds: [],
    incidentRouteIds: [],
    nearbyRouteIds: [],
  });
  // Canonical subway geometry is the only trusted source for snapping,
  // route indexing, pulse trips, and backend-trust map behavior.
  const subwayCanonicalDataRef = useRef<GeoJSON.FeatureCollection | null>(null);
  // Visual subway geometry is render-only. It may later diverge for readability;
  // never use visual data for train snapping, route indexing, or pulses.
  const subwayVisualDataRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const subwayNetworkRenderModeRef = useRef<NetworkRenderMode>("family-visual");
  const subwayIdentityAnchorsRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const subwayGroupEndpointsRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const incidentsRef = useRef<MapIncident[]>([]);
  const incidentPopupRef = useRef<maplibregl.Popup | null>(null);
  const buildingsEnabledRef = useRef(true);
  const buildingsVisibleRef = useRef(false);

  const subwayStops = useSubwayStops();

  const rotationRefs = {
    rotationTimeout: rotationTimeoutRef,
    rotationInterval: rotationIntervalRef,
  };
  const resolvedFocusedRouteIds = focusedRouteIds ?? EMPTY_FOCUSED_ROUTE_IDS;

  const syncDeckOverlay = () => {
    const overlay = overlayRef.current;
    if (!overlay) return;

    const buildingLayer = createBuildingsLayer({
      visible: buildingsEnabledRef.current && buildingsVisibleRef.current,
      beforeId: existingDeckBeforeId(map.current, FIRST_SUBWAY_NETWORK_LAYER_ID),
    });

    const pulseLayers = ambientPulseLayerRef.current;

    overlay.setProps({
      layers: [
        buildingLayer,
        ...(pulseLayers ?? []),
        ...routeDeckLayersRef.current,
      ],
    });
  };

  const setRouteDeckLayers = (layers: Layer[]) => {
    routeDeckLayersRef.current = layers;
    syncDeckOverlay();
  };

  function requestLiveTrainLayerSync() {
    if (liveTrainLayerFrameRef.current != null) return;
    liveTrainLayerFrameRef.current = requestAnimationFrame(() => {
      liveTrainLayerFrameRef.current = null;
      if (!map.current || !mapReadyRef.current || mode !== "liveFeed") return;
      setLiveTrainLayerData(
        map.current,
        buildLiveTrainFeatures(vehicleMarkersRef.current, mapZoomRef.current),
      );
    });
  }

  function stopAmbientPulseAnimation() {
    if (ambientPulseFrameRef.current != null) {
      cancelAnimationFrame(ambientPulseFrameRef.current);
      ambientPulseFrameRef.current = null;
    }
  }

  function startAmbientPulseAnimation() {
    if (
      ambientPulseFrameRef.current != null ||
      !overlayRef.current ||
      ambientPulseTripsRef.current.length === 0
    ) {
      return;
    }

    ambientPulseStartRef.current ??= performance.now();

    const frame = (now: number) => {
      if (!overlayRef.current || ambientPulseTripsRef.current.length === 0) {
        ambientPulseFrameRef.current = null;
        return;
      }

      const start = ambientPulseStartRef.current ?? now;
      const currentTime = (now - start) % SUBWAY_PULSE_LOOP_MS;
      ambientPulseLayerRef.current = createSubwayPulseLayers(
        ambientPulseTripsRef.current,
        currentTime,
        {
          emphasizedRouteIds: emphasizedPulseRouteIdsRef.current,
        },
      );
      syncDeckOverlay();
      ambientPulseFrameRef.current = requestAnimationFrame(frame);
    };

    ambientPulseFrameRef.current = requestAnimationFrame(frame);
  }

  useEffect(() => {
    onLocationUpdateRef.current = onLocationUpdate;
  }, [onLocationUpdate]);

  // Map initialization
  useEffect(() => {
    if (mode === "liveFeed") {
      setSelectedVehicleId(null);
      setSelectedMapRouteIds([]);
    }
  }, [mode, liveVehicleScopeKey]);

  useEffect(() => {
    const selectedRouteIds = routeTelemetryIds(routeData);
    for (const routeId of resolvedFocusedRouteIds) {
      const focusedRouteId = telemetryRouteKey(routeId);
      if (focusedRouteId) selectedRouteIds.add(focusedRouteId);
    }
    for (const routeId of selectedMapRouteIds) {
      const selectedMapRouteId = telemetryRouteKey(routeId);
      if (selectedMapRouteId) selectedRouteIds.add(selectedMapRouteId);
    }
    if (selectedVehicleId) {
      const selectedVehicle = vehicles.find((vehicle) => vehicle.id === selectedVehicleId);
      const selectedRouteId = telemetryRouteKey(selectedVehicle?.route_id);
      if (selectedRouteId) selectedRouteIds.add(selectedRouteId);
    }

    const incidentFocusedRouteIds = new Set<string>();
    for (const routeId of incidentRouteIds) {
      const incidentRouteId = telemetryRouteKey(routeId);
      if (incidentRouteId) incidentFocusedRouteIds.add(incidentRouteId);
    }

    const nearbyRouteIds = new Set<string>();
    for (const vehicle of vehicles) {
      const nearbyRouteId = telemetryRouteKey(vehicle.route_id);
      if (nearbyRouteId) nearbyRouteIds.add(nearbyRouteId);
    }

    const emphasizedPulseRouteIds = new Set<string>([
      ...selectedRouteIds,
      ...incidentFocusedRouteIds,
      ...nearbyRouteIds,
    ]);
    const focusState: SubwayNetworkFocusInput = {
      selectedRouteIds,
      incidentRouteIds: incidentFocusedRouteIds,
      nearbyRouteIds,
    };

    emphasizedPulseRouteIdsRef.current = emphasizedPulseRouteIds;
    subwayNetworkFocusStateRef.current = focusState;
    if (map.current && mapReadyRef.current) {
      setSubwayNetworkFocus(map.current, focusState);
    }
  }, [
    resolvedFocusedRouteIds,
    incidentRouteIds,
    routeData,
    selectedMapRouteIds,
    selectedVehicleId,
    vehicles,
  ]);

  useEffect(() => {
    if (!mapContainer.current) return;

    buildingsEnabledRef.current =
      typeof window === "undefined" ||
      new URLSearchParams(window.location.search).get("b") !== "0";

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
      center: [DEFAULT_LOCATION.lng, DEFAULT_LOCATION.lat],
      zoom: 14.5,
      pitch: 45,
      bearing: 0,
      canvasContextAttributes: { antialias: true },
    });

    function setMapCursor(cursor: string) {
      const canvas = map.current?.getCanvas();
      if (canvas) canvas.style.cursor = cursor;
    }

    function focusRoutesFromIncidentFeature(feature?: maplibregl.MapGeoJSONFeature) {
      const rawRouteIds = String(feature?.properties?.route_ids ?? "");
      const routeIds = rawRouteIds
        .split(",")
        .map((routeId) => telemetryRouteKey(routeId))
        .filter(Boolean);

      if (routeIds.length > 0) {
        setSelectedMapRouteIds(Array.from(new Set(routeIds)));
      }
    }

    function openIncidentPopup(
      feature?: maplibregl.MapGeoJSONFeature,
      fallbackLngLat?: maplibregl.LngLat,
    ) {
      if (!map.current || !feature?.properties) return;

      const model = incidentFeatureToPopupViewModel(feature.properties);
      const coordinates: [number, number] | null =
        feature.geometry.type === "Point"
          ? (feature.geometry.coordinates as [number, number])
          : fallbackLngLat
            ? [fallbackLngLat.lng, fallbackLngLat.lat]
            : null;
      if (!coordinates) return;

      incidentPopupRef.current?.remove();
      const popup = new maplibregl.Popup({
        anchor: "bottom-left",
        className: "sr-incident-maplibre-popup",
        closeButton: false,
        closeOnClick: false,
        maxWidth: "320px",
        offset: [24, -50],
      })
        .setLngLat(coordinates)
        .setHTML(renderIncidentPopupHtml(model))
        .addTo(map.current);

      incidentPopupRef.current = popup;
    }

    function handleSubwayRouteClick(event: maplibregl.MapLayerMouseEvent) {
      const feature = event.features?.[0];
      const routeId = telemetryRouteKey(feature?.properties?.route_id);
      if (routeId) setSelectedMapRouteIds([routeId]);

      const networkDebug =
        process.env.NEXT_PUBLIC_NETWORK_DEBUG === "on" ||
        new URLSearchParams(window.location.search).get("network-debug") === "1";
      if (DEBUG_LIVE_MAP && networkDebug && feature?.properties) {
        const properties = feature.properties;
        console.info("[subway-network/debug-click]", {
          route_id: properties.route_id,
          visual_route_id: properties.visual_route_id,
          visual_route_normalization_reason: properties.visual_route_normalization_reason,
          display_route: properties.display_route,
          shape_id: properties.shape_id,
          raw_route_ids: properties.raw_route_ids,
          raw_shape_ids: properties.raw_shape_ids,
          canonical_direction_id: properties.canonical_direction_id,
          canonical_direction_count: properties.canonical_direction_count,
          collapsed_direction_count: properties.collapsed_direction_count,
          direction_collapse_reason: properties.direction_collapse_reason,
          representative_shape_id: properties.representative_shape_id,
          visual_edge_ids: properties.visual_edge_ids,
          edge_count: properties.edge_count,
          edge_sequence_range: properties.edge_sequence_range,
          visual_edge_metadata: properties.visual_edge_metadata,
          from_stop_id: properties.from_stop_id,
          to_stop_id: properties.to_stop_id,
          from_stop_name: properties.from_stop_name,
          to_stop_name: properties.to_stop_name,
          edge_sequence: properties.edge_sequence,
          representative_edge_geometry_source: properties.representative_edge_geometry_source,
          representative_edge_geometry_sources: properties.representative_edge_geometry_sources,
          edge_geometry_confidence: properties.edge_geometry_confidence,
          geometry_reversal_count: properties.geometry_reversal_count,
          internal_stitch_valid: properties.internal_stitch_valid,
          max_internal_snap_distance_m: properties.max_internal_snap_distance_m,
          branch_representative_geometry_used: properties.branch_representative_geometry_used,
          branch_representative_warning: properties.branch_representative_warning,
          visual_branch_id: properties.visual_branch_id,
          group_sequence: properties.group_sequence,
          group_id: properties.group_id,
          group_member_routes: properties.group_member_routes,
          group_member_visual_routes: properties.group_member_visual_routes,
          group_lane_order: properties.group_lane_order,
          group_lane_order_visual: properties.group_lane_order_visual,
          visual_lane_slot: properties.visual_lane_slot,
          visual_z_order: properties.visual_z_order,
          effective_render_key: properties.effective_render_key,
          segment_kind: properties.segment_kind,
          debug_id: properties.debug_id,
          source: properties.source,
          feature_length_m: properties.feature_length_m,
          render_mode: subwayNetworkRenderModeRef.current,
          handoff_node_id: properties.handoff_node_id,
          handoff_reason: properties.handoff_reason,
          handoff_kind: properties.handoff_kind,
          handoff_from_group_id: properties.handoff_from_group_id,
          handoff_to_group_id: properties.handoff_to_group_id,
          handoff_from_lane_slot: properties.handoff_from_lane_slot,
          handoff_to_lane_slot: properties.handoff_to_lane_slot,
          assignment_reason: properties.assignment_reason,
          edge_pipeline_state:
            Number(properties.edge_count ?? 0) > 0
              ? "merged validated stop-pair visual edge(s)"
              : "missing visual edge lineage",
          continuity_state: properties.handoff_node_id
            ? "valid handoff boundary"
            : "continuous segment",
          base_color_layer_visible: true,
          base_casing_layer_visible: true,
          base_glow_layer_visible: true,
          highlight_only: false,
          base_visibility_reason: "feature is in the active subway visual source and group-corridors uses the shared subway line stack",
        });
      }
    }

    function handleIncidentClick(event: maplibregl.MapLayerMouseEvent) {
      const feature = event.features?.[0];
      focusRoutesFromIncidentFeature(feature);
      openIncidentPopup(feature, event.lngLat);
    }

    function handleMapBackgroundClick(event: maplibregl.MapMouseEvent) {
      if (!map.current) return;
      const layers = [
        SUBWAY_NETWORK_LINE_LAYER_ID,
        INCIDENT_MAPLIBRE_LAYER_ID,
      ].filter((layerId) => Boolean(map.current?.getLayer(layerId)));
      const hits =
        layers.length > 0
          ? map.current.queryRenderedFeatures(event.point, { layers })
          : [];

      if (hits.length === 0) {
        setSelectedMapRouteIds([]);
        setSelectedVehicleId(null);
        incidentPopupRef.current?.remove();
        incidentPopupRef.current = null;
      }
    }

    map.current.on("style.load", () => {
      if (!map.current) return;

      const overlay = new MapboxOverlay({
        interleaved: true,
        layers: [],
        effects: [createBuildingLighting()],
      });
      map.current.addControl(overlay as unknown as maplibregl.IControl);
      overlayRef.current = overlay;
      addSubwayNetwork(map.current);
      if (subwayVisualDataRef.current) {
        setSubwayNetworkData(
          map.current,
          subwayVisualDataRef.current,
          subwayNetworkRenderModeRef.current,
        );
      }
      if (subwayIdentityAnchorsRef.current) {
        setSubwayRouteIdentityData(map.current, subwayIdentityAnchorsRef.current);
      }
      if (subwayGroupEndpointsRef.current) {
        setSubwayGroupEndpointData(map.current, subwayGroupEndpointsRef.current);
      }
      addSubwayStops(map.current);
      ensureLiveTrainLayers(map.current);
      ensureIncidentMapLibreLayers(map.current);
      setIncidentMapLibreData(map.current, incidentsRef.current);
      setSubwayNetworkFocus(map.current, subwayNetworkFocusStateRef.current);
      map.current.on("click", SUBWAY_NETWORK_LINE_LAYER_ID, handleSubwayRouteClick);
      map.current.on("click", INCIDENT_MAPLIBRE_LAYER_ID, handleIncidentClick);
      map.current.on("mouseenter", SUBWAY_NETWORK_LINE_LAYER_ID, () => setMapCursor("pointer"));
      map.current.on("mouseleave", SUBWAY_NETWORK_LINE_LAYER_ID, () => setMapCursor(""));
      map.current.on("mouseenter", INCIDENT_MAPLIBRE_LAYER_ID, () => setMapCursor("pointer"));
      map.current.on("mouseleave", INCIDENT_MAPLIBRE_LAYER_ID, () => setMapCursor(""));
      buildingsVisibleRef.current = shouldShowBuildings(map.current.getZoom());
      syncDeckOverlay();
      startAmbientPulseAnimation();

      mapReadyRef.current = true;

      onMapReady?.({
        recenter: () => {
          const origin = originRef.current;
          if (origin && map.current) {
            map.current.flyTo({ center: origin, zoom: 15.6, pitch: 0, bearing: 0, duration: 1500 });
          }
        },
        zoomIn: () => {
          map.current?.easeTo({ zoom: Math.min((map.current?.getZoom() ?? 14.5) + 0.8, 18.5), duration: 220 });
        },
        zoomOut: () => {
          map.current?.easeTo({ zoom: Math.max((map.current?.getZoom() ?? 14.5) - 0.8, 9.5), duration: 220 });
        },
        resetNorth: () => {
          map.current?.easeTo({ bearing: 0, pitch: 0, duration: 260 });
        },
      });
    });

    map.current.on("click", handleMapBackgroundClick);

    map.current.on("zoomend", () => {
      if (map.current) {
        const z = map.current.getZoom();
        mapZoomRef.current = z;
        const nextBuildingVisibility = shouldShowBuildings(z);
        if (buildingsVisibleRef.current !== nextBuildingVisibility) {
          buildingsVisibleRef.current = nextBuildingVisibility;
          syncDeckOverlay();
        }
        requestLiveTrainLayerSync();
      }
    });

    function handlePosition(coords: { lng: number; lat: number }) {
      onLocationUpdateRef.current?.(coords);
      originRef.current = [coords.lng, coords.lat];

      if (map.current && !initialFlyDoneRef.current) {
        initialFlyDoneRef.current = true;
        map.current.flyTo({
          center: [coords.lng, coords.lat],
          zoom: 15.6,
          pitch: 0,
          bearing: 0,
          duration: 2000,
        });
      }

      if (marker.current) {
        marker.current.setLngLat([coords.lng, coords.lat]);
      } else if (map.current) {
        marker.current = createOrbMarker(map.current, coords, "#00D4FF", "rgba(0, 212, 255, 0.4)");
      }
    }

    let watchId: number;

    if (navigator.geolocation) {
      watchId = navigator.geolocation.watchPosition(
        (position) => {
          handlePosition({
            lng: position.coords.longitude,
            lat: position.coords.latitude,
          });
        },
        (error) => {
          if (error.code !== error.PERMISSION_DENIED) {
            console.warn("Geolocation unavailable:", error.message);
          }
          handlePosition(DEFAULT_LOCATION);
        },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 },
      );
    } else {
      handlePosition(DEFAULT_LOCATION);
    }

    return () => {
      vehicleMarkersRef.current.forEach((entry) => {
        disposeVehicleMarker(entry);
      });
      vehicleMarkersRef.current.clear();
      if (liveTrainLayerFrameRef.current != null) {
        cancelAnimationFrame(liveTrainLayerFrameRef.current);
        liveTrainLayerFrameRef.current = null;
      }
      stopAmbientPulseAnimation();
      incidentPopupRef.current?.remove();
      incidentPopupRef.current = null;
      ambientPulseLayerRef.current = null;
      if (map.current && map.current.getSource(LIVE_TRAIN_SOURCE_ID)) {
        clearLiveTrainLayerData(map.current);
      }
      map.current?.remove();
      if (watchId) navigator.geolocation.clearWatch(watchId);
    };
  }, []);

  useEffect(() => {
    incidentsRef.current = incidents;
    if (!map.current || !mapReadyRef.current) return;
    ensureIncidentMapLibreLayers(map.current);
    setIncidentMapLibreData(map.current, incidents);
  }, [incidents]);

  useEffect(() => {
    if (!map.current || !mapReadyRef.current || !subwayStops) return;
    setSubwayStopsData(map.current, subwayStops);
  }, [subwayStops]);

  useEffect(() => {
    let cancelled = false;
    loadVisualNetwork()
      .then(({ canonical, visual, identityAnchors, groupEndpoints, renderMode, visualSource }) => {
        if (!cancelled) {
          subwayCanonicalDataRef.current = canonical;
          subwayVisualDataRef.current = visual;
          subwayNetworkRenderModeRef.current = renderMode;
          subwayIdentityAnchorsRef.current = identityAnchors;
          subwayGroupEndpointsRef.current = groupEndpoints;
          if (map.current && mapReadyRef.current) {
            setSubwayNetworkData(map.current, visual, renderMode);
            setSubwayRouteIdentityData(map.current, identityAnchors);
            setSubwayGroupEndpointData(map.current, groupEndpoints);
          }
          setSubwayNetworkIndex(buildSubwayNetworkIndex(canonical));
          ambientPulseTripsRef.current = buildSubwayPulseTrips(canonical);
          if (DEBUG_LIVE_MAP) {
            console.info("[jarvis-map/subway-network] loaded route geometry", {
              renderMode,
              visualSource,
              canonicalFeatures: canonical.features.length,
              visualFeatures: visual.features.length,
              identityAnchors: identityAnchors.features.length,
              groupEndpoints: groupEndpoints.features.length,
            });
          }
          startAmbientPulseAnimation();
        }
      })
      .catch((error) => {
        if (DEBUG_LIVE_MAP) console.warn("[jarvis-map/subway-network] failed to load route geometry", error);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;
    ensureLiveTrainLayers(map.current);

    const markers = vehicleMarkersRef.current;
    if (mode !== "liveFeed") {
      markers.forEach((entry) => {
        disposeVehicleMarker(entry);
      });
      markers.clear();
      clearLiveTrainLayerData(map.current);
      setSelectedVehicleId(null);
      return;
    }

    const nextIds = new Set<string>();
    let created = 0;
    let updated = 0;
    let skippedInvalid = 0;
    let skippedNoTrack = 0;
    let fallbackTargets = 0;
    let skippedExpired = 0;
    let railAnimations = 0;
    let fallbackAnimations = 0;
    let railRepositions = 0;
    let jitterClamps = 0;
    let backtrackClamps = 0;
    const nowSeconds = Math.floor(Date.now() / 1000);
    const dedupedVehicles = dedupVehiclesByTripId(vehicles);
    const resolvedVehicles: ResolvedVehicleTarget[] = [];
    for (const vehicle of dedupedVehicles) {
      if (!isFinite(vehicle.lng) || !isFinite(vehicle.lat)) {
        skippedInvalid++;
        continue;
      }
      if (isExpiredStaleVehicle(vehicle, nowSeconds)) {
        skippedExpired++;
        continue;
      }
      const id = vehicle.id || `${vehicle.route_id}-${vehicle.trip_id || vehicle.stop_id || "vehicle"}`;
      const target = resolveVehicleTrack(vehicle, subwayNetworkIndex);
      if (!isRailTrackTarget(target)) fallbackTargets++;
      resolvedVehicles.push({
        id,
        vehicle,
        target,
      });
    }
    deconflictVehicleTargets(resolvedVehicles, mapZoomRef.current);

    for (const { vehicle, id, target } of resolvedVehicles) {
      nextIds.add(id);
      const targetIsRail = isRailTrackTarget(target);
      let effectiveTarget = targetIsRail ? syncRailTargetVisual(target) : target;

      const existing = markers.get(id);
      if (existing) {
        existing.onFrame = requestLiveTrainLayerSync;
        let stabilized:
          | { target: RailTrackTarget; forceReposition: boolean; clampedBacktrack: boolean }
          | null = null;
        if (targetIsRail) {
          stabilized = stabilizeTrackTarget(existing, target, vehicle);
          effectiveTarget = syncRailTargetVisual(stabilized.target);
          if (stabilized.clampedBacktrack) {
            backtrackClamps++;
          }
        }
        const snapshotKey = vehicleSnapshotKey(vehicle, effectiveTarget);
        const current = existing.currentLngLat;
        const visualDistance = distanceMeters(current, effectiveTarget.lngLat);
        const targetDistance = distanceMeters(existing.targetLngLat, effectiveTarget.lngLat);
        const snapshotChanged =
          existing.lastSnapshotKey !== snapshotKey ||
          targetDistance > LIVE_TRAIN_JITTER_METERS;
        const speed = snapshotChanged
          ? speedLabel(current, effectiveTarget.lngLat, existing.lastTimestamp, vehicle.timestamp)
          : existing.lastSpeedLabel || "-- MPH";
        if (!existing.frame) {
          setMarkerBearing(existing, effectiveTarget.bearing);
        }
        existing.vehicle = vehicle;
        existing.lastTripId = vehicle.trip_id;
        existing.direction = effectiveTarget.direction ?? existing.direction ?? 1;
        existing.marker.getElement().dataset.stale = String(vehicle.stale);
        existing.marker.getElement().title = `${vehicle.route_id} train${vehicle.stale ? " - stale position" : ""}`;
        const selectCb = () => setSelectedVehicleId((currentId) => (currentId === id ? null : id));
        renderTrainMarker(existing, vehicle, selectedVehicleId === id, speed, selectCb);
        if (vehicle.stale) {
          if (existing.frame) cancelAnimationFrame(existing.frame);
          existing.frame = undefined;
          existing.lastSnapshotKey = snapshotKey;
          existing.lastSpeedLabel = "0 MPH";
          requestLiveTrainLayerSync();
        } else if (!targetIsRail) {
          existing.lastSnapshotKey = snapshotKey;
          existing.lastSpeedLabel = speed;
          existing.lastTimestamp = vehicle.timestamp;
          if (!snapshotChanged || visualDistance <= LIVE_TRAIN_JITTER_METERS) {
            setMarkerImmediately(existing, effectiveTarget, false);
          } else {
            animateMarkerAlong(
              existing,
              effectiveTarget,
              [current, effectiveTarget.lngLat],
              LIVE_TRAIN_ANIMATION_MS,
            );
            fallbackAnimations++;
          }
        } else if (snapshotChanged) {
          if (!isRailTrackTarget(effectiveTarget)) {
            existing.lastSnapshotKey = snapshotKey;
            existing.lastSpeedLabel = speed;
            existing.lastTimestamp = vehicle.timestamp;
            setMarkerImmediately(existing, effectiveTarget, false);
            updated++;
            continue;
          }
          const railTarget = effectiveTarget;
          existing.lastSnapshotKey = snapshotKey;
          existing.lastSpeedLabel = speed;
          existing.lastTimestamp = vehicle.timestamp;

          if (stabilized?.clampedBacktrack || visualDistance <= LIVE_TRAIN_JITTER_METERS) {
            setMarkerImmediately(existing, effectiveTarget, false);
            jitterClamps++;
          } else if (stabilized?.forceReposition || visualDistance >= LIVE_TRAIN_REPOSITION_METERS) {
            setMarkerImmediately(existing, effectiveTarget, true);
            railRepositions++;
          } else {
            let path: [number, number][] | null = null;
            // Prefer currentMeasure on the same line — it's the logical
            // progress already computed on this polyline, guaranteed to sit
            // on the line. Re-projecting the interpolated mid-animation
            // pixel can snap to the wrong segment on curves. On a line
            // change we try re-projection, and if that fails the distance
            // cap we still fall back to the prior measure rather than
            // collapsing to a fade-reposition.
            let fromMeasure: number | undefined;
            const sameLineId = existing.routeLine?.id === railTarget.line.id;
            if (sameLineId && existing.currentMeasure != null) {
              fromMeasure = existing.currentMeasure;
            } else {
              const projection = projectPointToIndexedLine(current, railTarget.line);
              if (projection && projection.distance <= ROUTE_SNAP_MAX_METERS) {
                fromMeasure = projection.distanceAlong;
              } else if (existing.currentMeasure != null) {
                fromMeasure = existing.currentMeasure;
              }
            }

            if (fromMeasure != null) {
              path = sliceIndexedLineBetween(railTarget.line, fromMeasure, railTarget.measure);
            }

            if (!path || path.length < 2) {
              setMarkerImmediately(existing, railTarget, true);
              railRepositions++;
            } else {
              animateMarkerAlong(existing, railTarget, path, LIVE_TRAIN_ANIMATION_MS, fromMeasure);
              railAnimations++;
            }
          }
        } else {
          requestLiveTrainLayerSync();
        }
        updated++;
        continue;
      }

      const el = document.createElement("div");
      el.className = "sr-train-marker";
      el.dataset.stale = String(vehicle.stale);
      el.title = `${vehicle.route_id} train${vehicle.stale ? " - stale position" : ""}`;
      const newMarker = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat(effectiveTarget.lngLat)
        .addTo(map.current);
      const selectCb = () => setSelectedVehicleId((current) => (current === id ? null : id));
      const entry: VehicleMarkerEntry = {
        marker: newMarker,
        root: createRoot(el),
        currentLngLat: effectiveTarget.lngLat,
        targetLngLat: effectiveTarget.lngLat,
        bearing: effectiveTarget.bearing,
        lastTimestamp: vehicle.timestamp,
        lastSnapshotKey: vehicleSnapshotKey(vehicle, effectiveTarget),
        lastSpeedLabel: "-- MPH",
        routeLine: effectiveTarget.line,
        currentMeasure: effectiveTarget.measure,
        targetMeasure: effectiveTarget.measure,
        rawMeasure: effectiveTarget.rawMeasure ?? effectiveTarget.measure,
        direction: effectiveTarget.direction ?? 1,
        lastTripId: vehicle.trip_id,
        missingSnapshots: 0,
        onFrame: requestLiveTrainLayerSync,
        vehicle,
      };
      renderTrainMarker(
        entry,
        vehicle,
        selectedVehicleId === id,
        "-- MPH",
        selectCb,
      );
      markers.set(id, entry);
      requestLiveTrainLayerSync();
      created++;
    }

    let selectedStillExists = false;
    let removed = 0;
    markers.forEach((entry, id) => {
      if (!nextIds.has(id)) {
        disposeVehicleMarker(entry);
        markers.delete(id);
        removed++;
        return;
      }
      if (id === selectedVehicleId) selectedStillExists = true;
    });
    if (selectedVehicleId && !selectedStillExists) {
      setSelectedVehicleId(null);
    }
    if (removed > 0) {
      requestLiveTrainLayerSync();
    }
    if (DEBUG_LIVE_MAP) {
      console.info("[jarvis-map/live-vehicles] marker sync", JSON.stringify({
        inputVehicles: vehicles.length,
        activeMarkers: markers.size,
        created,
        updated,
        removed,
        skippedInvalid,
        skippedNoTrack,
        fallbackTargets,
        skippedExpired,
        railAnimations,
        fallbackAnimations,
        railRepositions,
        jitterClamps,
        backtrackClamps,
        selectedVehicleId,
        sampleVehicles: vehicles.slice(0, 3).map((vehicle) => ({
          id: vehicle.id,
          route_id: vehicle.route_id,
          lat: vehicle.lat,
          lng: vehicle.lng,
          stale: vehicle.stale,
          position_source: vehicle.position_source,
          segment: vehicle.segment,
        })),
      }, null, 2));
    }
  }, [mode, vehicles, selectedVehicleId, subwayNetworkIndex]);

  // Route animation + camera rotation
  useEffect(() => {
    if (!map.current || !mapReadyRef.current || !overlayRef.current) return;

    const m = map.current;

    const TRAIL_LENGTH = 850;
    const FADE_IN = 700;

    type CompletedStep = { trip: Trip; revealedAt: number };

    function renderTrips(
      trips: Trip[],
      currentTime: number,
      completedSteps: CompletedStep[],
    ) {
      const layers: Layer[] = [];

      for (let i = 0; i < completedSteps.length; i++) {
        const cs = completedSteps[i];
        const fade = Math.min(Math.max((currentTime - cs.revealedAt) / FADE_IN, 0), 1);
        const eased = fade * fade * (3 - 2 * fade);
        layers.push(
          new PathLayer<Trip>({
            id: `jr-path-${i}`,
            data: [cs.trip],
            getPath: (t) => t.path,
            getColor: (t) => [t.color[0], t.color[1], t.color[2], 255],
            getWidth: (t) => t.width,
            widthUnits: "pixels",
            widthMinPixels: 3,
            opacity: eased,
            capRounded: true,
            jointRounded: true,
          }),
        );
      }

      layers.push(
        new TripsLayer<Trip>({
          id: "jr-trips",
          data: trips,
          getPath: (t) => t.path,
          getTimestamps: (t) => t.timestamps,
          getColor: (t) => t.color,
          getWidth: (t) => t.width,
          widthUnits: "pixels",
          opacity: 1,
          capRounded: true,
          jointRounded: true,
          trailLength: TRAIL_LENGTH,
          currentTime,
          fadeTrail: true,
        }),
      );

      setRouteDeckLayers(layers);
    }

    function stopAnimation() {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = null;
      }
    }

    function stopAll() {
      stopRotation(rotationRefs);
      stopAnimation();
    }

    function clearRouteFromMap() {
      clearBadges(stationMarkersRef.current);
      setRouteDeckLayers([]);
    }

    if (!routeData) {
      stopAll();
      clearRouteFromMap();
      return stopAll;
    }

    if (isSpeaking && routeData) {
      stopAll();
      clearRouteFromMap();

      const steps = routeData.steps;
      if (!steps || steps.length === 0) return stopAll;

      const { trips, stepCoords, stepEndTimes, totalDuration } = buildTrips(steps);

      const allCoords = stepCoords.flat();
      if (allCoords.length > 0) {
        flyToRoute(m, allCoords);
      }

      const userOrigin: [number, number] = originRef.current || allCoords[0] || [0, 0];
      const endTime = totalDuration + TRAIL_LENGTH;
      const startTime = performance.now();
      let done = false;

      const stepTripIndex = new Map<number, number>();
      {
        let ti = 0;
        for (let i = 0; i < steps.length; i++) {
          if (stepCoords[i].length >= 2) {
            stepTripIndex.set(i, ti);
            ti++;
          }
        }
      }

      const completedSteps: CompletedStep[] = [];
      const badgeKeys = new Set<string>();
      let badgeCount = 0;
      let nextStepToReveal = 0;

      function addBadgeIfNew(coords: [number, number], name: string, letter: string, color: string) {
        const key = `${coords[0].toFixed(4)},${coords[1].toFixed(4)}`;
        if (badgeKeys.has(key)) return;
        badgeKeys.add(key);
        const mk = addStationBadge(m, coords, name, letter, color, badgeCount++);
        stationMarkersRef.current.push(mk);
      }

      function revealStep(i: number) {
        const step = steps[i];
        const coords = stepCoords[i];

        const ti = stepTripIndex.get(i);
        if (ti !== undefined) {
          completedSteps.push({ trip: trips[ti], revealedAt: stepEndTimes[i] });
        }

        if (step.type === "SUBWAY" || step.type === "BUS") {
          const color = step.type === "SUBWAY"
            ? (step.line_color || getLineColor(step.train_line || ""))
            : "#0057B8";
          const letter = step.train_line || (step.type === "BUS" ? "BUS" : "?");

          if (step.departure_coords && step.departure_stop) {
            addBadgeIfNew(toLngLat(step.departure_coords), step.departure_stop, letter, color);
          } else if (coords.length > 0 && step.departure_stop) {
            addBadgeIfNew(coords[0], step.departure_stop, letter, color);
          }

          if (step.arrival_coords && step.arrival_stop) {
            addBadgeIfNew(toLngLat(step.arrival_coords), step.arrival_stop, letter, color);
          } else if (coords.length > 0 && step.arrival_stop) {
            addBadgeIfNew(coords[coords.length - 1], step.arrival_stop, letter, color);
          }

          if (step.intermediate_stops) {
            const labels = addIntermediateStopLabels(m, coords, step.intermediate_stops, color);
            stationMarkersRef.current.push(...labels);
          }
        }
      }

      function frame(now: number) {
        const e = now - startTime;
        const currentTime = Math.min(e, endTime);

        while (nextStepToReveal < steps.length && e >= stepEndTimes[nextStepToReveal]) {
          revealStep(nextStepToReveal);
          nextStepToReveal++;
        }

        renderTrips(trips, currentTime, completedSteps);

        if (e < endTime) {
          animFrameRef.current = requestAnimationFrame(frame);
          return;
        }

        if (done) return;
        done = true;

        const lastCoords = stepCoords[stepCoords.length - 1] || [];
        const destEnd = lastCoords[lastCoords.length - 1] || userOrigin;
        startRotation(m, destEnd, rotationRefs);
      }

      animFrameRef.current = requestAnimationFrame(frame);
    } else if (!isSpeaking && routeData) {
      const steps = routeData.steps;
      if (steps && steps.length > 0) {
        const { trips } = buildTrips(steps);
        setRouteDeckLayers(selectedRouteLayers(trips));
      }

      // Audio ended — stop rotation, fly back to origin facing toward route
      stopRotation(rotationRefs);
      const origin = originRef.current;
      if (origin) {
        const firstTransit = routeData.steps.find(
          (s) => (s.type === "SUBWAY" || s.type === "BUS") && s.departure_coords
        );
        const firstTransitCoords = firstTransit?.departure_coords
          ? toLngLat(firstTransit.departure_coords)
          : null;
        flyToOrigin(m, origin, firstTransitCoords);
      }
    }

    return stopAll;
  }, [isSpeaking, routeData]);

  // Destination marker — use primitive deps to avoid spurious re-runs
  const destLng = destCoords?.lng ?? null;
  const destLat = destCoords?.lat ?? null;

  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;

    if (destMarker.current) {
      destMarker.current.remove();
      destMarker.current = null;
    }

    if (destLng == null || destLat == null || !isFinite(destLng) || !isFinite(destLat)) {
      return;
    }

    const el = createOrb("#FF3B30", "rgba(255, 59, 48, 0.4)");

    destMarker.current = new maplibregl.Marker({ element: el, anchor: "center" })
      .setLngLat([destLng, destLat])
      .addTo(map.current);

    return () => {
      if (destMarker.current) {
        destMarker.current.remove();
        destMarker.current = null;
      }
    };
  }, [destLng, destLat]);

  return (
    <div ref={mapContainer} className="absolute inset-0 w-full h-full" />
  );
}
