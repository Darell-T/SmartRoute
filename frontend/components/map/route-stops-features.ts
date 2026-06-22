// Pure feature builders for the picked route's stop markers and walk
// segments. NO maplibre import -- this module is exercised directly by
// node --test (route-stops.check.mjs); the MapLibre layer management lives
// in route-stops.ts.

import polyline from "@mapbox/polyline";
import type { RouteStep } from "@/types";

const BUS_COLOR = "#0057B8";
const FALLBACK_COLOR = "#8B939E";

function decode(encoded: string): [number, number][] {
  return polyline.decode(encoded).map(([lat, lng]: [number, number]) => [lng, lat]);
}

/** Interpolate a position along a coordinate array given progress 0..1.
 *  (Moved from station-badges.ts so the pure builders can share it.) */
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

/**
 * One Point feature per intermediate stop of every transit step. Real
 * coordinates (intermediate_stop_locations) win; when only names exist the
 * positions are interpolated evenly along the step's decoded polyline --
 * approximate, but it keeps dots on the line for un-enriched payloads.
 */
export function buildRouteStopFeatures(
  steps: RouteStep[] | undefined,
  colorFor: ColorResolver = defaultColorFor,
): GeoJSON.FeatureCollection<GeoJSON.Point, RouteStopProps> {
  const features: GeoJSON.Feature<GeoJSON.Point, RouteStopProps>[] = [];

  for (const step of steps ?? []) {
    if (step.type !== "SUBWAY" && step.type !== "BUS") continue;
    const color = colorFor(step);
    const line = (step.train_line || step.route_id || "").toUpperCase();

    const located = step.intermediate_stop_locations ?? [];
    if (located.length > 0) {
      // Stop coords are curbside positions, offset from the road/track
      // centerline Google returns as the step geometry. Snap each onto the
      // decoded polyline so the dot sits exactly on the drawn line.
      const encoded = step.polyline?.encodedPolyline;
      const lineCoords = encoded ? decode(encoded) : null;
      const snap = lineCoords && lineCoords.length >= 2;
      for (const stop of located) {
        if (typeof stop.lat !== "number" || typeof stop.lng !== "number") continue;
        const raw: [number, number] = [stop.lng, stop.lat];
        const coordinates = snap ? nearestPointOnPolyline(lineCoords, raw) : raw;
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates },
          properties: { name: stop.name, color, line, interpolated: false },
        });
      }
      continue;
    }

    const encoded = step.polyline?.encodedPolyline;
    if (!encoded) continue;
    const coords = decode(encoded);
    if (coords.length < 2) continue;

    const names = step.intermediate_stops ?? [];
    if (names.length >= 2) {
      const lastIndex = names.length - 1;
      for (let i = 0; i < names.length; i++) {
        const point = interpolateAlongLine(coords, i / lastIndex);
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: point },
          properties: { name: names[i], color, line, interpolated: true },
        });
      }
      continue;
    }

    // Last-resort fallback: GTFS enrichment gave neither coords nor names (e.g.
    // a leg whose station lookup came back empty), but Google's step still
    // carries the stop count + the polyline. Place that many evenly-spaced
    // (unlabeled) dots so EVERY transit leg shows its stops regardless of the
    // enrichment -- the dots no longer depend on the GTFS pipeline succeeding.
    const stopCount = typeof step.stop_count === "number" ? step.stop_count : 0;
    const count = stopCount + 1; // board stop + each subsequent stop (incl. alight)
    if (count >= 2) {
      const lastIndex = count - 1;
      for (let i = 0; i < count; i++) {
        const point = interpolateAlongLine(coords, i / lastIndex);
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: point },
          properties: { name: "", color, line, interpolated: true },
        });
      }
    }
  }

  // The trip's first and last stops are drawn as roundel badges (board /
  // arrive), so blank their labels here -- the dot still anchors the spot but
  // the station name is not printed twice next to the badge.
  if (features.length > 0) {
    features[0].properties.name = "";
    features[features.length - 1].properties.name = "";
  }

  return { type: "FeatureCollection", features };
}

/** LineString per WALK step (rendered as a dashed MapLibre line). */
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
