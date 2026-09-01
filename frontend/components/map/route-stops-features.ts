// Pure feature builders for the picked route's stop markers and walk
// segments. NO maplibre import -- this module is exercised directly by
// node --test (route-stops.check.mjs); the MapLibre layer management lives
// in route-stops.ts.

import polyline from "@mapbox/polyline";
import type { RouteStep } from "@/types";
import { isTransitStep } from "@/lib/route-planning";

const BUS_COLOR = "#0057B8";
const FALLBACK_COLOR = "#8B939E";

function decode(encoded: string): [number, number][] {
  return polyline.decode(encoded).map(([lat, lng]: [number, number]) => [lng, lat]);
}

export function interpolateAlongLine(
  coords: [number, number][],
  progress: number,
): [number, number] {
  if (coords.length < 2) return coords[0] || [0, 0];
  const segLens: number[] = [];
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    const dx = coords[i][0] - coords[i - 1][0];
    const dy = coords[i][1] - coords[i - 1][1];
    const d = Math.sqrt(dx * dx + dy * dy);
    segLens.push(d);
    total += d;
  }
  if (total === 0) return coords[0];
  const targetDist = progress * total;
  let traveled = 0;
  for (let i = 0; i < segLens.length; i++) {
    if (traveled + segLens[i] >= targetDist) {
      const t = (targetDist - traveled) / segLens[i];
      return [
        coords[i][0] + t * (coords[i + 1][0] - coords[i][0]),
        coords[i][1] + t * (coords[i + 1][1] - coords[i][1]),
      ];
    }
    traveled += segLens[i];
  }
  return coords[coords.length - 1];
}

/** Nearest point on a polyline to an arbitrary point (planar lng/lat, which
 *  is fine for snapping a coordinate that is only a few metres off the line).
 *  Used to pin curbside stop coordinates onto the drawn route geometry. */
function nearestPointOnPolyline(
  coords: [number, number][],
  pt: [number, number],
): [number, number] {
  if (coords.length === 0) return pt;
  if (coords.length === 1) return coords[0];
  let best = coords[0];
  let bestD = Infinity;
  for (let i = 1; i < coords.length; i++) {
    const a = coords[i - 1];
    const b = coords[i];
    const abx = b[0] - a[0];
    const aby = b[1] - a[1];
    const len2 = abx * abx + aby * aby;
    let t = len2 > 0 ? ((pt[0] - a[0]) * abx + (pt[1] - a[1]) * aby) / len2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const px = a[0] + t * abx;
    const py = a[1] + t * aby;
    const dx = pt[0] - px;
    const dy = pt[1] - py;
    const d = dx * dx + dy * dy;
    if (d < bestD) {
      bestD = d;
      best = [px, py];
    }
  }
  return best;
}

export type ColorResolver = (step: RouteStep) => string;

function defaultColorFor(step: RouteStep): string {
  if (step.type === "BUS") return step.line_color || BUS_COLOR;
  return step.line_color || FALLBACK_COLOR;
}

interface RouteStopProps {
  name: string;
  color: string;
  line: string;
  interpolated: boolean;
}

interface TransitPathProps {
  color: string;
  width: number;
}

/** LineString per transit step. Keeping selected-route paths in MapLibre
 *  alongside walk dashes and stop dots avoids a second WebGL renderer and
 *  guarantees every route layer shares the same camera and style lifecycle. */
export function buildTransitPathFeatures(
  steps: RouteStep[] | undefined,
  colorFor: ColorResolver = defaultColorFor,
): GeoJSON.FeatureCollection<GeoJSON.LineString, TransitPathProps> {
  const features: GeoJSON.Feature<GeoJSON.LineString, TransitPathProps>[] = [];

  for (const step of steps ?? []) {
    if (!isTransitStep(step)) continue;
    const encoded = step.polyline?.encodedPolyline;
    if (!encoded) continue;
    const coordinates = decode(encoded).filter(
      (point) => Number.isFinite(point[0]) && Number.isFinite(point[1]),
    );
    if (coordinates.length < 2) continue;

    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates },
      properties: {
        color: colorFor(step),
        width: step.type === "BUS" ? 5 : 6,
      },
    });
  }

  return { type: "FeatureCollection", features };
}

function stopPoint(
  coordinates: [number, number],
  name: string,
  color: string,
  line: string,
  interpolated: boolean,
): GeoJSON.Feature<GeoJSON.Point, RouteStopProps> {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates },
    properties: { name, color, line, interpolated },
  };
}

function locatedStopFeatures(
  step: RouteStep,
  color: string,
  line: string,
): GeoJSON.Feature<GeoJSON.Point, RouteStopProps>[] | null {
  const located = step.intermediate_stop_locations ?? [];
  if (located.length === 0) return null;
  const encoded = step.polyline?.encodedPolyline;
  const lineCoords = encoded ? decode(encoded) : null;
  const snap = Boolean(lineCoords && lineCoords.length >= 2);
  const features: GeoJSON.Feature<GeoJSON.Point, RouteStopProps>[] = [];
  for (const stop of located) {
    if (typeof stop.lat !== "number" || typeof stop.lng !== "number") continue;
    const raw: [number, number] = [stop.lng, stop.lat];
    const coordinates = snap && lineCoords ? nearestPointOnPolyline(lineCoords, raw) : raw;
    features.push(stopPoint(coordinates, stop.name, color, line, false));
  }
  return features;
}

function interpolatedStopFeatures(
  names: string[],
  coords: [number, number][],
  color: string,
  line: string,
): GeoJSON.Feature<GeoJSON.Point, RouteStopProps>[] {
  const lastIndex = names.length - 1;
  return names.map((name, index) =>
    stopPoint(interpolateAlongLine(coords, index / lastIndex), name, color, line, true),
  );
}

function countedStopFeatures(
  count: number,
  coords: [number, number][],
  color: string,
  line: string,
): GeoJSON.Feature<GeoJSON.Point, RouteStopProps>[] {
  const lastIndex = count - 1;
  return Array.from({ length: count }, (_, index) =>
    stopPoint(interpolateAlongLine(coords, index / lastIndex), "", color, line, true),
  );
}

function transitLineId(step: RouteStep): string {
  return (step.train_line || step.route_id || "").toUpperCase();
}

function decodedPolyline(step: RouteStep): [number, number][] | null {
  const encoded = step.polyline?.encodedPolyline;
  if (!encoded) return null;
  const coords = decode(encoded);
  return coords.length >= 2 ? coords : null;
}

/** Real coordinates win; names-only legs interpolate along the polyline so
 *  dots stay on the drawn line for un-enriched payloads. */
function stepStopFeatures(
  step: RouteStep,
  color: string,
  line: string,
): GeoJSON.Feature<GeoJSON.Point, RouteStopProps>[] {
  const located = locatedStopFeatures(step, color, line);
  if (located) return located;
  const coords = decodedPolyline(step);
  if (!coords) return [];
  const names = step.intermediate_stops ?? [];
  if (names.length >= 2) return interpolatedStopFeatures(names, coords, color, line);
  const stopCount = typeof step.stop_count === "number" ? step.stop_count : 0;
  const count = stopCount + 1;
  return count >= 2 ? countedStopFeatures(count, coords, color, line) : [];
}

export function buildRouteStopFeatures(
  steps: RouteStep[] | undefined,
  colorFor: ColorResolver = defaultColorFor,
): GeoJSON.FeatureCollection<GeoJSON.Point, RouteStopProps> {
  const features: GeoJSON.Feature<GeoJSON.Point, RouteStopProps>[] = [];
  for (const step of steps ?? []) {
    if (!isTransitStep(step)) continue;
    features.push(...stepStopFeatures(step, colorFor(step), transitLineId(step)));
  }
  if (features.length > 0) {
    features[0].properties.name = "";
    features[features.length - 1].properties.name = "";
  }
  return { type: "FeatureCollection", features };
}

export function buildWalkFeatures(
  steps: RouteStep[] | undefined,
): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  const features: GeoJSON.Feature<GeoJSON.LineString>[] = [];
  for (const step of steps ?? []) {
    if (step.type !== "WALK") continue;
    const encoded = step.polyline?.encodedPolyline;
    if (!encoded) continue;
    const coords = decode(encoded);
    if (coords.length < 2) continue;
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: coords },
      properties: {},
    });
  }
  return { type: "FeatureCollection", features };
}
