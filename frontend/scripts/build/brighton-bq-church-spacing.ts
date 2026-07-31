import type { Feature, LineStringGeometry, Position } from "./types.ts";

export type Vector = [number, number];

type BBox = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

type BrightonProperties = {
  corridor_id?: unknown;
  color?: unknown;
  route_id?: unknown;
  route_ids?: unknown;
  color_route_ids?: unknown;
  brighton_bq_church_spacing?: boolean;
  brighton_bq_church_min_before_m?: number | null;
  brighton_bq_church_min_after_m?: number | null;
  brighton_bq_church_core_min_after_m?: number | null;
  brighton_bq_church_centerline_fit?: CenterlineFit | null;
  brighton_bq_church_max_turn_after_degrees?: number | null;
  [key: string]: unknown;
};

type BrightonFeature = Feature<LineStringGeometry, BrightonProperties>;

export type ArcRange = {
  startArc: number;
  endArc: number;
};

export type CenterlineFit = "raw_centerline" | "smoothed_raw_centerline" | "cubic_axis_fit" | "cubic_hermite_fit";

export type FittedCenterline = {
  coords: Position[];
  fit: CenterlineFit;
};

export type BrightonRawOptions = {
  bbox?: BBox;
  marginM?: number;
  targetSeparationM?: number;
  blendM?: number;
  sampleM?: number;
  smoothingPasses?: number;
  blendFromCore?: boolean;
};

export type BrightonOptions = Required<BrightonRawOptions>;

// Generalized name: the "yellow"/"orange" naming in BalancedPair below is a
// holdover from the Brighton B/Q origin of this helper. Shared-corridor
// separation enforcement (shared-corridor-separation-stage.ts) reuses
// buildBalancedPair for arbitrary color pairs -- "yellow" == the first/"a"
// member, "orange" == the second/"b" member.
export type BalancedOptions = BrightonOptions & {
  coreStartFraction: number;
  coreEndFraction: number;
  // When set, skip the signSum side-detection and place "yellow"/a on this
  // side (+1/-1) of the fitted centerline. Callers windowing a long corridor
  // need this: with near-superimposed inputs the detected sign is
  // floating-point noise and can flip between adjacent windows, which would
  // make the two output lines cross at window boundaries.
  forcedASign?: number;
};

export type BalancedPair = {
  yellow: Position[];
  orange: Position[];
  aSign: number;
  minBeforeM: number;
  minAfterM: number;
  centerlineFit: CenterlineFit;
  maxCenterlineTurnAfterDegrees: number;
  coreStartFraction: number;
  coreEndFraction: number;
  coreMinAfterM: number;
};

type BrightonDiagnostics = {
  applied: boolean;
  reason: string | null;
  yellow_corridor_id: unknown;
  orange_corridor_id: unknown;
  min_separation_before_m: number | null;
  min_separation_after_m: number | null;
  core_min_separation_after_m: number | null;
  centerline_fit: CenterlineFit | null;
  max_centerline_turn_after_degrees: number | null;
};

type BrightonResult = {
  features: BrightonFeature[];
  diagnostics: BrightonDiagnostics;
};

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;
const YELLOW = "#FCCC0A";
const ORANGE = "#FF6319";
const DEFAULT_BBOX: BBox = {
  minLon: -73.9670,
  maxLon: -73.9590,
  minLat: 40.6415,
  maxLat: 40.6505,
};

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

export function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number): number => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function projectAt(point: Position, lat: number): Vector {
  return [point[0] * metersPerDegLng(lat), point[1] * M_PER_DEG_LAT];
}

function unprojectAt(point: Vector, lat: number): Position {
  return [point[0] / metersPerDegLng(lat), point[1] / M_PER_DEG_LAT];
}

function inBBox(point: Position, bbox: BBox): boolean {
  return (
    point[0] >= bbox.minLon &&
    point[0] <= bbox.maxLon &&
    point[1] >= bbox.minLat &&
    point[1] <= bbox.maxLat
  );
}

function routeIdsOf(feature: BrightonFeature): string[] {
  const routeIds = feature.properties?.route_ids;
  return Array.isArray(routeIds) ? routeIds.map(String) : [];
}

function hasRoute(feature: BrightonFeature, routeId: string): boolean {
  return routeIdsOf(feature).includes(routeId);
}

function isLineFeature(feature: BrightonFeature): boolean {
  return (
    feature?.geometry?.type === "LineString" &&
    Array.isArray(feature.geometry.coordinates) &&
    feature.geometry.coordinates.length >= 2
  );
}

export function cumulativeArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let index = 1; index < coords.length; index += 1) {
    arcs.push(arcs[index - 1] + haversineM(coords[index - 1], coords[index]));
  }
  return arcs;
}

export function interpolateAtArc(coords: Position[], arcs: number[], targetArc: number): Position {
  if (targetArc <= 0) return coords[0];
  const total = arcs[arcs.length - 1];
  if (targetArc >= total) return coords[coords.length - 1];

  for (let index = 1; index < coords.length; index += 1) {
    if (arcs[index] >= targetArc) {
      const length = arcs[index] - arcs[index - 1];
      const t = length <= 0 ? 0 : (targetArc - arcs[index - 1]) / length;
      const from = coords[index - 1];
      const to = coords[index];
      return [
        from[0] + (to[0] - from[0]) * t,
        from[1] + (to[1] - from[1]) * t,
      ];
    }
  }

  return coords[coords.length - 1];
}

function arcRangeForBBox(coords: Position[], bbox: BBox, marginM: number): ArcRange | null {
  const arcs = cumulativeArcs(coords);
  const insideArcs: number[] = [];
  for (let index = 0; index < coords.length; index += 1) {
    if (inBBox(coords[index], bbox)) insideArcs.push(arcs[index]);
  }
  if (insideArcs.length === 0) return null;

  const total = arcs[arcs.length - 1];
  return {
    startArc: Math.max(0, Math.min(...insideArcs) - marginM),
    endArc: Math.min(total, Math.max(...insideArcs) + marginM),
  };
}

export function sliceArc(coords: Position[], startArc: number, endArc: number): Position[] {
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1];
  const start = Math.max(0, Math.min(total, startArc));
  const end = Math.max(0, Math.min(total, endArc));
  if (end - start <= 0.5) return [];

  const output: Position[] = [interpolateAtArc(coords, arcs, start)];
  for (let index = 0; index < coords.length; index += 1) {
    if (arcs[index] > start && arcs[index] < end) output.push(coords[index]);
  }
  const endPoint = interpolateAtArc(coords, arcs, end);
  if (haversineM(output[output.length - 1], endPoint) > 0.01) output.push(endPoint);
  return output;
}

export function samplePolyline(coords: Position[], count: number): Position[] {
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1];
  const output: Position[] = [];
  for (let index = 0; index < count; index += 1) {
    const t = count <= 1 ? 0 : index / (count - 1);
    output.push(interpolateAtArc(coords, arcs, total * t));
  }
  return output;
}

export function lengthM(coords: Position[]): number {
  const arcs = cumulativeArcs(coords);
  return arcs[arcs.length - 1] ?? 0;
}

function smoothCenterline(coords: Position[], passes: number): Position[] {
  if (coords.length < 3 || passes <= 0) return coords;
  let current = coords;
  for (let pass = 0; pass < passes; pass += 1) {
    const next: Position[] = [current[0]];
    for (let index = 1; index < current.length - 1; index += 1) {
      const lat = current[index][1];
      const prev = projectAt(current[index - 1], lat);
      const here = projectAt(current[index], lat);
      const after = projectAt(current[index + 1], lat);
      next.push(unprojectAt([
        prev[0] * 0.22 + here[0] * 0.56 + after[0] * 0.22,
        prev[1] * 0.22 + here[1] * 0.56 + after[1] * 0.22,
      ], lat));
    }
    next.push(current[current.length - 1]);
    current = next;
  }
  return current;
}

function solve2x2(a00: number, a01: number, a11: number, b0: number, b1: number): Vector {
  const determinant = a00 * a11 - a01 * a01;
  if (Math.abs(determinant) < 1e-9) return [0, 0];
  return [
    (b0 * a11 - b1 * a01) / determinant,
    (a00 * b1 - a01 * b0) / determinant,
  ];
}

function fitCubicAxisCenterline(coords: Position[]): FittedCenterline {
  if (coords.length < 4) return { coords, fit: "raw_centerline" };

  const refLat = coords[Math.floor(coords.length / 2)][1];
  const projected = coords.map((coord) => projectAt(coord, refLat));
  const first = projected[0];
  const last = projected[projected.length - 1];
  const horizontalSpan = Math.abs(last[0] - first[0]);
  const verticalSpan = Math.abs(last[1] - first[1]);
  const useVerticalAxis = verticalSpan >= horizontalSpan;
  const axisIndex = useVerticalAxis ? 1 : 0;
  const dependentIndex = useVerticalAxis ? 0 : 1;
  const axis0 = first[axisIndex];
  const axis1 = last[axisIndex];
  const dependent0 = first[dependentIndex];
  const dependent1 = last[dependentIndex];
  const axisSpan = axis1 - axis0;

  if (Math.abs(axisSpan) < 5) return { coords: smoothCenterline(coords, 2), fit: "smoothed_raw_centerline" };

  let a00 = 0;
  let a01 = 0;
  let a11 = 0;
  let b0 = 0;
  let b1 = 0;

  for (const point of projected) {
    const t = Math.max(0, Math.min(1, (point[axisIndex] - axis0) / axisSpan));
    const baseline = dependent0 + (dependent1 - dependent0) * t;
    const residual = point[dependentIndex] - baseline;
    const bendBasis = t * (1 - t);
    const asymmetryBasis = bendBasis * (2 * t - 1);
    a00 += bendBasis * bendBasis;
    a01 += bendBasis * asymmetryBasis;
    a11 += asymmetryBasis * asymmetryBasis;
    b0 += bendBasis * residual;
    b1 += asymmetryBasis * residual;
  }

  const [bendCoefficient, asymmetryCoefficient] = solve2x2(a00, a01, a11, b0, b1);
  const fitted: Position[] = [];
  for (let index = 0; index < coords.length; index += 1) {
    const t = coords.length <= 1 ? 0 : index / (coords.length - 1);
    const axis = axis0 + axisSpan * t;
    const baseline = dependent0 + (dependent1 - dependent0) * t;
    const bendBasis = t * (1 - t);
    const asymmetryBasis = bendBasis * (2 * t - 1);
    const dependent = baseline + bendCoefficient * bendBasis + asymmetryCoefficient * asymmetryBasis;
    const point: Vector = useVerticalAxis ? [dependent, axis] : [axis, dependent];
    fitted.push(unprojectAt(point, refLat));
  }

  fitted[0] = coords[0];
  fitted[fitted.length - 1] = coords[coords.length - 1];
  return { coords: fitted, fit: "cubic_axis_fit" };
}

function normalizedVector(vector: Vector, fallback: Vector): Vector {
  const length = Math.hypot(vector[0], vector[1]);
  if (length < 1e-6) return fallback;
  return [vector[0] / length, vector[1] / length];
}

export function fitHermiteCenterline(coords: Position[]): FittedCenterline {
  if (coords.length < 4) return fitCubicAxisCenterline(coords);

  const refLat = coords[Math.floor(coords.length / 2)][1];
  const projected = coords.map((coord) => projectAt(coord, refLat));
  const first = projected[0];
  const last = projected[projected.length - 1];
  const chord: Vector = [last[0] - first[0], last[1] - first[1]];
  const chordLength = Math.hypot(chord[0], chord[1]);
  if (chordLength < 5) return fitCubicAxisCenterline(coords);

  const chordUnit: Vector = [chord[0] / chordLength, chord[1] / chordLength];
  const window = Math.max(3, Math.min(18, Math.floor(projected.length * 0.08)));
  let startTangent: Vector = [
    projected[Math.min(projected.length - 1, window)][0] - first[0],
    projected[Math.min(projected.length - 1, window)][1] - first[1],
  ];
  let endTangent: Vector = [
    last[0] - projected[Math.max(0, projected.length - 1 - window)][0],
    last[1] - projected[Math.max(0, projected.length - 1 - window)][1],
  ];
  if (startTangent[0] * chordUnit[0] + startTangent[1] * chordUnit[1] < 0) {
    startTangent = chord;
  }
  if (endTangent[0] * chordUnit[0] + endTangent[1] * chordUnit[1] < 0) {
    endTangent = chord;
  }

  const startUnit = normalizedVector(startTangent, chordUnit);
  const endUnit = normalizedVector(endTangent, chordUnit);
  const tangentScale = chordLength * 0.72;
  const m0: Vector = [startUnit[0] * tangentScale, startUnit[1] * tangentScale];
  const m1: Vector = [endUnit[0] * tangentScale, endUnit[1] * tangentScale];

  const fitted: Position[] = [];
  for (let index = 0; index < coords.length; index += 1) {
    const t = coords.length <= 1 ? 0 : index / (coords.length - 1);
    const t2 = t * t;
    const t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1;
    const h10 = t3 - 2 * t2 + t;
    const h01 = -2 * t3 + 3 * t2;
    const h11 = t3 - t2;
    fitted.push(unprojectAt([
      h00 * first[0] + h10 * m0[0] + h01 * last[0] + h11 * m1[0],
      h00 * first[1] + h10 * m0[1] + h01 * last[1] + h11 * m1[1],
    ], refLat));
  }

  fitted[0] = coords[0];
  fitted[fitted.length - 1] = coords[coords.length - 1];
  return { coords: fitted, fit: "cubic_hermite_fit" };
}

function maxBearingDeltaDegrees(coords: Position[], windowM = 35): number {
  if (coords.length < 3) return 0;
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1] ?? 0;
  let maxDelta = 0;
  for (let index = 1; index < coords.length - 1; index += 1) {
    const hereArc = arcs[index];
    if (hereArc <= 0 || hereArc >= total) continue;
    const before = interpolateAtArc(coords, arcs, Math.max(0, hereArc - windowM));
    const here = coords[index];
    const after = interpolateAtArc(coords, arcs, Math.min(total, hereArc + windowM));
    if (haversineM(before, here) < 1 || haversineM(here, after) < 1) continue;
    const lat = here[1];
    const p0 = projectAt(before, lat);
    const p1 = projectAt(here, lat);
    const p2 = projectAt(after, lat);
    const bearingIn = Math.atan2(p1[1] - p0[1], p1[0] - p0[0]);
    const bearingOut = Math.atan2(p2[1] - p1[1], p2[0] - p1[0]);
    let delta = Math.abs((bearingOut - bearingIn) * 180 / Math.PI) % 360;
    if (delta > 180) delta = 360 - delta;
    maxDelta = Math.max(maxDelta, delta);
  }
  return maxDelta;
}

export function normalAt(coords: Position[], index: number): Vector {
  const here = coords[index];
  const before = coords[Math.max(0, index - 1)];
  const after = coords[Math.min(coords.length - 1, index + 1)];
  const lat = here[1];
  const p0 = projectAt(before, lat);
  const p1 = projectAt(after, lat);
  const dx = p1[0] - p0[0];
  const dy = p1[1] - p0[1];
  const length = Math.hypot(dx, dy);
  if (length < 1e-9) return [0, 0];
  return [-dy / length, dx / length];
}

export function lerpPoint(from: Position, to: Position, t: number): Position {
  return [
    from[0] + (to[0] - from[0]) * t,
    from[1] + (to[1] - from[1]) * t,
  ];
}

export function smoothstep(t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  return clamped * clamped * (3 - 2 * clamped);
}

export function offsetPoint(center: Position, normal: Vector, offsetM: number): Position {
  const lat = center[1];
  const p = projectAt(center, lat);
  return unprojectAt([p[0] + normal[0] * offsetM, p[1] + normal[1] * offsetM], lat);
}

export function removeAdjacentDuplicates(coords: Position[]): Position[] {
  const output: Position[] = [];
  for (const coord of coords) {
    if (output.length === 0 || haversineM(output[output.length - 1], coord) > 0.01) {
      output.push(coord);
    }
  }
  return output;
}

export function replaceArcRange(
  coords: Position[],
  startArc: number,
  endArc: number,
  replacement: Position[],
): Position[] {
  const arcs = cumulativeArcs(coords);
  const before: Position[] = [];
  const after: Position[] = [];
  for (let index = 0; index < coords.length; index += 1) {
    if (arcs[index] < startArc) before.push(coords[index]);
    if (arcs[index] > endArc) after.push(coords[index]);
  }
  return removeAdjacentDuplicates([...before, ...replacement, ...after]);
}

function pointSegmentDistanceM(point: Position, from: Position, to: Position): number {
  const lat = point[1];
  const p = projectAt(point, lat);
  const a = projectAt(from, lat);
  const b = projectAt(to, lat);
  const vx = b[0] - a[0];
  const vy = b[1] - a[1];
  const wx = p[0] - a[0];
  const wy = p[1] - a[1];
  const t = Math.max(0, Math.min(1, (vx * wx + vy * wy) / (vx * vx + vy * vy || 1)));
  return Math.hypot(p[0] - (a[0] + vx * t), p[1] - (a[1] + vy * t));
}

export function pointLineDistanceM(point: Position, line: Position[]): number {
  let best = Infinity;
  for (let index = 1; index < line.length; index += 1) {
    best = Math.min(best, pointSegmentDistanceM(point, line[index - 1], line[index]));
  }
  return best;
}

export function minSeparationM(left: Position[], right: Position[]): number {
  let best = Infinity;
  for (const point of left) best = Math.min(best, pointLineDistanceM(point, right));
  for (const point of right) best = Math.min(best, pointLineDistanceM(point, left));
  return best;
}

export function orientationNeedsReverse(left: Position[], right: Position[]): boolean {
  return (
    haversineM(left[0], right[0]) + haversineM(left[left.length - 1], right[right.length - 1]) >
    haversineM(left[0], right[right.length - 1]) + haversineM(left[left.length - 1], right[0])
  );
}

export function buildBalancedPair(
  yellowSegment: Position[],
  orangeSegment: Position[],
  options: BalancedOptions,
): BalancedPair {
  const reversedOrange = orientationNeedsReverse(yellowSegment, orangeSegment);
  const orangeOriented = reversedOrange ? orangeSegment.slice().reverse() : orangeSegment;
  const segmentLength = Math.max(lengthM(yellowSegment), lengthM(orangeOriented));
  const count = Math.max(12, Math.ceil(segmentLength / options.sampleM));
  const yellowSamples = samplePolyline(yellowSegment, count);
  const orangeSamples = samplePolyline(orangeOriented, count);
  const rawCenters: Position[] = yellowSamples.map((yellowPoint, index) => [
    (yellowPoint[0] + orangeSamples[index][0]) / 2,
    (yellowPoint[1] + orangeSamples[index][1]) / 2,
  ]);
  const fittedCenterline = fitHermiteCenterline(rawCenters);
  const centers = smoothCenterline(fittedCenterline.coords, options.smoothingPasses);
  const centerArcs = cumulativeArcs(centers);
  const centerTotal = centerArcs[centerArcs.length - 1] || 1;
  const coreStartArc = centerTotal * Math.max(0, Math.min(1, options.coreStartFraction));
  const coreEndArc = centerTotal * Math.max(0, Math.min(1, options.coreEndFraction));

  let signSum = 0;
  for (let index = 0; index < centers.length; index += 1) {
    const normal = normalAt(centers, index);
    const lat = centers[index][1];
    const center = projectAt(centers[index], lat);
    const yellow = projectAt(yellowSamples[index], lat);
    signSum += (yellow[0] - center[0]) * normal[0] + (yellow[1] - center[1]) * normal[1];
  }
  const yellowSign = options.forcedASign ?? (signSum < 0 ? -1 : 1);

  const yellowOut: Position[] = [];
  const orangeOut: Position[] = [];
  const coreMask: boolean[] = [];
  for (let index = 0; index < centers.length; index += 1) {
    const normal = normalAt(centers, index);
    const existingSeparation = haversineM(yellowSamples[index], orangeSamples[index]);
    const separation = Math.max(existingSeparation, options.targetSeparationM);
    const generatedYellow = offsetPoint(centers[index], normal, yellowSign * separation * 0.5);
    const generatedOrange = offsetPoint(centers[index], normal, -yellowSign * separation * 0.5);
    const distanceToCore =
      centerArcs[index] < coreStartArc
        ? coreStartArc - centerArcs[index]
        : centerArcs[index] > coreEndArc
          ? centerArcs[index] - coreEndArc
          : 0;
    const distanceToReplacementEdge = Math.min(centerArcs[index], centerTotal - centerArcs[index]);
    const blendDistance = options.blendFromCore ? distanceToCore : distanceToReplacementEdge;
    const blend =
      options.blendM <= 0
        ? 1
        : options.blendFromCore
          ? 1 - smoothstep(Math.min(1, blendDistance / options.blendM))
          : smoothstep(Math.min(1, blendDistance / options.blendM));
    coreMask.push(centerArcs[index] >= coreStartArc && centerArcs[index] <= coreEndArc);
    yellowOut.push(lerpPoint(yellowSamples[index], generatedYellow, blend));
    orangeOut.push(lerpPoint(orangeSamples[index], generatedOrange, blend));
  }
  const coreYellow = yellowOut.filter((_, index) => coreMask[index]);
  const coreOrange = orangeOut.filter((_, index) => coreMask[index]);

  return {
    yellow: yellowOut,
    orange: reversedOrange ? orangeOut.slice().reverse() : orangeOut,
    // Which side of the fitted centerline "yellow"/a landed on (+1 or -1),
    // independent of `reversedOrange` (that only affects array order, not
    // physical side). Callers outside the Brighton hotspot -- notably
    // shared-corridor-separation-stage.ts -- use this to write an accurate
    // lane_slot_semantic so the renderer's paint z-order reflects the actual
    // geometric side rather than falling back to a color-rank tiebreak.
    aSign: yellowSign,
    minBeforeM: minSeparationM(yellowSamples, orangeOriented),
    minAfterM: minSeparationM(yellowOut, orangeOut),
    centerlineFit: fittedCenterline.fit,
    maxCenterlineTurnAfterDegrees: maxBearingDeltaDegrees(centers),
    coreStartFraction: options.coreStartFraction,
    coreEndFraction: options.coreEndFraction,
    coreMinAfterM:
      coreYellow.length >= 2 && coreOrange.length >= 2
        ? minSeparationM(coreYellow, coreOrange)
        : minSeparationM(yellowOut, orangeOut),
  };
}

export function applyBrightonBqChurchSpacing(
  features: BrightonFeature[],
  rawOptions: BrightonRawOptions = {},
): BrightonResult {
  const options: BrightonOptions = {
    bbox: DEFAULT_BBOX,
    marginM: 90,
    targetSeparationM: 13,
    blendM: 55,
    sampleM: 6,
    smoothingPasses: 3,
    blendFromCore: false,
    ...rawOptions,
  };

  const yellow = features.find((feature) => (
    isLineFeature(feature) &&
    String(feature.properties?.color ?? "").toUpperCase() === YELLOW &&
    hasRoute(feature, "Q") &&
    feature.geometry.coordinates.some((coord) => inBBox(coord, options.bbox))
  ));
  const orange = features.find((feature) => (
    isLineFeature(feature) &&
    String(feature.properties?.color ?? "").toUpperCase() === ORANGE &&
    hasRoute(feature, "B") &&
    feature.geometry.coordinates.some((coord) => inBBox(coord, options.bbox))
  ));

  const diagnostics: BrightonDiagnostics = {
    applied: false,
    reason: null,
    yellow_corridor_id: yellow?.properties?.corridor_id ?? null,
    orange_corridor_id: orange?.properties?.corridor_id ?? null,
    min_separation_before_m: null,
    min_separation_after_m: null,
    core_min_separation_after_m: null,
    centerline_fit: null,
    max_centerline_turn_after_degrees: null,
  };

  if (!yellow || !orange) {
    diagnostics.reason = "missing_bq_features";
    return { features, diagnostics };
  }

  const yellowRange = arcRangeForBBox(yellow.geometry.coordinates, options.bbox, options.marginM);
  const orangeRange = arcRangeForBBox(orange.geometry.coordinates, options.bbox, options.marginM);
  const yellowCoreRange = arcRangeForBBox(yellow.geometry.coordinates, options.bbox, 0);
  const orangeCoreRange = arcRangeForBBox(orange.geometry.coordinates, options.bbox, 0);
  if (!yellowRange || !orangeRange) {
    diagnostics.reason = "missing_bbox_arc_range";
    return { features, diagnostics };
  }
  if (!yellowCoreRange || !orangeCoreRange) {
    diagnostics.reason = "missing_bbox_core_arc_range";
    return { features, diagnostics };
  }

  const yellowSegment = sliceArc(yellow.geometry.coordinates, yellowRange.startArc, yellowRange.endArc);
  const orangeSegment = sliceArc(orange.geometry.coordinates, orangeRange.startArc, orangeRange.endArc);
  if (yellowSegment.length < 2 || orangeSegment.length < 2) {
    diagnostics.reason = "degenerate_local_segment";
    return { features, diagnostics };
  }

  const yellowSpan = Math.max(1, yellowRange.endArc - yellowRange.startArc);
  const orangeSpan = Math.max(1, orangeRange.endArc - orangeRange.startArc);
  const yellowCoreStart = (yellowCoreRange.startArc - yellowRange.startArc) / yellowSpan;
  const yellowCoreEnd = (yellowCoreRange.endArc - yellowRange.startArc) / yellowSpan;
  const orangeCoreStart = (orangeCoreRange.startArc - orangeRange.startArc) / orangeSpan;
  const orangeCoreEnd = (orangeCoreRange.endArc - orangeRange.startArc) / orangeSpan;
  const coreStartFraction = Math.max(0, Math.min(1, (yellowCoreStart + orangeCoreStart) / 2));
  const coreEndFraction = Math.max(coreStartFraction, Math.min(1, (yellowCoreEnd + orangeCoreEnd) / 2));

  const balanced = buildBalancedPair(yellowSegment, orangeSegment, {
    ...options,
    coreStartFraction,
    coreEndFraction,
  });
  diagnostics.applied = true;
  diagnostics.min_separation_before_m = Number(balanced.minBeforeM.toFixed(2));
  diagnostics.min_separation_after_m = Number(balanced.minAfterM.toFixed(2));
  diagnostics.core_min_separation_after_m = Number(balanced.coreMinAfterM.toFixed(2));
  diagnostics.centerline_fit = balanced.centerlineFit;
  diagnostics.max_centerline_turn_after_degrees = Number(
    balanced.maxCenterlineTurnAfterDegrees.toFixed(2),
  );

  return {
    features: features.map((feature) => {
      if (feature === yellow) {
        return {
          ...feature,
          geometry: {
            ...feature.geometry,
            coordinates: replaceArcRange(
              feature.geometry.coordinates,
              yellowRange.startArc,
              yellowRange.endArc,
              balanced.yellow,
            ),
          },
          properties: {
            ...feature.properties,
            brighton_bq_church_spacing: true,
            brighton_bq_church_min_before_m: diagnostics.min_separation_before_m,
            brighton_bq_church_min_after_m: diagnostics.min_separation_after_m,
            brighton_bq_church_core_min_after_m: diagnostics.core_min_separation_after_m,
            brighton_bq_church_centerline_fit: diagnostics.centerline_fit,
            brighton_bq_church_max_turn_after_degrees: diagnostics.max_centerline_turn_after_degrees,
          },
        };
      }
      if (feature === orange) {
        return {
          ...feature,
          geometry: {
            ...feature.geometry,
            coordinates: replaceArcRange(
              feature.geometry.coordinates,
              orangeRange.startArc,
              orangeRange.endArc,
              balanced.orange,
            ),
          },
          properties: {
            ...feature.properties,
            brighton_bq_church_spacing: true,
            brighton_bq_church_min_before_m: diagnostics.min_separation_before_m,
            brighton_bq_church_min_after_m: diagnostics.min_separation_after_m,
            brighton_bq_church_core_min_after_m: diagnostics.core_min_separation_after_m,
            brighton_bq_church_centerline_fit: diagnostics.centerline_fit,
            brighton_bq_church_max_turn_after_degrees: diagnostics.max_centerline_turn_after_degrees,
          },
        };
      }
      return feature;
    }),
    diagnostics,
  };
}
