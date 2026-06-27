import type { Feature, LineStringGeometry, Position } from "./types.ts";

type Vector = [number, number];

type BBox = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

type CulverProperties = {
  visual_feature_type?: string;
  corridor_id?: string;
  color?: unknown;
  route_ids?: unknown;
  color_route_ids?: unknown;
  lane_offset_baked?: boolean;
  culver_fg_prospect_smoothing?: boolean;
  culver_fg_prospect_min_before_m?: number | null;
  culver_fg_prospect_min_after_m?: number | null;
  [key: string]: unknown;
};

type CulverFeature = Feature<LineStringGeometry, CulverProperties>;

type SmoothingOptions = {
  bbox: BBox;
  marginM: number;
  targetSeparationM: number;
  blendM: number;
  sampleM: number;
  smoothingPasses: number;
  seamMaxM: number;
};

type PartialSmoothingOptions = Partial<SmoothingOptions>;

type ArcRange = {
  startArc: number;
  endArc: number;
};

type GreenChain = {
  firstFeature: CulverFeature;
  secondFeature: CulverFeature;
  first: Position[];
  second: Position[];
  firstReversed: boolean;
  secondReversed: boolean;
  gapM: number;
};

type ReplacementResult = {
  green: Position[];
  minBeforeM: number;
  minAfterM: number;
};

type Diagnostics = {
  applied: boolean;
  reason: string | null;
  orange_corridor_id: unknown;
  green_corridor_ids: unknown[];
  seam_gap_m: number | null;
  min_separation_before_m: number | null;
  min_separation_after_m: number | null;
};

type SmoothingResult = {
  features: CulverFeature[];
  diagnostics: Diagnostics;
};

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;
const ORANGE = "#FF6319";
const G_GREEN = "#6CBE45";
const DEFAULT_BBOX = {
  minLon: -73.9815,
  maxLon: -73.9740,
  minLat: 40.6510,
  maxLat: 40.6570,
};

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
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

function isLineFeature(feature: CulverFeature): boolean {
  return (
    feature?.geometry?.type === "LineString" &&
    Array.isArray(feature.geometry.coordinates) &&
    feature.geometry.coordinates.length >= 2
  );
}

function routeIdsOf(feature: CulverFeature): string[] {
  return Array.isArray(feature.properties?.route_ids)
    ? feature.properties.route_ids.map(String)
    : [];
}

function hasRoute(feature: CulverFeature, routeId: string): boolean {
  return routeIdsOf(feature).includes(routeId);
}

function cumulativeArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let index = 1; index < coords.length; index += 1) {
    arcs.push(arcs[index - 1] + haversineM(coords[index - 1], coords[index]));
  }
  return arcs;
}

function lengthM(coords: Position[]): number {
  const arcs = cumulativeArcs(coords);
  return arcs[arcs.length - 1] ?? 0;
}

function interpolateAtArc(coords: Position[], arcs: number[], targetArc: number): Position {
  if (targetArc <= 0) return coords[0];
  const total = arcs[arcs.length - 1];
  if (targetArc >= total) return coords[coords.length - 1];

  for (let index = 1; index < coords.length; index += 1) {
    if (arcs[index] >= targetArc) {
      const segmentLength = arcs[index] - arcs[index - 1];
      const t = segmentLength <= 0 ? 0 : (targetArc - arcs[index - 1]) / segmentLength;
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

function sliceArc(coords: Position[], startArc: number, endArc: number): Position[] {
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1];
  const start = Math.max(0, Math.min(total, startArc));
  const end = Math.max(0, Math.min(total, endArc));
  if (end - start <= 0.5) return [];

  const output = [interpolateAtArc(coords, arcs, start)];
  for (let index = 0; index < coords.length; index += 1) {
    if (arcs[index] > start && arcs[index] < end) output.push(coords[index]);
  }
  const endPoint = interpolateAtArc(coords, arcs, end);
  if (haversineM(output[output.length - 1], endPoint) > 0.01) output.push(endPoint);
  return output;
}

function samplePolyline(coords: Position[], count: number): Position[] {
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1];
  const output: Position[] = [];
  for (let index = 0; index < count; index += 1) {
    const t = count <= 1 ? 0 : index / (count - 1);
    output.push(interpolateAtArc(coords, arcs, total * t));
  }
  return output;
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

function removeAdjacentDuplicates(coords: Position[]): Position[] {
  const output: Position[] = [];
  for (const coord of coords) {
    if (output.length === 0 || haversineM(output[output.length - 1], coord) > 0.01) {
      output.push(coord);
    }
  }
  return output;
}

function replaceArcRange(coords: Position[], startArc: number, endArc: number, replacement: Position[]): Position[] {
  const arcs = cumulativeArcs(coords);
  const before: Position[] = [];
  const after: Position[] = [];
  for (let index = 0; index < coords.length; index += 1) {
    if (arcs[index] < startArc) before.push(coords[index]);
    if (arcs[index] > endArc) after.push(coords[index]);
  }
  return removeAdjacentDuplicates([...before, ...replacement, ...after]);
}

function smoothCenterline(coords: Position[], passes: number): Position[] {
  if (coords.length < 3 || passes <= 0) return coords;
  let current = coords;
  for (let pass = 0; pass < passes; pass += 1) {
    const next = [current[0]];
    for (let index = 1; index < current.length - 1; index += 1) {
      const lat = current[index][1];
      const prev = projectAt(current[index - 1], lat);
      const here = projectAt(current[index], lat);
      const after = projectAt(current[index + 1], lat);
      next.push(unprojectAt([
        prev[0] * 0.18 + here[0] * 0.64 + after[0] * 0.18,
        prev[1] * 0.18 + here[1] * 0.64 + after[1] * 0.18,
      ], lat));
    }
    next.push(current[current.length - 1]);
    current = next;
  }
  return current;
}

function normalAt(coords: Position[], index: number): Vector {
  const here = coords[index];
  const before = coords[Math.max(0, index - 1)];
  const after = coords[Math.min(coords.length - 1, index + 1)];
  const lat = here[1];
  const p0 = projectAt(before, lat);
  const p1 = projectAt(after, lat);
  const dx = p1[0] - p0[0];
  const dy = p1[1] - p0[1];
  const len = Math.hypot(dx, dy);
  if (len < 1e-9) return [0, 0];
  return [-dy / len, dx / len];
}

function offsetPoint(center: Position, normal: Vector, offsetM: number): Position {
  const lat = center[1];
  const p = projectAt(center, lat);
  return unprojectAt([p[0] + normal[0] * offsetM, p[1] + normal[1] * offsetM], lat);
}

function lerpPoint(from: Position, to: Position, t: number): Position {
  return [
    from[0] + (to[0] - from[0]) * t,
    from[1] + (to[1] - from[1]) * t,
  ];
}

function smoothstep(t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  return clamped * clamped * (3 - 2 * clamped);
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

function pointLineDistanceM(point: Position, line: Position[]): number {
  let best = Infinity;
  for (let index = 1; index < line.length; index += 1) {
    best = Math.min(best, pointSegmentDistanceM(point, line[index - 1], line[index]));
  }
  return best;
}

function minSeparationM(left: Position[], right: Position[]): number {
  let best = Infinity;
  for (const point of left) best = Math.min(best, pointLineDistanceM(point, right));
  for (const point of right) best = Math.min(best, pointLineDistanceM(point, left));
  return best;
}

function orientationNeedsReverse(left: Position[], right: Position[]): boolean {
  return (
    haversineM(left[0], right[0]) + haversineM(left[left.length - 1], right[right.length - 1]) >
    haversineM(left[0], right[right.length - 1]) + haversineM(left[left.length - 1], right[0])
  );
}

function findBestGreenChain(greenFeatures: CulverFeature[], seamMaxM: number): GreenChain | null {
  let best: GreenChain | null = null;
  for (let i = 0; i < greenFeatures.length; i += 1) {
    for (let j = 0; j < greenFeatures.length; j += 1) {
      if (i === j) continue;
      const firstFeature = greenFeatures[i];
      const secondFeature = greenFeatures[j];
      for (const firstReversed of [false, true]) {
        const first = firstReversed
          ? firstFeature.geometry.coordinates.slice().reverse()
          : firstFeature.geometry.coordinates.slice();
        for (const secondReversed of [false, true]) {
          const second = secondReversed
            ? secondFeature.geometry.coordinates.slice().reverse()
            : secondFeature.geometry.coordinates.slice();
          const gapM = haversineM(first[first.length - 1], second[0]);
          if (gapM > seamMaxM) continue;
          if (!best || gapM < best.gapM) {
            best = { firstFeature, secondFeature, first, second, firstReversed, secondReversed, gapM };
          }
        }
      }
    }
  }
  return best;
}

function buildGreenReplacementFromOrange(greenSegment: Position[], orangeSegment: Position[], options: SmoothingOptions): ReplacementResult {
  const orangeOriented = orientationNeedsReverse(greenSegment, orangeSegment)
    ? orangeSegment.slice().reverse()
    : orangeSegment;
  const spanM = Math.max(lengthM(greenSegment), lengthM(orangeOriented));
  const count = Math.max(16, Math.ceil(spanM / options.sampleM));
  const greenSamples = samplePolyline(greenSegment, count);
  const orangeSamples = smoothCenterline(samplePolyline(orangeOriented, count), options.smoothingPasses);
  const orangeArcs = cumulativeArcs(orangeSamples);
  const orangeTotal = orangeArcs[orangeArcs.length - 1] || 1;

  let signSum = 0;
  for (let index = 0; index < orangeSamples.length; index += 1) {
    const normal = normalAt(orangeSamples, index);
    const lat = orangeSamples[index][1];
    const orangeP = projectAt(orangeSamples[index], lat);
    const greenP = projectAt(greenSamples[index], lat);
    signSum += (greenP[0] - orangeP[0]) * normal[0] + (greenP[1] - orangeP[1]) * normal[1];
  }
  const sign = signSum < 0 ? -1 : 1;

  const output: Position[] = [];
  for (let index = 0; index < orangeSamples.length; index += 1) {
    const generated = offsetPoint(
      orangeSamples[index],
      normalAt(orangeSamples, index),
      sign * options.targetSeparationM,
    );
    const distanceToEdgeM = Math.min(orangeArcs[index], orangeTotal - orangeArcs[index]);
    const blend = smoothstep(Math.min(1, distanceToEdgeM / options.blendM));
    output.push(lerpPoint(greenSamples[index], generated, blend));
  }

  return {
    green: output,
    minBeforeM: minSeparationM(greenSamples, orangeOriented),
    minAfterM: minSeparationM(output, orangeOriented),
  };
}

function replaceFeatureByOrientedRange(
  feature: CulverFeature,
  orientedCoords: Position[],
  wasReversed: boolean,
  startArc: number,
  endArc: number,
  replacement: Position[],
): CulverFeature {
  const replaced = replaceArcRange(orientedCoords, startArc, endArc, replacement);
  return {
    ...feature,
    geometry: {
      ...feature.geometry,
      coordinates: wasReversed ? replaced.slice().reverse() : replaced,
    },
    properties: {
      ...feature.properties,
      culver_fg_prospect_smoothing: true,
    },
  };
}

export function applyCulverFgProspectSmoothing(features: CulverFeature[], rawOptions: PartialSmoothingOptions = {}): SmoothingResult {
  const options = {
    bbox: DEFAULT_BBOX,
    marginM: 260,
    targetSeparationM: 14,
    blendM: 120,
    sampleM: 6,
    smoothingPasses: 2,
    seamMaxM: 18,
    ...rawOptions,
  };

  const orange = features.find((feature) => (
    isLineFeature(feature) &&
    String(feature.properties?.color ?? "").toUpperCase() === ORANGE &&
    hasRoute(feature, "F") &&
    feature.geometry.coordinates.some((coord) => inBBox(coord, options.bbox))
  ));
  const greens = features.filter((feature) => (
    isLineFeature(feature) &&
    String(feature.properties?.color ?? "").toUpperCase() === G_GREEN &&
    hasRoute(feature, "G") &&
    feature.geometry.coordinates.some((coord) => inBBox(coord, options.bbox))
  ));

  const diagnostics: Diagnostics = {
    applied: false,
    reason: null,
    orange_corridor_id: orange?.properties?.corridor_id ?? null,
    green_corridor_ids: greens.map((feature) => feature.properties?.corridor_id ?? null),
    seam_gap_m: null,
    min_separation_before_m: null,
    min_separation_after_m: null,
  };

  if (!orange || greens.length < 2) {
    diagnostics.reason = "missing_fg_features";
    return { features, diagnostics };
  }

  const chain = findBestGreenChain(greens, options.seamMaxM);
  if (!chain) {
    diagnostics.reason = "missing_connected_g_chain";
    return { features, diagnostics };
  }

  const seam: Position = [
    (chain.first[chain.first.length - 1][0] + chain.second[0][0]) / 2,
    (chain.first[chain.first.length - 1][1] + chain.second[0][1]) / 2,
  ];
  const first = chain.first.slice();
  const second = chain.second.slice();
  first[first.length - 1] = seam;
  second[0] = seam;
  const firstLen = lengthM(first);
  const secondLen = lengthM(second);
  const composite = removeAdjacentDuplicates([...first, ...second.slice(1)]);

  const greenRange = arcRangeForBBox(composite, options.bbox, options.marginM);
  const orangeRange = arcRangeForBBox(orange.geometry.coordinates, options.bbox, options.marginM);
  if (!greenRange || !orangeRange) {
    diagnostics.reason = "missing_bbox_arc_range";
    return { features, diagnostics };
  }
  if (!(greenRange.startArc < firstLen && greenRange.endArc > firstLen)) {
    diagnostics.reason = "range_does_not_span_g_seam";
    return { features, diagnostics };
  }

  const greenSegment = sliceArc(composite, greenRange.startArc, greenRange.endArc);
  const orangeSegment = sliceArc(orange.geometry.coordinates, orangeRange.startArc, orangeRange.endArc);
  if (greenSegment.length < 2 || orangeSegment.length < 2) {
    diagnostics.reason = "degenerate_local_segment";
    return { features, diagnostics };
  }

  const replacement = buildGreenReplacementFromOrange(greenSegment, orangeSegment, options);
  const greenReplacementArcs = cumulativeArcs(replacement.green);
  const replacementTotal = greenReplacementArcs[greenReplacementArcs.length - 1] || 1;
  const greenSpan = greenRange.endArc - greenRange.startArc;
  const splitFraction = Math.max(0, Math.min(1, (firstLen - greenRange.startArc) / greenSpan));
  const replacementSplitArc = replacementTotal * splitFraction;
  const firstReplacement = sliceArc(replacement.green, 0, replacementSplitArc);
  const secondReplacement = sliceArc(replacement.green, replacementSplitArc, replacementTotal);
  const generatedSeam = interpolateAtArc(replacement.green, greenReplacementArcs, replacementSplitArc);

  if (firstReplacement.length < 2 || secondReplacement.length < 2) {
    diagnostics.reason = "degenerate_split_replacement";
    return { features, diagnostics };
  }
  firstReplacement[firstReplacement.length - 1] = generatedSeam;
  secondReplacement[0] = generatedSeam;

  const firstRangeStart = Math.max(0, greenRange.startArc);
  const firstRangeEnd = Math.min(firstLen, greenRange.endArc);
  const secondRangeStart = Math.max(0, greenRange.startArc - firstLen);
  const secondRangeEnd = Math.min(secondLen, greenRange.endArc - firstLen);

  diagnostics.applied = true;
  diagnostics.seam_gap_m = Number(chain.gapM.toFixed(2));
  diagnostics.min_separation_before_m = Number(replacement.minBeforeM.toFixed(2));
  diagnostics.min_separation_after_m = Number(replacement.minAfterM.toFixed(2));

  return {
    features: features.map((feature) => {
      if (feature === chain.firstFeature) {
        const updated = replaceFeatureByOrientedRange(
          feature,
          first,
          chain.firstReversed,
          firstRangeStart,
          firstRangeEnd,
          firstReplacement,
        );
        updated.properties.culver_fg_prospect_min_before_m = diagnostics.min_separation_before_m;
        updated.properties.culver_fg_prospect_min_after_m = diagnostics.min_separation_after_m;
        return updated;
      }
      if (feature === chain.secondFeature) {
        const updated = replaceFeatureByOrientedRange(
          feature,
          second,
          chain.secondReversed,
          secondRangeStart,
          secondRangeEnd,
          secondReplacement,
        );
        updated.properties.culver_fg_prospect_min_before_m = diagnostics.min_separation_before_m;
        updated.properties.culver_fg_prospect_min_after_m = diagnostics.min_separation_after_m;
        return updated;
      }
      return feature;
    }),
    diagnostics,
  };
}
