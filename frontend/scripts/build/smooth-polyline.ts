// frontend/scripts/build/smooth-polyline.ts
// Pure helper -- no fs, no globals. Rounds sharp single-vertex "elbow" corners
// in a polyline using endpoint-pinned Chaikin corner-cutting. Straight runs and
// gentle curves are returned byte-identical so the only geometry that moves is
// the kinky corners (e.g. the coarse OpenData elbows through the DeKalb
// interlocking that MapLibre's round line-join cannot smooth). Endpoints are
// NEVER moved, so feature-to-feature junctions stay coincident.

import type { Coordinate } from "./types.ts";

// Re-exported so existing consumers can keep importing `Coordinate` from here.
export type { Coordinate };

export type SmoothSharpCornersOptions = {
  angleThresholdDeg?: number;
  iterations?: number;
  ratio?: number;
  maxFilletM?: number;
};

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 111320;

function haversineM([lon1, lat1]: Coordinate, [lon2, lat2]: Coordinate): number {
  const toRad = (degrees: number) => (degrees * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

/**
 * Insert evenly-spaced points on any segment longer than maxSegM so a coarse
 * OpenData polyline (kilometer-scale chords) gains the intermediate vertices
 * that lane-offsetting and corner-smoothing need to work. Endpoints and
 * existing vertices are preserved; only extra collinear points are added. Pure.
 */
export function densifyLongSegments(
  coords: Coordinate[],
  maxSegM = 250,
  stepM = 40,
): Coordinate[] {
  if (!Array.isArray(coords) || coords.length < 2) return coords;
  let changed = false;
  const out: Coordinate[] = [coords[0]];
  for (let i = 1; i < coords.length; i += 1) {
    const a = coords[i - 1];
    const b = coords[i];
    const seg = haversineM(a, b);
    if (seg > maxSegM) {
      const steps = Math.ceil(seg / stepM);
      for (let k = 1; k < steps; k += 1) {
        const t = k / steps;
        out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
      }
      changed = true;
    }
    out.push(b);
  }
  return changed ? out : coords;
}

// Interior turn angle in degrees at vertex b for a->b->c.
// 0 = straight through, 90 = right-angle, 180 = full reversal.
function turnDeg(a: Coordinate, b: Coordinate, c: Coordinate): number {
  const mPerLng = Math.cos((b[1] * Math.PI) / 180) * M_PER_DEG_LAT;
  const ax = (a[0] - b[0]) * mPerLng;
  const ay = (a[1] - b[1]) * M_PER_DEG_LAT;
  const cx = (c[0] - b[0]) * mPerLng;
  const cy = (c[1] - b[1]) * M_PER_DEG_LAT;
  const la = Math.hypot(ax, ay);
  const lc = Math.hypot(cx, cy);
  if (la < 1e-9 || lc < 1e-9) return 0;
  let cos = (ax * cx + ay * cy) / (la * lc);
  cos = Math.max(-1, Math.min(1, cos));
  return 180 - (Math.acos(cos) * 180) / Math.PI;
}

export function countSharpCorners(coords: Coordinate[], angleThresholdDeg = 35): number {
  if (!Array.isArray(coords) || coords.length < 3) return 0;
  let count = 0;
  for (let i = 1; i < coords.length - 1; i += 1) {
    if (turnDeg(coords[i - 1], coords[i], coords[i + 1]) >= angleThresholdDeg) {
      count += 1;
    }
  }
  return count;
}

// A point a capped fraction of the way from `corner` toward `toward`.
function cutToward(
  corner: Coordinate,
  toward: Coordinate,
  ratio: number,
  maxFilletM: number,
): Coordinate {
  const segM = haversineM(corner, toward);
  const t = segM > 0 ? Math.min(ratio, maxFilletM / segM) : 0;
  return [corner[0] + (toward[0] - corner[0]) * t, corner[1] + (toward[1] - corner[1]) * t];
}

/**
 * Endpoint-pinned, threshold-gated Chaikin corner smoothing.
 */
export function smoothSharpCorners(
  coords: Coordinate[],
  options: SmoothSharpCornersOptions = {},
): Coordinate[] {
  const {
    angleThresholdDeg = 35,
    iterations = 2,
    ratio = 0.22,
    maxFilletM = 18,
  } = options;
  if (!Array.isArray(coords) || coords.length < 3) return coords;

  let pts = coords;
  let changed = false;
  for (let it = 0; it < iterations; it += 1) {
    const sharp = new Array<boolean>(pts.length).fill(false);
    let any = false;
    for (let i = 1; i < pts.length - 1; i += 1) {
      if (turnDeg(pts[i - 1], pts[i], pts[i + 1]) >= angleThresholdDeg) {
        sharp[i] = true;
        any = true;
      }
    }
    if (!any) break;
    const out: Coordinate[] = [pts[0]];
    for (let i = 1; i < pts.length - 1; i += 1) {
      if (sharp[i]) {
        out.push(cutToward(pts[i], pts[i - 1], ratio, maxFilletM)); // back cut
        out.push(cutToward(pts[i], pts[i + 1], ratio, maxFilletM)); // forward cut
        changed = true;
      } else {
        out.push(pts[i]);
      }
    }
    out.push(pts[pts.length - 1]);
    pts = out;
  }
  return changed ? pts : coords;
}
