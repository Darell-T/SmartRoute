// Pure helper -- no fs, no globals.
//
// Apple/Transit-style tight-curve simplification. Some real revenue track makes
// extremely tight hairpins (e.g. the 5 at the 149 St / Mott Haven curve, the red
// 148 St yard-lead curve). Those are faithful geometry, but at map scale a
// sub-radius hairpin reads as a teardrop/hook scribble. Apple and Transit App
// trade geometric fidelity for legibility by rounding such hairpins into smooth
// gentle arcs. This pass does the same: where a polyline packs a lot of total
// turning into a short arc (a small local radius), it relaxes that run toward a
// gentler arc with Laplacian smoothing. Straight runs and gentle curves are
// returned byte-identical, and BOTH endpoints are pinned so feature-to-feature
// junctions never move (connectivity is GTFS-topology-based, not geometry-based).

import type { Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;

function mPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function bearingDeg(a: Position, b: Position): number {
  const k = mPerDegLng((a[1] + b[1]) / 2);
  return (Math.atan2((b[1] - a[1]) * M_PER_DEG_LAT, (b[0] - a[0]) * k) * 180) / Math.PI;
}

function turnAt(p: Position, c: Position, n: Position): number {
  let t = bearingDeg(c, n) - bearingDeg(p, c);
  while (t > 180) t -= 360;
  while (t < -180) t += 360;
  return t;
}

function segLengths(coords: Position[]): number[] {
  const seg = new Array(coords.length - 1);
  for (let i = 0; i < coords.length - 1; i += 1) seg[i] = haversineM(coords[i], coords[i + 1]);
  return seg;
}

// Highest "turn density" (sum of |turn| in degrees per meter of arc) found in any
// window of +/- windowM around an interior vertex. A sharp hairpin scores high;
// a straight or gently curving line scores near zero.
export function maxTurnDensityDegPerM(coords: Position[], windowM = 40): number {
  if (!Array.isArray(coords) || coords.length < 3) return 0;
  const n = coords.length;
  const seg = segLengths(coords);
  const turn = new Array(n).fill(0);
  for (let i = 1; i < n - 1; i += 1) turn[i] = Math.abs(turnAt(coords[i - 1], coords[i], coords[i + 1]));
  let best = 0;
  for (let i = 1; i < n - 1; i += 1) {
    let sum = turn[i];
    let d = 0;
    for (let l = i; l > 1; l -= 1) {
      if (d + seg[l - 1] > windowM) break;
      d += seg[l - 1];
      sum += turn[l - 1];
    }
    let dr = 0;
    for (let r = i; r < n - 2; r += 1) {
      if (dr + seg[r] > windowM) break;
      dr += seg[r];
      sum += turn[r + 1];
    }
    const arc = Math.max(d + dr, 1e-6);
    best = Math.max(best, sum / arc);
  }
  return best;
}

type SimplifyTightCurvesOptions = {
  tightTurnDeg?: number;
  windowM?: number;
  iterations?: number;
  lambda?: number;
  marginVerts?: number;
};

/**
 * Round tight hairpins into gentler arcs. Pure; returns the SAME array reference
 * when nothing qualifies (straight / gentle-only inputs).
 *
 * @param {Array<[number,number]>} coords
 * @param {object} [options]
 * @param {number} [options.tightTurnDeg=70] total |turn| within the window that marks a vertex "tight"
 * @param {number} [options.windowM=50] arc half-window used to accumulate turning
 * @param {number} [options.iterations=16] Laplacian relaxation iterations on tight vertices
 * @param {number} [options.lambda=0.5] relaxation strength (0..1)
 * @param {number} [options.marginVerts=1] how many vertices to extend each tight run by, for blending
 * @returns {Array<[number,number]>}
 */
export function simplifyTightCurves(coords: Position[], options: SimplifyTightCurvesOptions = {}): Position[] {
  const {
    tightTurnDeg = 70,
    windowM = 50,
    iterations = 16,
    lambda = 0.5,
    marginVerts = 1,
  } = options;
  if (!Array.isArray(coords) || coords.length < 5) return coords;

  const n = coords.length;
  const seg = segLengths(coords);
  const turn = new Array(n).fill(0);
  for (let i = 1; i < n - 1; i += 1) turn[i] = Math.abs(turnAt(coords[i - 1], coords[i], coords[i + 1]));

  // Mark vertices whose neighbourhood (+/- windowM) packs >= tightTurnDeg of turning.
  const tight = new Array(n).fill(false);
  let any = false;
  for (let i = 1; i < n - 1; i += 1) {
    let sum = turn[i];
    let d = 0;
    for (let l = i; l > 1; l -= 1) {
      if (d + seg[l - 1] > windowM) break;
      d += seg[l - 1];
      sum += turn[l - 1];
    }
    let dr = 0;
    for (let r = i; r < n - 2; r += 1) {
      if (dr + seg[r] > windowM) break;
      dr += seg[r];
      sum += turn[r + 1];
    }
    if (sum >= tightTurnDeg) {
      tight[i] = true;
      any = true;
    }
  }
  if (!any) return coords;

  // Extend each tight run by marginVerts so the relaxation blends into the curve.
  const mark = tight.slice();
  for (let i = 0; i < n; i += 1) {
    if (!tight[i]) continue;
    for (let k = -marginVerts; k <= marginVerts; k += 1) {
      const j = i + k;
      if (j > 0 && j < n - 1) mark[j] = true;
    }
  }
  mark[0] = false;
  mark[n - 1] = false;

  // Laplacian relaxation on marked vertices only; unmarked vertices stay pinned,
  // which anchors each tight run to the surrounding (untouched) geometry.
  let pts = coords.map((p) => p.slice() as Position);
  for (let it = 0; it < iterations; it += 1) {
    const next = pts.map((p) => p.slice() as Position);
    for (let i = 1; i < n - 1; i += 1) {
      if (!mark[i]) continue;
      const midX = 0.5 * (pts[i - 1][0] + pts[i + 1][0]);
      const midY = 0.5 * (pts[i - 1][1] + pts[i + 1][1]);
      next[i][0] = (1 - lambda) * pts[i][0] + lambda * midX;
      next[i][1] = (1 - lambda) * pts[i][1] + lambda * midY;
    }
    pts = next;
  }
  return pts;
}
