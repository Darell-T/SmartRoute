// Pure helper -- no fs, no globals.
//
// Final cross-color parallelization. The materialization offsets routes WITHIN a
// bundle, and the early cross-color pass skips already-baked continuous lanes --
// so two DIFFERENT-color routes that share a physical track but live in different
// bundles (e.g. the green 5 rush pattern sitting on top of the red 2 on White
// Plains Rd) end up coincident and cross instead of running as a parallel pair.
//
// This pass runs on the FINAL features: wherever a feature overlaps a LOWER-color-
// rank different-color feature over a sustained run and is either coincident with
// it or crosses from one side of it to the other, it shifts ONLY the higher-rank
// feature aside by one lane width over that run (tapered), leaving the primary
// trunk in place. Pairs that are already sustained parallel lanes on one side are
// untouched even when they are closer than overlapDistM.

import { offsetPolylineOverExtent } from "./cross-color-spread.ts";
import type { LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;

type GeometryLike = {
  type?: string;
  coordinates?: Position[] | Position;
};

type CrossColorProperties = {
  color?: string;
  cross_color_parallelized?: boolean;
  corridor_id?: string;
  marker_type?: string;
  route_ids?: string[];
  custom_stage_marker?: string;
};

export type CrossColorFeature = {
  type?: string;
  id?: string | number;
  geometry?: GeometryLike | null;
  properties?: CrossColorProperties | null;
};

type CrossColorLineFeature = CrossColorFeature & {
  geometry: LineStringGeometry;
  properties: CrossColorProperties;
};

type Projection = {
  distM: number;
  signedSideM: number;
};

type ArcRange = {
  startArc: number;
  endArc: number;
};

export type ParallelOffsetCrossColorOptions = {
  colorOrder?: string[];
  overlapDistM?: number;
  sideEpsM?: number;
  minOverlapM?: number;
  laneWidthM?: number;
  taperM?: number;
};

export type ParallelOffsetCrossColorResult = {
  features: CrossColorFeature[];
  shiftedCount: number;
};

function metersPerDegLng(lat: number): number {
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

function cumulativeArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let i = 1; i < coords.length; i += 1) arcs.push(arcs[i - 1] + haversineM(coords[i - 1], coords[i]));
  return arcs;
}

function isFiniteNumber(value: number): boolean {
  return value === Number(value) && Number.isFinite(value);
}

function isPosition(value: Position | number[]): value is Position {
  return value.length >= 2 && isFiniteNumber(value[0]) && isFiniteNumber(value[1]);
}

function isLineFeature(feature: CrossColorFeature): feature is CrossColorLineFeature {
  const geometry = feature.geometry;
  const properties = feature.properties;
  const coordinates = geometry?.coordinates;
  if (geometry?.type !== "LineString" || !Array.isArray(coordinates) || coordinates.length < 2) {
    return false;
  }
  const first = coordinates[0];
  if (!Array.isArray(first) || !isPosition(first)) return false;
  for (const row of coordinates) {
    if (!Array.isArray(row) || !isPosition(row)) return false;
  }
  return properties != null && !Array.isArray(properties);
}

function projectToPolyline(coords: Position[], p: Position): Projection | null {
  let best: Projection | null = null;
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
    const len = Math.sqrt(len2);
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    const proj: Position = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const distM = haversineM(proj, p);
    if (!best || distM < best.distM) {
      best = {
        distM,
        signedSideM: (dx * (py - ay) - dy * (px - ax)) / len,
      };
    }
  }
  return best;
}

function colorRank(color: string, colorOrder: string[]): number {
  const i = colorOrder.indexOf(color);
  return i === -1 ? 999 : i;
}

function runHasSideFlipOrCoincidence(projections: Array<Projection | null>, start: number, end: number, sideEpsM: number): boolean {
  let hasPositiveSide = false;
  let hasNegativeSide = false;
  let coincidentCount = 0;
  let sampleCount = 0;

  for (let i = start; i <= end; i += 1) {
    const side = projections[i]?.signedSideM;
    if (side == null || !Number.isFinite(side)) continue;

    sampleCount += 1;
    if (side > sideEpsM) hasPositiveSide = true;
    else if (side < -sideEpsM) hasNegativeSide = true;
    else coincidentCount += 1;
  }

  if (hasPositiveSide && hasNegativeSide) return true;
  return sampleCount > 0 && coincidentCount >= Math.max(2, Math.ceil(sampleCount * 0.8));
}

function mergeArcRanges(ranges: ArcRange[]): ArcRange[] {
  const sorted = ranges
    .filter((range) => Number.isFinite(range.startArc) && Number.isFinite(range.endArc) && range.endArc > range.startArc)
    .sort((a, b) => a.startArc - b.startArc || a.endArc - b.endArc);
  const merged: ArcRange[] = [];

  for (const range of sorted) {
    const last = merged.at(-1);
    if (last && range.startArc <= last.endArc) {
      last.endArc = Math.max(last.endArc, range.endArc);
    } else {
      merged.push({ ...range });
    }
  }

  return merged;
}

/**
 * The canonical color order uses lower indices for lines that stay put.
 */
type ResolvedParallelOptions = {
  colorOrder: string[];
  overlapDistM: number;
  sideEpsM: number;
  minOverlapM: number;
  laneWidthM: number;
  taperM: number;
};

function coveredFlipRanges(
  coords: Position[],
  arcs: number[],
  targetCoords: Position[],
  overlapDistM: number,
  sideEpsM: number,
  minOverlapM: number,
): ArcRange[] {
  const projections = coords.map((point) => projectToPolyline(targetCoords, point));
  const covered = projections.map((projection) => projection !== null && projection.distM <= overlapDistM);
  const ranges: ArcRange[] = [];
  let i = 0;
  while (i < covered.length) {
    if (!covered[i]) {
      i += 1;
      continue;
    }
    let j = i;
    while (j + 1 < covered.length && covered[j + 1]) j += 1;
    if (arcs[j] - arcs[i] >= minOverlapM && runHasSideFlipOrCoincidence(projections, i, j, sideEpsM)) {
      ranges.push({ startArc: arcs[i], endArc: arcs[j] });
    }
    i = j + 1;
  }
  return ranges;
}

function lowerColorTargets(
  feature: CrossColorLineFeature,
  lines: CrossColorLineFeature[],
  colorOrder: string[],
): CrossColorLineFeature[] {
  const color = feature.properties.color;
  if (!color) return [];
  const rank = colorRank(color, colorOrder);
  return lines.filter((target) => {
    const targetColor = target.properties.color;
    return Boolean(target !== feature && targetColor && targetColor !== color && colorRank(targetColor, colorOrder) < rank);
  });
}

function shiftFeatureOffLowerColors(
  feature: CrossColorLineFeature,
  lines: CrossColorLineFeature[],
  options: ResolvedParallelOptions,
): CrossColorFeature | null {
  const targets = lowerColorTargets(feature, lines, options.colorOrder);
  if (targets.length === 0) return null;

  const coords = feature.geometry.coordinates;
  const arcs = cumulativeArcs(coords);
  const rangesToOffset: ArcRange[] = [];
  for (const target of targets) {
    rangesToOffset.push(
      ...coveredFlipRanges(
        coords,
        arcs,
        target.geometry.coordinates,
        options.overlapDistM,
        options.sideEpsM,
        options.minOverlapM,
      ),
    );
  }

  const offsetRanges = mergeArcRanges(rangesToOffset);
  if (offsetRanges.length === 0) return null;

  let working = coords;
  for (const range of offsetRanges) {
    working = offsetPolylineOverExtent(working, range.startArc, range.endArc, options.laneWidthM, options.taperM);
  }
  return {
    ...feature,
    geometry: { type: "LineString", coordinates: working },
    properties: { ...feature.properties, cross_color_parallelized: true },
  };
}

export function parallelOffsetCrossColor(
  features: CrossColorFeature[],
  options: ParallelOffsetCrossColorOptions = {},
): ParallelOffsetCrossColorResult {
  const resolved: ResolvedParallelOptions = {
    colorOrder: options.colorOrder ?? [],
    overlapDistM: options.overlapDistM ?? 8,
    sideEpsM: options.sideEpsM ?? 0.5,
    minOverlapM: options.minOverlapM ?? 150,
    laneWidthM: options.laneWidthM ?? 8,
    taperM: options.taperM ?? 40,
  };
  const lines = features.filter(isLineFeature);
  const replaced = new Map<CrossColorFeature, CrossColorFeature>();
  let shiftedCount = 0;

  for (const feature of lines) {
    const shifted = shiftFeatureOffLowerColors(feature, lines, resolved);
    if (!shifted) continue;
    replaced.set(feature, shifted);
    shiftedCount += 1;
  }

  return { features: features.map((feature) => replaced.get(feature) ?? feature), shiftedCount };
}
