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

const M_PER_DEG_LAT = 110574;
function mPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

function projAt(p, lat) { return [p[0] * mPerDegLng(lat), p[1] * M_PER_DEG_LAT]; }
function unprojAt(p, lat) { return [p[0] / mPerDegLng(lat), p[1] / M_PER_DEG_LAT]; }

/**
 * Tangent-matched cubic Hermite from `start` to `end`. Tangents are unit vectors
 * in meter space (startTangent points the way the curve LEAVES start; endTangent
 * points the way it ARRIVES at end).
 * @returns {Array<[number,number]>}
 */
export function hermiteBetween(start, end, startTangent, endTangent, options = {}) {
  const { handleFrac = 0.5, sampleM = 6 } = options;
  const lat0 = (start[1] + end[1]) / 2;
  const p0 = projAt(start, lat0);
  const p1 = projAt(end, lat0);
  const dist = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const h = dist * handleFrac;
  const m0 = [startTangent[0] * h, startTangent[1] * h];
  const m1 = [endTangent[0] * h, endTangent[1] * h];
  const steps = Math.max(8, Math.ceil(dist / sampleM));
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
 * @param {Array<[number,number]>} coords  source polyline (the shared spine)
 * @param {object} [options]
 * @param {number} [options.maxOffsetM=80] peak offset at the middle of the span
 * @param {"left"|"right"} [options.side="left"] which side to bow toward (left = +90deg of travel)
 * @param {number} [options.taperPow=1] >1 makes the bow flatter near the ends and rounder in the middle
 * @param {number} [options.peakAt] if set (0..1), skews the peak toward this arc fraction:
 *   a smooth (smoothstep) peel up to the peak, then a STRAIGHT linear descent to the
 *   merge. The linear descent makes the spine converge on the source at a constant angle
 *   -- a clean Y-join (as Apple draws it) instead of a symmetric rounded "leaf" bottom.
 * @param {[number,number]} [options.plateau] if set ([startFrac,endFrac], 0..1): teardrop
 *   profile -- smoothstep peel UP to startFrac, hold at max (run PARALLEL to the source)
 *   to endFrac, then a STRAIGHT linear descent to 0 at the end. Rounded top (no pointy
 *   apex) + parallel mid + straight Y-merge. Takes precedence over peakAt.
 * @param {number} [options.teardropK] if set (>1): teardrop profile sin(pi * t^k). A single
 *   smooth convex curve -- gentle tangential peel off the source, a rounded apex (no point),
 *   then a STEEP non-tangential rejoin (a Y, not a rounded tangential "bottom curve"). Larger
 *   k => steeper Y and lower/later apex. Takes precedence over plateau/peakAt.
 * @returns {Array<[number,number]>} bow polyline, same vertex count as coords
 */
export function offsetBow(coords, options = {}) {
  const { maxOffsetM = 80, side = "left", taperPow = 1, peakAt = null, plateau = null, teardropK = null } = options;
  if (!Array.isArray(coords) || coords.length < 2) return coords;

  // cumulative arc for the taper parameter
  const arcs = [0];
  for (let i = 1; i < coords.length; i += 1) arcs.push(arcs[i - 1] + haversineM(coords[i - 1], coords[i]));
  const total = arcs[arcs.length - 1] || 1;
  const sign = side === "right" ? -1 : 1;
  const smoothstep = (u) => { const c = Math.max(0, Math.min(1, u)); return c * c * (3 - 2 * c); };

  const out = [];
  for (let i = 0; i < coords.length; i += 1) {
    const t = arcs[i] / total; // 0..1
    let taper;
    if (teardropK != null) {
      // single smooth convex curve: tangential peel, rounded apex, steep Y-rejoin
      taper = Math.sin(Math.PI * Math.pow(t, teardropK));
    } else if (plateau != null) {
      // teardrop: rounded peel up -> flat parallel plateau -> straight linear descent
      const [ps, pe] = plateau;
      if (t <= ps) taper = smoothstep(t / Math.max(1e-6, ps));
      else if (t <= pe) taper = 1;
      else taper = 1 - (t - pe) / Math.max(1e-6, 1 - pe);
    } else if (peakAt != null) {
      // asymmetric Y-join: smoothstep peel up to the peak, then a STRAIGHT linear
      // descent to 0. Linear descent => constant convergence angle => the spine
      // meets the source as a Y (a straight line into a vertex), not a rounded curve.
      taper = t <= peakAt
        ? smoothstep(t / Math.max(1e-6, peakAt))
        : 1 - (t - peakAt) / Math.max(1e-6, 1 - peakAt);
    } else {
      taper = Math.sin(Math.PI * t) ** taperPow; // 0 at ends, 1 at middle
    }
    const offset = maxOffsetM * taper;

    // local tangent (in meters) from neighbouring vertices
    const a = coords[Math.max(0, i - 1)];
    const b = coords[Math.min(coords.length - 1, i + 1)];
    const k = mPerDegLng(coords[i][1]);
    let tx = (b[0] - a[0]) * k;
    let ty = (b[1] - a[1]) * M_PER_DEG_LAT;
    const tl = Math.hypot(tx, ty) || 1;
    tx /= tl; ty /= tl;
    // left normal of travel direction is (-ty, tx)
    const nx = -ty * sign;
    const ny = tx * sign;

    out.push([
      coords[i][0] + (nx * offset) / k,
      coords[i][1] + (ny * offset) / M_PER_DEG_LAT,
    ]);
  }
  return out;
}
