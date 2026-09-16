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

type SimplifyTightCurvesOptions = {
  tightTurnDeg?: number;
  windowM?: number;
  iterations?: number;
  lambda?: number;
  marginVerts?: number;
};

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

function clonePoint(point: Position): Position {
  return [point[0], point[1]];
}

function segLengths(coords: Position[]): number[] {
  const seg: number[] = [];
  for (let i = 0; i < coords.length - 1; i += 1) seg.push(haversineM(coords[i], coords[i + 1]));
  return seg;
}

function absTurns(coords: Position[]): number[] {
  const turn = Array.from({ length: coords.length }, () => 0);
  for (let i = 1; i < coords.length - 1; i += 1) {
    turn[i] = Math.abs(turnAt(coords[i - 1], coords[i], coords[i + 1]));
  }
  return turn;
}

function windowedAbsTurn(turn: number[], seg: number[], index: number, windowM: number): number {
  let sum = turn[index];
  let d = 0;
  for (let l = index; l > 1; l -= 1) {
    if (d + seg[l - 1] > windowM) break;
    d += seg[l - 1];
    sum += turn[l - 1];
  }
  let dr = 0;
  for (let r = index; r < turn.length - 2; r += 1) {
    if (dr + seg[r] > windowM) break;
    dr += seg[r];
    sum += turn[r + 1];
  }
  return sum;
}

function markTightVertices(turn: number[], seg: number[], windowM: number, tightTurnDeg: number): boolean[] {
  const tight = Array.from({ length: turn.length }, () => false);
  for (let i = 1; i < turn.length - 1; i += 1) {
    if (windowedAbsTurn(turn, seg, i, windowM) >= tightTurnDeg) tight[i] = true;
  }
  return tight;
}

function extendTightRuns(tight: boolean[], marginVerts: number): boolean[] {
  const mark = tight.slice();
  for (let i = 0; i < tight.length; i += 1) {
    if (!tight[i]) continue;
    for (let k = -marginVerts; k <= marginVerts; k += 1) {
      const j = i + k;
      if (j > 0 && j < tight.length - 1) mark[j] = true;
    }
  }
  mark[0] = false;
  mark[tight.length - 1] = false;
  return mark;
}

function relaxMarkedVertices(coords: Position[], mark: boolean[], iterations: number, lambda: number): Position[] {
  let pts = coords.map(clonePoint);
  for (let it = 0; it < iterations; it += 1) {
    const next = pts.map(clonePoint);
    for (let i = 1; i < coords.length - 1; i += 1) {
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

// Highest "turn density" (sum of |turn| in degrees per meter of arc) found in any
// window of +/- windowM around an interior vertex. A sharp hairpin scores high;
// a straight or gently curving line scores near zero.
export function maxTurnDensityDegPerM(coords: Position[], windowM = 40): number {
  if (!Array.isArray(coords) || coords.length < 3) return 0;
  const seg = segLengths(coords);
  const turn = absTurns(coords);
  let best = 0;
  for (let i = 1; i < coords.length - 1; i += 1) {
    const sum = windowedAbsTurn(turn, seg, i, windowM);
    let d = 0;
    for (let l = i; l > 1; l -= 1) {
      if (d + seg[l - 1] > windowM) break;
      d += seg[l - 1];
    }
    let dr = 0;
    for (let r = i; r < coords.length - 2; r += 1) {
      if (dr + seg[r] > windowM) break;
      dr += seg[r];
    }
    best = Math.max(best, sum / Math.max(d + dr, 1e-6));
  }
  return best;
}

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
  const resolved = ({
    tightTurnDeg: (options).tightTurnDeg ?? 70,
    windowM: (options).windowM ?? 50,
    iterations: (options).iterations ?? 16,
    lambda: (options).lambda ?? 0.5,
    marginVerts: (options).marginVerts ?? 1,
});
  if (!Array.isArray(coords) || coords.length < 5) return coords;

  const seg = segLengths(coords);
  const turn = absTurns(coords);
  const tight = markTightVertices(turn, seg, resolved.windowM, resolved.tightTurnDeg);
  if (!tight.some(Boolean)) return coords;
  const mark = extendTightRuns(tight, resolved.marginVerts);
  return relaxMarkedVertices(coords, mark, resolved.iterations, resolved.lambda);
}
