import type { Position } from "./types.ts";

export const LANE_WIDTH_METERS = 18;
export const MITER_LENGTH_CAP_RATIO = 2; // fall back to bevel above this miter length
export const M_PER_DEG_LAT = 111_320;
export const RESAMPLE_INTERVAL_M = 25;
export const HAUSDORFF_MAX_M = 15;

export function metersPerDegLng(lat: number) {
  return 111_320 * Math.cos((lat * Math.PI) / 180);
}

export function distanceMeters(a: Position, b: Position) {
  const midLat = (a[1] + b[1]) / 2;
  const mPerLng = metersPerDegLng(midLat);
  const dx = (a[0] - b[0]) * mPerLng;
  const dy = (a[1] - b[1]) * M_PER_DEG_LAT;
  return Math.hypot(dx, dy);
}

export function lineLengthMeters(coords: Position[]) {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) {
    total += distanceMeters(coords[i - 1], coords[i]);
  }
  return total;
}

export function vectorMeters(from: Position, to: Position): Position {
  const midLat = (from[1] + to[1]) / 2;
  const mPerLng = metersPerDegLng(midLat);
  return [
    (to[0] - from[0]) * mPerLng,
    (to[1] - from[1]) * M_PER_DEG_LAT,
  ];
}

export function angleDeltaDegrees(a: Position, b: Position) {
  const dot = a[0] * b[0] + a[1] * b[1];
  const aLen = Math.hypot(a[0], a[1]);
  const bLen = Math.hypot(b[0], b[1]);
  if (aLen === 0 || bLen === 0) return 0;
  return Math.acos(Math.max(-1, Math.min(1, dot / (aLen * bLen)))) * 180 / Math.PI;
}

export function geometryStats(coords: Position[]) {
  let lengthM = 0;
  let maxSegmentLengthM = 0;
  let sharpAngleCount = 0;
  let maxBearingChangeDegrees = 0;

  for (let i = 1; i < coords.length; i += 1) {
    const segmentLength = distanceMeters(coords[i - 1], coords[i]);
    lengthM += segmentLength;
    maxSegmentLengthM = Math.max(maxSegmentLengthM, segmentLength);
  }

  for (let i = 2; i < coords.length; i += 1) {
    const incoming = vectorMeters(coords[i - 2], coords[i - 1]);
    const outgoing = vectorMeters(coords[i - 1], coords[i]);
    const delta = angleDeltaDegrees(incoming, outgoing);
    maxBearingChangeDegrees = Math.max(maxBearingChangeDegrees, delta);
    if (delta > 120) sharpAngleCount += 1;
  }

  const directDistanceM =
    coords.length >= 2 ? distanceMeters(coords[0], coords[coords.length - 1]) : 0;
  const sinuosity = directDistanceM > 1 ? lengthM / directDistanceM : 1;

  return {
    length_m: Number(lengthM.toFixed(2)),
    direct_distance_m: Number(directDistanceM.toFixed(2)),
    sinuosity: Number(sinuosity.toFixed(4)),
    max_segment_length_m: Number(maxSegmentLengthM.toFixed(2)),
    coordinate_count: coords.length,
    sharp_angle_count: sharpAngleCount,
    max_bearing_change_degrees: Number(maxBearingChangeDegrees.toFixed(2)),
  };
}

const REF_LAT = 40.73;
const M_PER_DEG_LNG = metersPerDegLng(REF_LAT);

export function toMeters(coord: Position): Position {
  return [coord[0] * M_PER_DEG_LNG, coord[1] * M_PER_DEG_LAT];
}

export function resampleEdgeAt5m(coordsLngLat: Position[]) {
  const coordsM = coordsLngLat.map(toMeters);
  const arc = [0];
  for (let i = 1; i < coordsM.length; i += 1) {
    const dx = coordsM[i][0] - coordsM[i - 1][0];
    const dy = coordsM[i][1] - coordsM[i - 1][1];
    arc.push(arc[i - 1] + Math.hypot(dx, dy));
  }
  const total = arc[arc.length - 1];
  if (total < RESAMPLE_INTERVAL_M * 2) {
    return [
      { x: coordsM[0][0], y: coordsM[0][1], t: 0 },
      { x: coordsM[coordsM.length - 1][0], y: coordsM[coordsM.length - 1][1], t: total },
    ].map((p, i, arr) => {
      const next = arr[Math.min(i + 1, arr.length - 1)];
      const prev = arr[Math.max(i - 1, 0)];
      const dx = next.x - prev.x;
      const dy = next.y - prev.y;
      const len = Math.hypot(dx, dy) || 1;
      return { ...p, tx: dx / len, ty: dy / len };
    });
  }
  const samples = [];
  let segIdx = 0;
  for (let s = 0; s <= total; s += RESAMPLE_INTERVAL_M) {
    while (segIdx < arc.length - 2 && arc[segIdx + 1] < s) segIdx += 1;
    const segStart = arc[segIdx];
    const segEnd = arc[segIdx + 1];
    const segLen = segEnd - segStart;
    const t = segLen > 0 ? (s - segStart) / segLen : 0;
    const x = coordsM[segIdx][0] + t * (coordsM[segIdx + 1][0] - coordsM[segIdx][0]);
    const y = coordsM[segIdx][1] + t * (coordsM[segIdx + 1][1] - coordsM[segIdx][1]);
    const dx = coordsM[segIdx + 1][0] - coordsM[segIdx][0];
    const dy = coordsM[segIdx + 1][1] - coordsM[segIdx][1];
    const len = Math.hypot(dx, dy) || 1;
    samples.push({ x, y, t: s, tx: dx / len, ty: dy / len });
  }
  return samples;
}

type TangentSample = {
  x: number;
  y: number;
  tx: number;
  ty: number;
};

function nearestSample(target: TangentSample, others: TangentSample[]): TangentSample | null {
  let best = Infinity;
  let bestB: TangentSample | null = null;
  for (const other of others) {
    const dx = target.x - other.x;
    const dy = target.y - other.y;
    const d2 = dx * dx + dy * dy;
    if (d2 < best) {
      best = d2;
      bestB = other;
    }
  }
  return bestB;
}

function directedDistanceStats(samples: TangentSample[], others: TangentSample[]) {
  let max = 0;
  let within = 0;
  let distanceSum = 0;
  for (const sample of samples) {
    const best = nearestSample(sample, others);
    const d = best ? Math.hypot(sample.x - best.x, sample.y - best.y) : Infinity;
    distanceSum += d;
    if (d > max) max = d;
    if (d <= HAUSDORFF_MAX_M) within += 1;
  }
  return {
    max,
    overlap: samples.length > 0 ? within / samples.length : 0,
    avgDistance: samples.length > 0 ? distanceSum / samples.length : Infinity,
  };
}

function meanAbsTangentDeg(samples: TangentSample[], others: TangentSample[]): number {
  let tanSum = 0;
  let tanCount = 0;
  for (const sample of samples) {
    const best = nearestSample(sample, others);
    if (!best) continue;
    const dot = Math.abs(sample.tx * best.tx + sample.ty * best.ty);
    tanSum += Math.acos(Math.min(1, Math.max(-1, dot))) * 180 / Math.PI;
    tanCount += 1;
  }
  return tanCount > 0 ? tanSum / tanCount : 180;
}

export function bidirectionalHausdorff(
  samplesA: TangentSample[],
  samplesB: TangentSample[],
) {
  const a = directedDistanceStats(samplesA, samplesB);
  const b = directedDistanceStats(samplesB, samplesA);
  return {
    hausdorff: Math.max(a.max, b.max),
    overlap: Math.min(a.overlap, b.overlap),
    overlapA: a.overlap,
    overlapB: b.overlap,
    avgDistanceA: a.avgDistance,
    avgDistanceB: b.avgDistance,
    avgTangentDeg: meanAbsTangentDeg(samplesA, samplesB),
  };
}

export function routeSetsIntersect(left: string[], right: string[]) {
  const rightSet = new Set(right);
  return left.some((routeId) => rightSet.has(routeId));
}

function segmentRightNormals(projected: number[][]): number[][] {
  const segNormals = [];
  for (let i = 0; i < projected.length - 1; i += 1) {
    const dx = projected[i + 1][0] - projected[i][0];
    const dy = projected[i + 1][1] - projected[i][1];
    const len = Math.hypot(dx, dy);
    if (len === 0) {
      segNormals.push([0, 0]);
      continue;
    }
    segNormals.push([dy / len, -dx / len]);
  }
  return segNormals;
}

function miterVertexNormal(
  prev: number[],
  next: number[],
  offsetMeters: number,
  miterCap: number,
): number[] {
  const sumX = prev[0] + next[0];
  const sumY = prev[1] + next[1];
  const sumLen = Math.hypot(sumX, sumY);
  if (sumLen < 1e-9) return next;
  const nx = sumX / sumLen;
  const ny = sumY / sumLen;
  const cosHalf = prev[0] * nx + prev[1] * ny;
  const miterLen = Math.abs(offsetMeters) / Math.max(0.05, Math.abs(cosHalf));
  if (miterLen > miterCap) return next;
  const scale = 1 / cosHalf;
  return [nx * scale, ny * scale];
}

function vertexMiterNormals(
  projected: number[][],
  segNormals: number[][],
  offsetMeters: number,
  miterCap: number,
): number[][] {
  const vertexNormals = [];
  for (let i = 0; i < projected.length; i += 1) {
    if (i === 0) {
      vertexNormals.push(segNormals[0]);
      continue;
    }
    if (i === projected.length - 1) {
      vertexNormals.push(segNormals[segNormals.length - 1]);
      continue;
    }
    vertexNormals.push(miterVertexNormal(segNormals[i - 1], segNormals[i], offsetMeters, miterCap));
  }
  return vertexNormals;
}

// Compute pre-baked offset geometry. Walks the polyline vertex by vertex,
// computes the average of adjacent segment normals (miter join), caps to
// MITER_LENGTH_CAP_RATIO x lane width to avoid spikes at sharp corners
// (falls back to the segment normal - bevel). All math is in projected
// meters; final result is converted back to [lng, lat] using local
// per-vertex meters-per-degree.
export function offsetPolylineByLaneSlot(coords: Position[], laneSlot: number) {
  if (!Array.isArray(coords) || coords.length < 2) return coords;
  if (!Number.isFinite(laneSlot) || laneSlot === 0) return coords;
  const offsetMeters = laneSlot * LANE_WIDTH_METERS;
  const miterCap = LANE_WIDTH_METERS * MITER_LENGTH_CAP_RATIO;
  const mPerLngAt = coords.map((c) => metersPerDegLng(c[1]));
  const projected = coords.map((c, i) => [c[0] * mPerLngAt[i], c[1] * M_PER_DEG_LAT]);
  const segNormals = segmentRightNormals(projected);
  const vertexNormals = vertexMiterNormals(projected, segNormals, offsetMeters, miterCap);
  const out: Position[] = [];
  for (let i = 0; i < projected.length; i += 1) {
    const n = vertexNormals[i];
    out.push([
      (projected[i][0] + n[0] * offsetMeters) / mPerLngAt[i],
      (projected[i][1] + n[1] * offsetMeters) / M_PER_DEG_LAT,
    ]);
  }
  return out;
}
