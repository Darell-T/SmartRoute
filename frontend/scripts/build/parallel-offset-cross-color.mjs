// Pure helper -- no fs, no globals.
//
// Final cross-color parallelization. The materialization offsets routes WITHIN a
// bundle, and the early cross-color pass skips already-baked continuous lanes --
// so two DIFFERENT-color routes that share a physical track but live in different
// bundles (e.g. the green 5 rush pattern sitting on top of the red 2 on White
// Plains Rd) end up coincident and cross instead of running as a parallel pair.
//
// This pass runs on the FINAL features: wherever a feature overlaps a LOWER-color-
// rank different-color feature over a sustained run (i.e. they are currently
// coincident/crossing), it shifts ONLY the higher-rank feature aside by one lane
// width over that run (tapered), leaving the primary trunk in place. Pairs that are
// already separated by more than overlapDistM are untouched (idempotent), so it
// never disturbs lanes that are already parallel.

import { offsetPolylineOverExtent } from "./cross-color-spread.mjs";

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function cumulativeArcs(coords) {
  const arcs = [0];
  for (let i = 1; i < coords.length; i += 1) arcs.push(arcs[i - 1] + haversineM(coords[i - 1], coords[i]));
  return arcs;
}

function projectDist(coords, p) {
  let best = Infinity;
  const mPerLng = metersPerDegLng(p[1]);
  const px = p[0] * mPerLng;
  const py = p[1] * M_PER_DEG_LAT;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const a = coords[i];
    const b = coords[i + 1];
    const ax = a[0] * mPerLng, ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * mPerLng, by = b[1] * M_PER_DEG_LAT;
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    const proj = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const d = haversineM(proj, p);
    if (d < best) best = d;
  }
  return best;
}

function colorRank(color, colorOrder) {
  const i = colorOrder.indexOf(color);
  return i === -1 ? 999 : i;
}

/**
 * @param {Array} features
 * @param {object} [options]
 * @param {string[]} [options.colorOrder=[]]  canonical color order (lower index = stays put)
 * @param {number} [options.overlapDistM=8]   vertices closer than this to a lower-rank other-color line are "coincident"
 * @param {number} [options.minOverlapM=150]  only shift runs longer than this
 * @param {number} [options.laneWidthM=8]     shift distance
 * @param {number} [options.taperM=40]
 * @returns {{ features: Array, shiftedCount: number }}
 */
export function parallelOffsetCrossColor(features, options = {}) {
  const { colorOrder = [], overlapDistM = 8, minOverlapM = 150, laneWidthM = 8, taperM = 40 } = options;
  const lines = features.filter(
    (f) => f.geometry?.type === "LineString" && Array.isArray(f.geometry.coordinates) && f.geometry.coordinates.length >= 2,
  );

  const replaced = new Map();
  let shiftedCount = 0;

  for (const f of lines) {
    const fColor = f.properties?.color;
    if (!fColor) continue;
    const fRank = colorRank(fColor, colorOrder);
    // lower-rank, different-color targets (the "primary" lines f must move away from)
    const targets = lines.filter(
      (t) => t !== f && t.properties?.color && t.properties.color !== fColor && colorRank(t.properties.color, colorOrder) < fRank,
    );
    if (targets.length === 0) continue;

    const coords = f.geometry.coordinates;
    const arcs = cumulativeArcs(coords);
    // per-vertex: is this vertex coincident with any lower-rank other-color line?
    const covered = coords.map((p) => targets.some((t) => projectDist(t.geometry.coordinates, p) <= overlapDistM));

    // sustained covered runs -> offset those arc ranges
    let working = coords;
    let didShift = false;
    let i = 0;
    while (i < covered.length) {
      if (!covered[i]) { i += 1; continue; }
      let j = i;
      while (j + 1 < covered.length && covered[j + 1]) j += 1;
      const runLen = arcs[j] - arcs[i];
      if (runLen >= minOverlapM) {
        working = offsetPolylineOverExtent(working, arcs[i], arcs[j], laneWidthM, taperM);
        didShift = true;
      }
      i = j + 1;
    }

    if (didShift) {
      replaced.set(f, {
        ...f,
        geometry: { type: "LineString", coordinates: working },
        properties: { ...f.properties, cross_color_parallelized: true },
      });
      shiftedCount += 1;
    }
  }

  return { features: features.map((f) => replaced.get(f) ?? f), shiftedCount };
}
