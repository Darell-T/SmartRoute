import type { Position } from "./types.ts";

export const LANE_WIDTH_METERS = 18;
export const MITER_LENGTH_CAP_RATIO = 2; // fall back to bevel above this miter length
export const M_PER_DEG_LAT = 111_320;
export const RESAMPLE_INTERVAL_M = 25;
export const HAUSDORFF_MAX_M = 15;
// Legacy buildJunctionBridges (Fix 1) gap-bridge max distance; paired with the
// branch-transition promotion in the orchestrator.
export const JUNCTION_BRIDGE_MAX_M = 90;

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

export function bidirectionalHausdorff(
  samplesA: Array<{ x: number; y: number; tx: number; ty: number }>,
  samplesB: Array<{ x: number; y: number; tx: number; ty: number }>,
) {
  let maxA = 0;
  let withinA = 0;
  let distanceSumA = 0;
  let tanSumA = 0;
  let tanCountA = 0;
  for (const a of samplesA) {
    let best = Infinity;
    let bestB = null;
    for (const b of samplesB) {
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = dx * dx + dy * dy;
      if (d2 < best) { best = d2; bestB = b; }
    }
    const d = Math.sqrt(best);
    distanceSumA += d;
    if (d > maxA) maxA = d;
    if (d <= HAUSDORFF_MAX_M) withinA += 1;
    if (bestB) {
      const dot = Math.abs(a.tx * bestB.tx + a.ty * bestB.ty);
      const angleDeg = Math.acos(Math.min(1, Math.max(-1, dot))) * 180 / Math.PI;
      tanSumA += angleDeg;
      tanCountA += 1;
    }
  }
  let maxB = 0;
  let withinB = 0;
  let distanceSumB = 0;
  for (const b of samplesB) {
    let best = Infinity;
    for (const a of samplesA) {
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = dx * dx + dy * dy;
      if (d2 < best) best = d2;
    }
    const d = Math.sqrt(best);
    distanceSumB += d;
    if (d > maxB) maxB = d;
    if (d <= HAUSDORFF_MAX_M) withinB += 1;
  }
  const overlapA = samplesA.length > 0 ? withinA / samplesA.length : 0;
  const overlapB = samplesB.length > 0 ? withinB / samplesB.length : 0;
  return {
    hausdorff: Math.max(maxA, maxB),
    overlap: Math.min(overlapA, overlapB),
    overlapA,
    overlapB,
    avgDistanceA: samplesA.length > 0 ? distanceSumA / samplesA.length : Infinity,
    avgDistanceB: samplesB.length > 0 ? distanceSumB / samplesB.length : Infinity,
    avgTangentDeg: tanCountA > 0 ? tanSumA / tanCountA : 180,
  };
}

export function routeSetsIntersect(left: string[], right: string[]) {
  const rightSet = new Set(right);
  return left.some((routeId) => rightSet.has(routeId));
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

  // Pre-compute per-vertex meters-per-degree-longitude (varies with lat).
  const mPerLngAt = coords.map((c) => metersPerDegLng(c[1]));

  // Project to meters using each vertex's lat-corrected scale.
  const projected = coords.map((c, i) => [c[0] * mPerLngAt[i], c[1] * M_PER_DEG_LAT]);

  // Per-segment unit normal (right-hand perpendicular to segment direction).
  const segNormals = [];
  for (let i = 0; i < projected.length - 1; i += 1) {
    const dx = projected[i + 1][0] - projected[i][0];
    const dy = projected[i + 1][1] - projected[i][1];
    const len = Math.hypot(dx, dy);
    if (len === 0) {
      segNormals.push([0, 0]);
      continue;
    }
    // Right-hand normal: rotate +90 degrees clockwise (dx, dy) -> (dy, -dx)
    segNormals.push([dy / len, -dx / len]);
  }

  // Per-vertex normal (averaged miter join) with bevel fallback.
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
    const a = segNormals[i - 1];
    const b = segNormals[i];
    const sumX = a[0] + b[0];
    const sumY = a[1] + b[1];
    const sumLen = Math.hypot(sumX, sumY);
    if (sumLen < 1e-9) {
      vertexNormals.push(b);
      continue;
    }
    const nx = sumX / sumLen;
    const ny = sumY / sumLen;
    // Miter scale: the offset along the miter axis must be (offset / cos(half-angle)).
    // cos(half-angle) = dot(a, miter) which equals (a dot miter). Equivalently, the
    // miter length factor is 1 / (a dot n) where n is the average unit normal.
    const cosHalf = a[0] * nx + a[1] * ny;
    const miterLen = Math.abs(offsetMeters) / Math.max(0.05, Math.abs(cosHalf));
    if (miterLen > miterCap) {
      // Sharp corner - fall back to the segment that's about to start
      vertexNormals.push(b);
    } else {
      // Scale the unit normal so projection onto a yields offsetMeters
      const scale = 1 / cosHalf;
      vertexNormals.push([nx * scale, ny * scale]);
    }
  }

  // Apply offset in projected meter space; convert back to lng/lat.
  const out: Position[] = [];
  for (let i = 0; i < projected.length; i += 1) {
    const n = vertexNormals[i];
    const nx = n[0] * offsetMeters;
    const ny = n[1] * offsetMeters;
    const x = projected[i][0] + nx;
    const y = projected[i][1] + ny;
    out.push([x / mPerLngAt[i], y / M_PER_DEG_LAT]);
  }
  return out;
}
