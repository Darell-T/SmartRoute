// Pure helper -- no fs, no globals.
//
// Replace tight hairpin sections (where a polyline reverses direction over a
// short distance) with a tangent-matched Hermite arc. This is the "Apple Maps
// schematic simplification" for junctions like the 5 at 149 St / Mott Haven,
// where the real GTFS track does a ~270-degree tight curl that reads as a kinked
// loop on a transit map, but Apple draws a clean wide arc. The detection is
// general: scan backwards from the endpoint for a section where the heading
// swings more than `minReversalDeg` within `maxArcM` meters. Replace that
// section with a Hermite spline tangent-matched at both ends to the surviving
// geometry. Endpoints of the polyline that are NOT in the hairpin are preserved.

const M_PER_DEG_LAT = 110574;
function mPerDegLng(lat) { return 111320 * Math.cos((lat * Math.PI) / 180); }

function haversineM([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

function bearingDeg(a, b) {
  const k = mPerDegLng((a[1] + b[1]) / 2);
  return (Math.atan2((b[1] - a[1]) * M_PER_DEG_LAT, (b[0] - a[0]) * k) * 180) / Math.PI;
}

function signedTurnDeg(p, c, n) {
  let t = bearingDeg(c, n) - bearingDeg(p, c);
  while (t > 180) t -= 360;
  while (t < -180) t += 360;
  return t;
}

function projAt(p, lat) { return [p[0] * mPerDegLng(lat), p[1] * M_PER_DEG_LAT]; }
function unprojAt(p, lat) { return [p[0] / mPerDegLng(lat), p[1] / M_PER_DEG_LAT]; }

function unitVec(from, to) {
  const k = mPerDegLng((from[1] + to[1]) / 2);
  const dx = (to[0] - from[0]) * k, dy = (to[1] - from[1]) * M_PER_DEG_LAT;
  const l = Math.hypot(dx, dy);
  return l < 1e-9 ? [0, 0] : [dx / l, dy / l];
}

function hermiteCurve(start, end, startUnit, endUnit, handleFrac, sampleM) {
  const lat0 = (start[1] + end[1]) / 2;
  const p0 = projAt(start, lat0), p1 = projAt(end, lat0);
  const dist = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const h = dist * handleFrac;
  const m0 = [startUnit[0] * h, startUnit[1] * h];
  const m1 = [endUnit[0] * h, endUnit[1] * h];
  const steps = Math.max(12, Math.ceil(dist / sampleM));
  const out = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps, t2 = t * t, t3 = t2 * t;
    out.push(unprojAt([
      (2 * t3 - 3 * t2 + 1) * p0[0] + (t3 - 2 * t2 + t) * m0[0] + (-2 * t3 + 3 * t2) * p1[0] + (t3 - t2) * m1[0],
      (2 * t3 - 3 * t2 + 1) * p0[1] + (t3 - 2 * t2 + t) * m0[1] + (-2 * t3 + 3 * t2) * p1[1] + (t3 - t2) * m1[1],
    ], lat0));
  }
  return out;
}

/**
 * Replace tight hairpin sections near polyline endpoints with a tangent-matched
 * Hermite arc. Returns the SAME array ref if no hairpin found.
 *
 * @param {Array<[number,number]>} coords
 * @param {Array<[number,number]>} targetPoint  the sibling feature endpoint to land on (if provided)
 * @param {Array<number>} targetTangentUnit  unit tangent pointing back TOWARD the branch at targetPoint
 * @param {object} [options]
 * @param {number} [options.minReversalDeg=120] cumulative |turn| that marks a hairpin
 * @param {number} [options.maxArcM=300] only scan this far from the endpoint
 * @param {number} [options.handleFrac=0.55] Hermite handle as fraction of chord distance
 * @param {number} [options.sampleM=5] curve sampling interval
 * @param {number} [options.tangentSampleN=5] how many vertices to average for tangent
 * @returns {Array<[number,number]>}
 */
export function replaceEndpointHairpin(coords, targetPoint, targetTangentUnit, options = {}) {
  const {
    minReversalDeg = 120,
    maxArcM = 300,
    handleFrac = 0.55,
    sampleM = 5,
    tangentSampleN = 5,
  } = options;
  if (!Array.isArray(coords) || coords.length < 10) return coords;

  const n = coords.length;
  // Scan backwards from the end
  let cumTurn = 0;
  let cutIdx = -1;
  for (let i = n - 2; i > Math.max(0, n - 200); i -= 1) {
    cumTurn += signedTurnDeg(coords[i - 1], coords[i], coords[i + 1]);
    const arc = haversineM(coords[i], coords[n - 1]);
    if (arc > maxArcM) break;
    if (Math.abs(cumTurn) >= minReversalDeg) { cutIdx = i; break; }
  }
  if (cutIdx < 0) return coords;

  // Tangent at cut point (branch outbound heading)
  const tSample = Math.min(tangentSampleN, cutIdx);
  const branchTangent = unitVec(coords[cutIdx - tSample], coords[cutIdx]);

  // Target: if provided, use it; otherwise use the polyline's own endpoint
  const target = targetPoint || coords[n - 1];
  const targetTangent = targetTangentUnit || unitVec(coords[n - 1], coords[n - 2]);

  const curve = hermiteCurve(coords[cutIdx], target, branchTangent, targetTangent, handleFrac, sampleM);

  return [...coords.slice(0, cutIdx + 1), ...curve.slice(1)];
}
