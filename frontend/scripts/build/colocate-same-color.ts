// Pure helper -- no fs, no globals. Pulls long parallel same-color lanes
// onto one shared track for the Apple one-ribbon-per-color look.
//
// On Queens Blvd the F express track runs ~18m from the F+M local track for
// ~5km. Both are MTA orange; Apple draws ONE orange ribbon there, but two
// distinct geometries 18m apart read as a clear double strand from ~z13.5 up.
// (Pairs ~6m apart -- Lex 4+5/4+6, 6th Av -- already fuse in paint and are
// deliberately left alone via minGapM.)
//
// For each same-color lane pair, the overlay lane is pulled onto its
// sibling wherever they run parallel within maxGapM for at least
// minStretchM, easing in/out over blendM so the departure points stay
// smooth. The carrier lane never moves. Overlay selection: fewer routes
// moves; on a tie (at this build stage the QB local lane is still [M] --
// F joins it later), the LONGER feature moves, keeping the ribbon on the
// short local alignment that owns the intermediate stations.

import type { Feature, LineStringGeometry, Position } from "./types.ts";

type ColocateFeatureProperties = {
  visual_feature_type?: string;
  route_ids?: string[];
  color?: string;
  corridor_id?: string;
  same_color_colocated?: boolean;
};

type ColocateFeature = Feature<LineStringGeometry, ColocateFeatureProperties>;

type ColocateOptions = {
  minGapM?: number;
  maxGapM?: number;
  minStretchM?: number;
  blendM?: number;
};

type ColocateGates = {
  minGapM: number;
  maxGapM: number;
  minStretchM: number;
  blendM: number;
};

type ColocateLane = {
  feature: ColocateFeature;
  color: string;
  routeCount: number;
  lengthM: number;
};

type Projection = {
  d: number;
  point: Position;
};

type ColocateStretch = {
  routes: string;
  lengthM: number;
};

type ColocateResult = {
  count: number;
  stretches: ColocateStretch[];
};

const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function distM(a: Position, b: Position): number {
  const k = metersPerDegLng((a[1] + b[1]) / 2);
  return Math.hypot((a[0] - b[0]) * k, (a[1] - b[1]) * M_PER_DEG_LAT);
}

// Nearest point on polyline (projected per-segment in local meters).
function nearestOnPolyline(point: Position, coords: Position[]): Projection {
  const lat = point[1];
  const k = metersPerDegLng(lat);
  const px = point[0] * k;
  const py = point[1] * M_PER_DEG_LAT;
  let best: Projection | null = null;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const ax = coords[i][0] * k;
    const ay = coords[i][1] * M_PER_DEG_LAT;
    const bx = coords[i + 1][0] * k;
    const by = coords[i + 1][1] * M_PER_DEG_LAT;
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    const candidate: Position = [
      coords[i][0] + (coords[i + 1][0] - coords[i][0]) * t,
      coords[i][1] + (coords[i + 1][1] - coords[i][1]) * t,
    ];
    const d = distM(point, candidate);
    if (!best || d < best.d) best = { d, point: candidate };
  }
  return best ?? { d: Infinity, point };
}

function ease(t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  return clamped * clamped * (3 - 2 * clamped);
}

function median(values: number[]): number {
  if (!values.length) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function polylineLengthM(coords: Position[]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += distM(coords[i - 1], coords[i]);
  return total;
}

function colocateLanes(lanes: ColocateFeature[]): ColocateLane[] {
  return lanes
    .filter(
      (feature) =>
        feature.geometry?.type === "LineString" &&
        Array.isArray(feature.geometry.coordinates) &&
        feature.geometry.coordinates.length >= 2,
    )
    .map((feature) => ({
      feature,
      color: String(feature.properties?.color ?? "").toUpperCase(),
      routeCount: (feature.properties?.route_ids ?? []).length,
      lengthM: polylineLengthM(feature.geometry.coordinates),
    }));
}

function isOverlayLane(mover: ColocateLane, carrier: ColocateLane): boolean {
  if (!mover.color || mover.color !== carrier.color) return false;
  if (mover.routeCount < carrier.routeCount) return true;
  return mover.routeCount === carrier.routeCount && mover.lengthM > carrier.lengthM;
}

function collectGapRuns(projections: Projection[], maxGapM: number): Array<[number, number]> {
  let runStart: number | null = null;
  const runs: Array<[number, number]> = [];
  for (let index = 0; index <= projections.length; index += 1) {
    const inRun = index < projections.length && projections[index].d <= maxGapM;
    if (inRun && runStart === null) runStart = index;
    if (!inRun && runStart !== null) {
      runs.push([runStart, index - 1]);
      runStart = null;
    }
  }
  return runs;
}

function pullStretchOntoCarrier(
  mover: ColocateLane,
  from: number,
  to: number,
  projections: Projection[],
  arc: number[],
  gates: ColocateGates,
): ColocateStretch | null {
  const coords = mover.feature.geometry.coordinates;
  const stretchLengthM = arc[to] - arc[from];
  if (stretchLengthM < gates.minStretchM) return null;
  const gaps: number[] = [];
  for (let index = from; index <= to; index += 1) gaps.push(projections[index].d);
  if (median(gaps) < gates.minGapM) return null;

  const startBoundaryEases = from > 0;
  const endBoundaryEases = to < coords.length - 1;
  for (let index = from; index <= to; index += 1) {
    let weight = 1;
    if (startBoundaryEases) weight = Math.min(weight, ease((arc[index] - arc[from]) / gates.blendM));
    if (endBoundaryEases) weight = Math.min(weight, ease((arc[to] - arc[index]) / gates.blendM));
    if (weight <= 0) continue;
    const target = projections[index].point;
    coords[index] = [
      coords[index][0] + (target[0] - coords[index][0]) * weight,
      coords[index][1] + (target[1] - coords[index][1]) * weight,
    ];
  }
  mover.feature.properties = {
    ...mover.feature.properties,
    same_color_colocated: true,
  };
  return {
    routes: (mover.feature.properties.route_ids ?? []).join(","),
    lengthM: Number(stretchLengthM.toFixed(0)),
  };
}

function colocateMoverOntoCarrier(
  mover: ColocateLane,
  carrier: ColocateLane,
  gates: ColocateGates,
): ColocateStretch[] {
  const moverCoords = mover.feature.geometry.coordinates;
  const projections = moverCoords.map((vertex) => nearestOnPolyline(vertex, carrier.feature.geometry.coordinates));
  const arc: number[] = [0];
  for (let index = 1; index < moverCoords.length; index += 1) {
    arc.push(arc[index - 1] + distM(moverCoords[index - 1], moverCoords[index]));
  }
  const stretches: ColocateStretch[] = [];
  for (const [from, to] of collectGapRuns(projections, gates.maxGapM)) {
    const stretch = pullStretchOntoCarrier(mover, from, to, projections, arc, gates);
    if (stretch) stretches.push(stretch);
  }
  return stretches;
}

/**
 * Co-locate long parallel same-color stretches onto one track.
 *
 * Mutates mover geometry in place; flags movers with
 * `same_color_colocated: true`.
 */
export function colocateSameColorStretches(
  lanes: ColocateFeature[],
  options: ColocateOptions = {},
): ColocateResult {
  const gates: ColocateGates = {
    minGapM: options.minGapM ?? 10,
    maxGapM: options.maxGapM ?? 30,
    minStretchM: options.minStretchM ?? 500,
    blendM: options.blendM ?? 100,
  };
  const entries = colocateLanes(lanes);
  const stretches: ColocateStretch[] = [];
  for (let i = 0; i < entries.length; i += 1) {
    for (let j = 0; j < entries.length; j += 1) {
      if (i === j || !isOverlayLane(entries[i], entries[j])) continue;
      stretches.push(...colocateMoverOntoCarrier(entries[i], entries[j], gates));
    }
  }
  return { count: stretches.length, stretches };
}
