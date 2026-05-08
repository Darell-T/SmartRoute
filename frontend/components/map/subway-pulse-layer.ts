import type { Layer } from "@deck.gl/core";
import { TripsLayer } from "@deck.gl/geo-layers";

export const SUBWAY_PULSE_LOOP_MS = 12_000;

// Geographic anchor for "the heart of NYC" — the convergence point all pulses
// flow toward. Times Sq area: roughly equidistant from major express trunk
// terminals and visually iconic. Tunable.
export const MANHATTAN_HEART: [number, number] = [-73.985, 40.755];

// Four pulses per loop, evenly spaced. Denser than the previous 3 for an
// "electric current" feel — multiple sparks chasing each other along the line.
const PULSE_OFFSETS = [0, 3_000, 6_000, 9_000];

// Halo trail (outer, route-color, soft).
const HALO_TRAIL_LENGTH = 160;
const HALO_PULSE_WIDTH = 6.5;
const HALO_BACKGROUND_PULSE_WIDTH = 4.2;
const HALO_EMPHASIZED_PULSE_WIDTH = 8.2;
const HALO_IDLE_ALPHA = 168;
const HALO_BACKGROUND_ALPHA = 88;
const HALO_EMPHASIZED_ALPHA = 220;

// Core trail (inner, near-white, sharp). Narrower + shorter trail = "spark
// head" feel chasing along inside the halo.
const CORE_TRAIL_LENGTH = 70;
const CORE_PULSE_WIDTH = 2.4;
const CORE_BACKGROUND_PULSE_WIDTH = 1.6;
const CORE_EMPHASIZED_PULSE_WIDTH = 3.0;
const CORE_IDLE_ALPHA = 244;
const CORE_BACKGROUND_ALPHA = 140;
const CORE_EMPHASIZED_ALPHA = 252;

// Legacy aliases preserved for resolveSubwayPulseVisuals — that function
// returns the HALO visuals (current external contract).
const TRAIL_LENGTH = HALO_TRAIL_LENGTH;
const PULSE_WIDTH = HALO_PULSE_WIDTH;
const BACKGROUND_PULSE_WIDTH = HALO_BACKGROUND_PULSE_WIDTH;
const EMPHASIZED_PULSE_WIDTH = HALO_EMPHASIZED_PULSE_WIDTH;
const IDLE_PULSE_ALPHA = HALO_IDLE_ALPHA;
const BACKGROUND_PULSE_ALPHA = HALO_BACKGROUND_ALPHA;
const EMPHASIZED_PULSE_ALPHA = HALO_EMPHASIZED_ALPHA;

export type SubwayPulseTrip = {
  id: string;
  routeId: string;
  path: [number, number][];
  timestamps: number[];
  color: [number, number, number, number];
  width: number;
};

export type SubwayPulseLayerOptions = {
  emphasizedRouteIds?: Iterable<string>;
};

type SubwayFeatureProperties = {
  route_id?: string;
  display_route?: string;
  shape_id?: string;
  color?: string;
};

function isLineStringFeature(
  feature: GeoJSON.Feature,
): feature is GeoJSON.Feature<GeoJSON.LineString, SubwayFeatureProperties> {
  return feature.geometry?.type === "LineString";
}

function parseHexColor(hex: string | undefined): [number, number, number] {
  const fallback: [number, number, number] = [156, 207, 191];
  if (!hex) return fallback;
  const clean = hex.replace("#", "");
  const normalized =
    clean.length === 3
      ? clean
          .split("")
          .map((part) => part + part)
          .join("")
      : clean;
  if (normalized.length !== 6) return fallback;
  const value = Number.parseInt(normalized, 16);
  if (!Number.isFinite(value)) return fallback;
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function normalizePulseRouteId(routeId: string) {
  const normalized = routeId.trim().toUpperCase();
  if (normalized === "6D") return "6X";
  if (normalized === "7D") return "7X";
  if (normalized === "FD" || normalized === "FX") return "F";
  if (normalized === "FS" || normalized === "GS" || normalized === "H") return "S";
  if (normalized === "SIR") return "SI";
  return normalized;
}

function brighten(
  color: [number, number, number],
): [number, number, number, number] {
  return [
    Math.min(255, Math.round(color[0] + (255 - color[0]) * 0.22)),
    Math.min(255, Math.round(color[1] + (255 - color[1]) * 0.22)),
    Math.min(255, Math.round(color[2] + (255 - color[2]) * 0.22)),
    IDLE_PULSE_ALPHA,
  ];
}

function normalizedRouteSet(routeIds?: Iterable<string>) {
  const set = new Set<string>();
  if (!routeIds) return set;
  for (const routeId of routeIds) {
    const normalized = normalizePulseRouteId(routeId);
    if (normalized) set.add(normalized);
  }
  return set;
}

export function resolveSubwayPulseVisuals(
  trip: SubwayPulseTrip,
  emphasizedRouteIds?: Iterable<string>,
) {
  const emphasized = normalizedRouteSet(emphasizedRouteIds);
  return resolveSubwayPulseVisualsForSet(trip, emphasized);
}

function resolveSubwayPulseVisualsForSet(
  trip: SubwayPulseTrip,
  emphasized: Set<string>,
) {
  const hasEmphasis = emphasized.size > 0;
  const isEmphasized = emphasized.has(normalizePulseRouteId(trip.routeId));
  const alpha = !hasEmphasis
    ? IDLE_PULSE_ALPHA
    : isEmphasized
      ? EMPHASIZED_PULSE_ALPHA
      : BACKGROUND_PULSE_ALPHA;
  const width = !hasEmphasis
    ? trip.width
    : isEmphasized
      ? EMPHASIZED_PULSE_WIDTH
      : BACKGROUND_PULSE_WIDTH;

  return {
    color: [trip.color[0], trip.color[1], trip.color[2], alpha] as [
      number,
      number,
      number,
      number,
    ],
    width,
  };
}

/**
 * Returns the BRIGHT-CORE color tuple for a trip — symmetric to
 * `resolveSubwayPulseVisuals` (which returns the halo color). Used by the
 * core TripsLayer to render a near-white spark head inside the route-color
 * halo.
 *
 * Computes the core color from the trip's halo color (which is already
 * route-color-brightened-22%) by re-pushing it 75% toward white. This means
 * the core auto-derives from whatever the halo color is — no separate hex
 * constants to maintain.
 */
export function resolveSubwayPulseCoreColor(
  trip: SubwayPulseTrip,
  emphasizedRouteIds?: Iterable<string>,
): [number, number, number, number] {
  const emphasized = normalizedRouteSet(emphasizedRouteIds);
  const hasEmphasis = emphasized.size > 0;
  const isEmphasized = emphasized.has(normalizePulseRouteId(trip.routeId));
  const alpha = !hasEmphasis
    ? CORE_IDLE_ALPHA
    : isEmphasized
      ? CORE_EMPHASIZED_ALPHA
      : CORE_BACKGROUND_ALPHA;
  // Recover the original hue from the halo color (route color brightened 22%
  // toward white) and push it 75% toward white. Inline the inversion so we
  // don't have to store the original color on the trip.
  const debrightened: [number, number, number] = [
    Math.max(0, Math.round((trip.color[0] - 255 * 0.22) / (1 - 0.22))),
    Math.max(0, Math.round((trip.color[1] - 255 * 0.22) / (1 - 0.22))),
    Math.max(0, Math.round((trip.color[2] - 255 * 0.22) / (1 - 0.22))),
  ];
  const r = Math.min(255, Math.round(debrightened[0] + (255 - debrightened[0]) * 0.75));
  const g = Math.min(255, Math.round(debrightened[1] + (255 - debrightened[1]) * 0.75));
  const b = Math.min(255, Math.round(debrightened[2] + (255 - debrightened[2]) * 0.75));
  return [r, g, b, alpha];
}

function resolveSubwayPulseCoreWidth(
  trip: SubwayPulseTrip,
  emphasizedRouteIds?: Iterable<string>,
): number {
  const emphasized = normalizedRouteSet(emphasizedRouteIds);
  const hasEmphasis = emphasized.size > 0;
  const isEmphasized = emphasized.has(normalizePulseRouteId(trip.routeId));
  if (!hasEmphasis) return CORE_PULSE_WIDTH;
  return isEmphasized ? CORE_EMPHASIZED_PULSE_WIDTH : CORE_BACKGROUND_PULSE_WIDTH;
}

function segmentDistances(path: [number, number][]) {
  const distances: number[] = [0];
  let total = 0;

  for (let i = 1; i < path.length; i++) {
    const [prevLng, prevLat] = path[i - 1];
    const [lng, lat] = path[i];
    const x = (lng - prevLng) * Math.cos(((lat + prevLat) / 2) * (Math.PI / 180));
    const y = lat - prevLat;
    total += Math.sqrt(x * x + y * y);
    distances.push(total);
  }

  return { distances, total };
}

function timestampsForPath(path: [number, number][], offset: number) {
  const { distances, total } = segmentDistances(path);
  if (total <= 0) return path.map(() => -offset);

  return distances.map(
    (distance) => (distance / total) * SUBWAY_PULSE_LOOP_MS - offset,
  );
}

function normalizePath(
  coordinates: GeoJSON.LineString["coordinates"],
): [number, number][] {
  return coordinates
    .filter(
      (coordinate): coordinate is [number, number] =>
        coordinate.length >= 2 &&
        Number.isFinite(coordinate[0]) &&
        Number.isFinite(coordinate[1]),
    )
    .map(([lng, lat]) => [lng, lat]);
}

/**
 * Find the vertex closest to MANHATTAN_HEART and split the path into one or
 * two sub-paths that each flow FROM a terminus TOWARD the apex.
 *
 * Single-end cases (apex at start or end of the path):
 *   - apex at end (idx N-1): return [path] as-is — already terminus → apex.
 *   - apex at start (idx 0): return [reversed(path)] so apex is the last vertex.
 *
 * Through-route case (apex mid-path):
 *   - return [path[0..apex], reversed(path[apex..N-1])] — two paths, both
 *     ending at the apex coord.
 *
 * Distance is measured in lat-aware meters so cosine of latitude doesn't
 * distort the apex selection at NYC latitudes.
 */
export function splitPathAtManhattanApex(
  path: [number, number][],
): [number, number][][] {
  if (path.length < 2) return [];

  // Find apex index (vertex closest to MANHATTAN_HEART in meter-space).
  let apexIdx = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  const heartLat = MANHATTAN_HEART[1];
  const metersPerDegreeLat = 111_320;
  for (let i = 0; i < path.length; i += 1) {
    const [lng, lat] = path[i];
    const avgLat = (lat + heartLat) / 2;
    const metersPerDegreeLng =
      metersPerDegreeLat * Math.cos((avgLat * Math.PI) / 180);
    const dx = (lng - MANHATTAN_HEART[0]) * metersPerDegreeLng;
    const dy = (lat - heartLat) * metersPerDegreeLat;
    const d = Math.hypot(dx, dy);
    if (d < bestDistance) {
      bestDistance = d;
      apexIdx = i;
    }
  }

  // Single-end cases: apex coincides with one of the path's endpoints.
  if (apexIdx === path.length - 1) {
    return [path.map((coord) => [coord[0], coord[1]] as [number, number])];
  }
  if (apexIdx === 0) {
    return [
      path
        .slice()
        .reverse()
        .map((coord) => [coord[0], coord[1]] as [number, number]),
    ];
  }

  // Through-route case: split at apex; both halves flow terminus → apex.
  const firstHalf = path
    .slice(0, apexIdx + 1)
    .map((coord) => [coord[0], coord[1]] as [number, number]);
  const secondHalf = path
    .slice(apexIdx)
    .reverse()
    .map((coord) => [coord[0], coord[1]] as [number, number]);

  return [firstHalf, secondHalf];
}

export function buildSubwayPulseTrips(
  featureCollection: GeoJSON.FeatureCollection,
): SubwayPulseTrip[] {
  const trips: SubwayPulseTrip[] = [];

  for (const feature of featureCollection.features) {
    if (!isLineStringFeature(feature)) continue;
    const path = normalizePath(feature.geometry.coordinates);
    if (path.length < 2) continue;

    const routeId = normalizePulseRouteId(
      feature.properties?.route_id ?? feature.properties?.display_route ?? "route",
    );
    const shapeId = feature.properties?.shape_id ?? String(trips.length);
    const color = brighten(parseHexColor(feature.properties?.color));

    // Split into one or two sub-paths flowing terminus → Manhattan apex. A
    // through-route (e.g. 4 train Bronx ↔ Brooklyn) yields two paths that
    // both end at the apex; a one-sided route (e.g. L train ending in
    // Manhattan) yields a single path. See `splitPathAtManhattanApex` for
    // the exact rules.
    const subPaths = splitPathAtManhattanApex(path);
    for (let halfIdx = 0; halfIdx < subPaths.length; halfIdx += 1) {
      const subPath = subPaths[halfIdx];
      if (subPath.length < 2) continue;
      for (const offset of PULSE_OFFSETS) {
        trips.push({
          id: `${routeId}-${shapeId}-${halfIdx}-${offset}`,
          routeId,
          path: subPath,
          timestamps: timestampsForPath(subPath, offset),
          // halo color — core derives from this via resolveSubwayPulseCoreColor
          color,
          width: HALO_PULSE_WIDTH,
        });
      }
    }
  }

  return trips;
}

/**
 * Build the electric-current pulse: TWO stacked TripsLayers.
 *
 *   [0] halo: outer route-color trail, wide+soft, longer trail (160) — this is
 *       the "energy bolt" body.
 *   [1] core: inner near-white trail, narrow+sharp, short trail (70) — this is
 *       the "spark head" chasing along inside the halo.
 *
 * Both layers consume the SAME `trips` array (one source of truth — no
 * duplicate trip-building). The halo color comes from `trip.color` (already
 * brightened-22%); the core color is derived per-frame via
 * `resolveSubwayPulseCoreColor`.
 *
 * Caller spreads the returned array into deck.gl's layers list. Layer order
 * matters: halo MUST come before core so the core renders on top.
 */
export function createSubwayPulseLayers(
  trips: SubwayPulseTrip[],
  currentTime: number,
  options: SubwayPulseLayerOptions = {},
): Layer[] {
  const emphasizedRouteIds = normalizedRouteSet(options.emphasizedRouteIds);

  const halo = new TripsLayer<SubwayPulseTrip>({
    id: "sr-subway-pulse-trips-halo",
    data: trips,
    getPath: (trip) => trip.path,
    getTimestamps: (trip) => trip.timestamps,
    getColor: (trip) =>
      resolveSubwayPulseVisualsForSet(trip, emphasizedRouteIds).color,
    getWidth: (trip) =>
      resolveSubwayPulseVisualsForSet(trip, emphasizedRouteIds).width,
    widthUnits: "pixels",
    widthMinPixels: 3.2,
    opacity: 0.62,
    capRounded: true,
    jointRounded: true,
    trailLength: HALO_TRAIL_LENGTH,
    currentTime,
    fadeTrail: true,
    parameters: {
      depthTest: false,
    },
  } as ConstructorParameters<typeof TripsLayer<SubwayPulseTrip>>[0]);

  const core = new TripsLayer<SubwayPulseTrip>({
    id: "sr-subway-pulse-trips-core",
    data: trips,
    getPath: (trip) => trip.path,
    getTimestamps: (trip) => trip.timestamps,
    getColor: (trip) => resolveSubwayPulseCoreColor(trip, emphasizedRouteIds),
    getWidth: (trip) => resolveSubwayPulseCoreWidth(trip, emphasizedRouteIds),
    widthUnits: "pixels",
    widthMinPixels: 1.6,
    opacity: 0.96,
    capRounded: true,
    jointRounded: true,
    trailLength: CORE_TRAIL_LENGTH,
    currentTime,
    fadeTrail: true,
    parameters: {
      depthTest: false,
    },
  } as ConstructorParameters<typeof TripsLayer<SubwayPulseTrip>>[0]);

  return [halo, core];
}
