// Pure helper -- no fs, no globals.
//
// Re-route OpenData geometry that wandered off a route's GTFS revenue shape back
// onto that shape, following the real track. NYC OpenData draws each service as
// one stroke that sometimes swings far off the actual revenue path (e.g. the 5
// at 149 St / Mott Haven bulges ~120-300 m west toward Walton Av, well off the
// GTFS 5 shape) -- drawn faithfully that makes a lens / wide sweep that does not
// match a clean transit-map rendering. The GTFS revenue shape IS the route's
// true path, so each contiguous OFF-shape excursion (vertices farther than
// maxOffM from every supplied shape) is REPLACED with the GTFS shape's sub-path
// between where the line left and rejoined the shape. On-shape geometry (coarse
// sampling, real curves) is left untouched, and replacements follow the real
// curve (never a straight chord), so nothing jumps.

const M_PER_DEG_LAT = 110574;

function mPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

function cumulativeArcs(coords) {
  const arcs = [0];
  for (let i = 1; i < coords.length; i += 1) arcs.push(arcs[i - 1] + haversineM(coords[i - 1], coords[i]));
  return arcs;
}

// Nearest point on a polyline to p, with arc position. { point, distM, arcM }.
function nearestWithArc(coords, arcs, p) {
  const k = mPerDegLng(p[1]);
  const px = p[0] * k, py = p[1] * M_PER_DEG_LAT;
  let best = null;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const a = coords[i], b = coords[i + 1];
    const ax = a[0] * k, ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * k, by = b[1] * M_PER_DEG_LAT;
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    let t = ((px - ax) * dx + (py - ay) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const point = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const distM = Math.hypot(point[0] * k - px, point[1] * M_PER_DEG_LAT - py);
    if (!best || distM < best.distM) best = { point, distM, arcM: arcs[i] + (arcs[i + 1] - arcs[i]) * t };
  }
  return best;
}

function interpAtArc(coords, arcs, arcM) {
  const total = arcs[arcs.length - 1];
  if (arcM <= 0) return coords[0].slice();
  if (arcM >= total) return coords[coords.length - 1].slice();
  for (let i = 1; i < arcs.length; i += 1) {
    if (arcs[i] >= arcM) {
      const t = (arcM - arcs[i - 1]) / ((arcs[i] - arcs[i - 1]) || 1e-9);
      return [coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t, coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t];
    }
  }
  return coords[coords.length - 1].slice();
}

// The shape's own vertices strictly between arcA and arcB, plus the two endpoints,
// oriented from arcA to arcB (handles either direction).
function subPath(coords, arcs, arcA, arcB) {
  const lo = Math.min(arcA, arcB);
  const hi = Math.max(arcA, arcB);
  const out = [interpAtArc(coords, arcs, lo)];
  for (let i = 0; i < coords.length; i += 1) {
    if (arcs[i] > lo + 1e-6 && arcs[i] < hi - 1e-6) out.push(coords[i]);
  }
  out.push(interpAtArc(coords, arcs, hi));
  return arcA <= arcB ? out : out.reverse();
}

function nearestAcrossShapes(shapes, shapeArcs, p) {
  let best = null;
  for (let si = 0; si < shapes.length; si += 1) {
    if (!Array.isArray(shapes[si]) || shapes[si].length < 2) continue;
    const n = nearestWithArc(shapes[si], shapeArcs[si], p);
    if (n && (!best || n.distM < best.distM)) best = { ...n, shapeIdx: si };
  }
  return best;
}

export function maxOffShapeM(coords, shapes) {
  if (!Array.isArray(coords) || !shapes?.length) return 0;
  const shapeArcs = shapes.map(cumulativeArcs);
  let m = 0;
  for (const p of coords) {
    const n = nearestAcrossShapes(shapes, shapeArcs, p);
    if (n && n.distM > m) m = n.distM;
  }
  return m;
}

/**
 * @param {Array<[number,number]>} coords
 * @param {Array<Array<[number,number]>>} shapes  the route's GTFS revenue shape coord-arrays
 * @param {object} [options]
 * @param {number} [options.maxOffM=55] vertices farther than this from every shape start an excursion
 * @param {number} [options.dedupeEpsM=0.5]
 * @returns {Array<[number,number]>} new coords, or the SAME ref if nothing changed
 */
export function snapOffRevenueToShape(coords, shapes, options = {}) {
  const { maxOffM = 55, dedupeEpsM = 0.5 } = options;
  if (!Array.isArray(coords) || coords.length < 2 || !shapes?.length) return coords;

  const shapeArcs = shapes.map(cumulativeArcs);
  const info = coords.map((p) => nearestAcrossShapes(shapes, shapeArcs, p));
  const off = info.map((x) => !x || x.distM > maxOffM);
  if (!off.some(Boolean)) return coords;

  const out = [];
  let i = 0;
  let changed = false;
  while (i < coords.length) {
    if (!off[i]) { out.push(coords[i]); i += 1; continue; }
    let b = i;
    while (b + 1 < coords.length && off[b + 1]) b += 1;
    const entryIdx = i - 1;
    const exitIdx = b + 1;

    if (entryIdx >= 0 && exitIdx < coords.length) {
      // Interior excursion: replace with the shape sub-path between the on-shape
      // entry and exit. A route has many shape variants; pick the one that covers
      // BOTH flanks (within maxOffM) with the SHORTEST sub-path, so we follow the
      // local track and never a variant that detours.
      let chosen = null;
      for (let si = 0; si < shapes.length; si += 1) {
        if (!Array.isArray(shapes[si]) || shapes[si].length < 2) continue;
        const ep = nearestWithArc(shapes[si], shapeArcs[si], coords[entryIdx]);
        const xp = nearestWithArc(shapes[si], shapeArcs[si], coords[exitIdx]);
        if (ep.distM <= maxOffM && xp.distM <= maxOffM) {
          const span = Math.abs(xp.arcM - ep.arcM);
          if (!chosen || span < chosen.span) chosen = { si, ep, xp, span };
        }
      }
      if (chosen) {
        const sp = subPath(shapes[chosen.si], shapeArcs[chosen.si], chosen.ep.arcM, chosen.xp.arcM);
        for (let k = 1; k < sp.length; k += 1) out.push(sp[k]); // entry already in out
        out.push(coords[exitIdx]);
        changed = true;
        i = exitIdx + 1;
        continue;
      }
      // No single shape covers both flanks: connect entry straight to exit.
      out.push(coords[exitIdx]);
      changed = true;
      i = exitIdx + 1;
      continue;
    }
    // Dangling off-run at the very start or end: drop those vertices.
    changed = true;
    i = b + 1;
  }
  if (!changed || out.length < 2) return coords;

  const deduped = [out[0]];
  for (let j = 1; j < out.length; j += 1) {
    const prev = deduped[deduped.length - 1];
    const cur = out[j];
    const k = mPerDegLng(cur[1]);
    const d = Math.hypot((cur[0] - prev[0]) * k, (cur[1] - prev[1]) * M_PER_DEG_LAT);
    if (j === out.length - 1 || d > dedupeEpsM) deduped.push(cur);
  }
  return deduped.length >= 2 ? deduped : coords;
}
