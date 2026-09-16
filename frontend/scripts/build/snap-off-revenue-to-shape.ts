// Pure helper -- no fs, no globals.
//
// Re-route OpenData geometry that wandered off a route's GTFS revenue polyline
// back onto that polyline, following the real track. NYC OpenData draws each
// service as one stroke that sometimes swings far off the actual revenue path
// (e.g. the 5 at 149 St / Mott Haven bulges ~120-300 m west toward Walton Av,
// well off the GTFS 5 polyline) -- drawn faithfully that makes a lens / wide
// sweep that does not match a clean transit-map rendering. The GTFS revenue
// polyline IS the route's true path, so each contiguous OFF-polyline excursion
// (vertices farther than maxOffM from every supplied polyline) is REPLACED with
// the GTFS polyline's sub-path between where the line left and rejoined. On-path
// geometry (coarse sampling, real curves) is left untouched, and replacements
// follow the real curve (never a straight chord), so nothing jumps.

import type { Position } from "./types.ts";

type NearestPoint = {
  point: Position;
  distM: number;
  arcM: number;
};

type NearestPolylineHit = NearestPoint & {
  polylineIdx: number;
};

type ChosenPolylineSpan = {
  si: number;
  ep: NearestPoint;
  xp: NearestPoint;
  span: number;
};

type SnapOffRevenueOptions = {
  maxOffM?: number;
  dedupeEpsM?: number;
};

type ReplacedOffRevenue = {
  out: Position[];
  changed: boolean;
};

const M_PER_DEG_LAT = 110574;

function mPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

function cumulativeArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let i = 1; i < coords.length; i += 1) arcs.push(arcs[i - 1] + haversineM(coords[i - 1], coords[i]));
  return arcs;
}

function nearestWithArc(coords: Position[], arcs: number[], p: Position): NearestPoint {
  const k = mPerDegLng(p[1]);
  const px = p[0] * k, py = p[1] * M_PER_DEG_LAT;
  let best: NearestPoint | null = null;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const a = coords[i], b = coords[i + 1];
    const ax = a[0] * k, ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * k, by = b[1] * M_PER_DEG_LAT;
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    let t = ((px - ax) * dx + (py - ay) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const point: Position = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const distM = Math.hypot(point[0] * k - px, point[1] * M_PER_DEG_LAT - py);
    if (!best || distM < best.distM) best = { point, distM, arcM: arcs[i] + (arcs[i + 1] - arcs[i]) * t };
  }
  return best ?? { point: coords[0], distM: Infinity, arcM: 0 };
}

function clonePosition(coord: Position): Position {
  return [coord[0], coord[1]];
}

function interpAtArc(coords: Position[], arcs: number[], arcM: number): Position {
  const total = arcs[arcs.length - 1];
  if (arcM <= 0) return clonePosition(coords[0]);
  if (arcM >= total) return clonePosition(coords[coords.length - 1]);
  for (let i = 1; i < arcs.length; i += 1) {
    if (arcs[i] >= arcM) {
      const t = (arcM - arcs[i - 1]) / ((arcs[i] - arcs[i - 1]) || 1e-9);
      return [coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t, coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t];
    }
  }
  return clonePosition(coords[coords.length - 1]);
}

function polylineSubpath(coords: Position[], arcs: number[], arcA: number, arcB: number): Position[] {
  const lo = Math.min(arcA, arcB);
  const hi = Math.max(arcA, arcB);
  const out = [interpAtArc(coords, arcs, lo)];
  for (let i = 0; i < coords.length; i += 1) {
    if (arcs[i] > lo + 1e-6 && arcs[i] < hi - 1e-6) out.push(coords[i]);
  }
  out.push(interpAtArc(coords, arcs, hi));
  return arcA <= arcB ? out : out.reverse();
}

function nearestAcrossPolylines(
  polylines: Position[][],
  polylineArcs: number[][],
  p: Position,
): NearestPolylineHit | null {
  let best: NearestPolylineHit | null = null;
  for (let si = 0; si < polylines.length; si += 1) {
    if (!Array.isArray(polylines[si]) || polylines[si].length < 2) continue;
    const n = nearestWithArc(polylines[si], polylineArcs[si], p);
    if (n && (!best || n.distM < best.distM)) best = { ...n, polylineIdx: si };
  }
  return best;
}

function chooseShortestCoveringSpan(
  polylines: Position[][],
  polylineArcs: number[][],
  entry: Position,
  exit: Position,
  maxOffM: number,
): ChosenPolylineSpan | null {
  let chosen: ChosenPolylineSpan | null = null;
  for (let si = 0; si < polylines.length; si += 1) {
    if (!Array.isArray(polylines[si]) || polylines[si].length < 2) continue;
    const ep = nearestWithArc(polylines[si], polylineArcs[si], entry);
    const xp = nearestWithArc(polylines[si], polylineArcs[si], exit);
    if (ep.distM > maxOffM || xp.distM > maxOffM) continue;
    const span = Math.abs(xp.arcM - ep.arcM);
    if (!chosen || span < chosen.span) chosen = { si, ep, xp, span };
  }
  return chosen;
}

function appendChosenSubpath(
  out: Position[],
  polylines: Position[][],
  polylineArcs: number[][],
  chosen: ChosenPolylineSpan,
): void {
  const segment = polylineSubpath(
    polylines[chosen.si],
    polylineArcs[chosen.si],
    chosen.ep.arcM,
    chosen.xp.arcM,
  );
  for (let k = 1; k < segment.length; k += 1) out.push(segment[k]);
}

function replaceOffRevenueRuns(
  coords: Position[],
  polylines: Position[][],
  polylineArcs: number[][],
  off: boolean[],
  maxOffM: number,
): ReplacedOffRevenue {
  const out: Position[] = [];
  let i = 0;
  let changed = false;
  while (i < coords.length) {
    if (!off[i]) {
      out.push(coords[i]);
      i += 1;
      continue;
    }
    let b = i;
    while (b + 1 < coords.length && off[b + 1]) b += 1;
    const entryIdx = i - 1;
    const exitIdx = b + 1;
    if (entryIdx < 0 || exitIdx >= coords.length) {
      changed = true;
      i = b + 1;
      continue;
    }
    const chosen = chooseShortestCoveringSpan(
      polylines,
      polylineArcs,
      coords[entryIdx],
      coords[exitIdx],
      maxOffM,
    );
    if (chosen) appendChosenSubpath(out, polylines, polylineArcs, chosen);
    out.push(coords[exitIdx]);
    changed = true;
    i = exitIdx + 1;
  }
  return { out, changed };
}

function dedupeNearbyVertices(coords: Position[], dedupeEpsM: number): Position[] {
  const deduped: Position[] = [coords[0]];
  for (let j = 1; j < coords.length; j += 1) {
    const prev = deduped[deduped.length - 1];
    const cur = coords[j];
    const k = mPerDegLng(cur[1]);
    const d = Math.hypot((cur[0] - prev[0]) * k, (cur[1] - prev[1]) * M_PER_DEG_LAT);
    if (j === coords.length - 1 || d > dedupeEpsM) deduped.push(cur);
  }
  return deduped;
}

function snapOffRevenueToPolyline(
  coords: Position[],
  polylines: Position[][],
  options: SnapOffRevenueOptions = {},
): Position[] {
  const maxOffM = options.maxOffM ?? 55;
  const dedupeEpsM = options.dedupeEpsM ?? 0.5;
  if (!Array.isArray(coords) || coords.length < 2 || !polylines?.length) return coords;

  const polylineArcs = polylines.map(cumulativeArcs);
  const off = coords.map((p) => {
    const nearest = nearestAcrossPolylines(polylines, polylineArcs, p);
    return !nearest || nearest.distM > maxOffM;
  });
  if (!off.some(Boolean)) return coords;

  const { out, changed } = replaceOffRevenueRuns(coords, polylines, polylineArcs, off, maxOffM);
  if (!changed || out.length < 2) return coords;
  const deduped = dedupeNearbyVertices(out, dedupeEpsM);
  return deduped.length >= 2 ? deduped : coords;
}

export { snapOffRevenueToPolyline };
