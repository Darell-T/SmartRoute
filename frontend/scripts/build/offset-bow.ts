// Pure helper -- no fs, no globals.
//
// Build a "bow": a tapered perpendicular offset of a polyline that coincides with
// the source at both ends and bows out to a maximum offset in the middle. Used to
// author the cartographic lens at junctions where two services share track but
// are drawn as parallel lines that separate around a station and rejoin (e.g. the
// 4 on Grand Concourse + the 5 bowing west via Walton Av at 149 St / Mott Haven,
// exactly as Apple Maps and the Transit app draw it). The offset follows the
// source's local tangent so the bow tracks the source curve, and the taper is a
// smooth sine bump so the ends meet the source with matching tangents (no kink).

import type { Position } from "./types.ts";

const M_PER_DEG_LAT = 110574;
function mPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

function projAt(p: Position, lat: number): Position { return [p[0] * mPerDegLng(lat), p[1] * M_PER_DEG_LAT]; }
function unprojAt(p: Position, lat: number): Position { return [p[0] / mPerDegLng(lat), p[1] / M_PER_DEG_LAT]; }

/**
 * Tangent-matched cubic Hermite from `start` to `end`. Tangents are unit vectors
 * in meter space (startTangent points the way the curve LEAVES start; endTangent
 * points the way it ARRIVES at end).
 * @returns {Array<[number,number]>}
 */
export function hermiteBetween(
  start: Position,
  end: Position,
  startTangent: Position,
  endTangent: Position,
  options: { handleFrac?: number; sampleM?: number } = {},
): Position[] {
  const { handleFrac = 0.5, sampleM = 6 } = options;
  const lat0 = (start[1] + end[1]) / 2;
  const p0 = projAt(start, lat0);
  const p1 = projAt(end, lat0);
  const dist = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const h = dist * handleFrac;
  const m0 = [startTangent[0] * h, startTangent[1] * h];
  const m1 = [endTangent[0] * h, endTangent[1] * h];
  const steps = Math.max(8, Math.ceil(dist / sampleM));
  const out: Position[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps, t2 = t * t, t3 = t2 * t;
    out.push(unprojAt([
      (2 * t3 - 3 * t2 + 1) * p0[0] + (t3 - 2 * t2 + t) * m0[0] + (-2 * t3 + 3 * t2) * p1[0] + (t3 - t2) * m1[0],
      (2 * t3 - 3 * t2 + 1) * p0[1] + (t3 - 2 * t2 + t) * m0[1] + (-2 * t3 + 3 * t2) * p1[1] + (t3 - t2) * m1[1],
    ], lat0));
  }
  return out;
}

type BowProfile = {
  maxOffsetM: number;
  side: "left" | "right";
  taperPow: number;
  peakAt: number | null;
  plateau: Position | null;
  teardropK: number | null;
};

function smoothstep(u: number): number {
  const c = Math.max(0, Math.min(1, u));
  return c * c * (3 - 2 * c);
}

function bowTaper(t: number, profile: BowProfile): number {
  if (profile.teardropK != null) {
    return Math.sin(Math.PI * Math.pow(t, profile.teardropK));
  }
  if (profile.plateau != null) {
    const [ps, pe] = profile.plateau;
    if (t <= ps) return smoothstep(t / Math.max(1e-6, ps));
    if (t <= pe) return 1;
    return 1 - (t - pe) / Math.max(1e-6, 1 - pe);
  }
  if (profile.peakAt != null) {
    if (t <= profile.peakAt) return smoothstep(t / Math.max(1e-6, profile.peakAt));
    return 1 - (t - profile.peakAt) / Math.max(1e-6, 1 - profile.peakAt);
  }
  return Math.sin(Math.PI * t) ** profile.taperPow;
}

function offsetVertexAlongNormal(
  coord: Position,
  prev: Position,
  next: Position,
  offsetM: number,
  sign: number,
): Position {
  const k = mPerDegLng(coord[1]);
  let tx = (next[0] - prev[0]) * k;
  let ty = (next[1] - prev[1]) * M_PER_DEG_LAT;
  const tl = Math.hypot(tx, ty) || 1;
  tx /= tl;
  ty /= tl;
  const nx = -ty * sign;
  const ny = tx * sign;
  return [
    coord[0] + (nx * offsetM) / k,
    coord[1] + (ny * offsetM) / M_PER_DEG_LAT,
  ];
}

type BowOptions = {
  maxOffsetM?: number;
  side?: "left" | "right";
  taperPow?: number;
  peakAt?: number | null;
  plateau?: Position | null;
  teardropK?: number | null;
};

function cumulativeBowArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let i = 1; i < coords.length; i += 1) {
    arcs.push(arcs[i - 1] + haversineM(coords[i - 1], coords[i]));
  }
  return arcs;
}

/** Offset a polyline with a tapered bow that rejoins its source at both ends. */
export function offsetBow(coords: Position[], options: BowOptions = {}): Position[] {
  if (!Array.isArray(coords) || coords.length < 2) return coords;
  const profile: BowProfile = {
    maxOffsetM: (options).maxOffsetM ?? 80,
    side: (options).side ?? "left",
    taperPow: (options).taperPow ?? 1,
    peakAt: (options).peakAt ?? null,
    plateau: (options).plateau ?? null,
    teardropK: (options).teardropK ?? null,
  };
  const arcs = cumulativeBowArcs(coords);
  const total = arcs[arcs.length - 1] || 1;
  const sign = profile.side === "right" ? -1 : 1;
  return coords.map((coord, index) =>
    offsetVertexAlongNormal(
      coord,
      coords[Math.max(0, index - 1)],
      coords[Math.min(coords.length - 1, index + 1)],
      profile.maxOffsetM * bowTaper(arcs[index] / total, profile),
      sign,
    ),
  );
}
