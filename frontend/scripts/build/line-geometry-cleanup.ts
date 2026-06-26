const EARTH_RADIUS_M = 6371000;

import type { Feature, LineStringGeometry, Position } from "./types.ts";

type LineCleanupProperties = {
  bundle_id?: string;
  corridor_id?: string;
  source_corridor_id?: string | null;
  length_m?: number;
  coordinate_count?: number;
  max_segment_split?: boolean;
  max_segment_split_part?: number;
  max_segment_split_count?: number;
  [key: string]: unknown;
};

type LineCleanupFeature = Feature<LineStringGeometry, LineCleanupProperties>;

type SplitFeatureOptions = {
  maxSegmentM?: number;
  minSplitPartLengthM?: number;
};

type SplitFeaturesResult = {
  features: LineCleanupFeature[];
  splitFeatureCount: number;
  emittedPartCount: number;
};

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function lengthM(coords: Position[]): number {
  let total = 0;
  for (let index = 1; index < coords.length; index += 1) {
    total += haversineM(coords[index - 1], coords[index]);
  }
  return total;
}

function cloneWithPart(
  feature: LineCleanupFeature,
  coordinates: Position[],
  partIndex: number,
  partCount: number,
): LineCleanupFeature {
  const sourceCorridorId = feature.properties?.corridor_id ?? feature.properties?.bundle_id ?? "feature";
  return {
    ...feature,
    geometry: {
      type: "LineString",
      coordinates,
    },
    properties: {
      ...feature.properties,
      corridor_id: `${sourceCorridorId}-split-${partIndex}`,
      source_corridor_id: feature.properties?.source_corridor_id ?? feature.properties?.corridor_id ?? null,
      length_m: Number(lengthM(coordinates).toFixed(2)),
      coordinate_count: coordinates.length,
      max_segment_split: true,
      max_segment_split_part: partIndex,
      max_segment_split_count: partCount,
    },
  };
}

export function splitFeatureAtLongSegments(
  feature: LineCleanupFeature,
  options: SplitFeatureOptions = {},
): LineCleanupFeature[] {
  const maxSegmentM = options.maxSegmentM ?? 250;
  const minSplitPartLengthM = options.minSplitPartLengthM ?? 0;
  const coords = feature?.geometry?.coordinates;
  if (
    feature?.geometry?.type !== "LineString" ||
    !Array.isArray(coords) ||
    coords.length < 2
  ) {
    return [feature];
  }

  const runs = [];
  let current = [coords[0]];
  let hadLongSegment = false;

  for (let index = 1; index < coords.length; index += 1) {
    const previous = coords[index - 1];
    const next = coords[index];
    const distanceM = haversineM(previous, next);

    if (distanceM > maxSegmentM) {
      hadLongSegment = true;
      if (current.length >= 2) runs.push(current);
      current = [next];
      continue;
    }

    current.push(next);
  }

  if (current.length >= 2) runs.push(current);
  const keptRuns = runs.filter((run) => lengthM(run) >= minSplitPartLengthM);
  if (keptRuns.length === 0) return [];
  if (runs.length === 1 && !hadLongSegment) return [feature];

  return keptRuns.map((run, index) => cloneWithPart(feature, run, index, keptRuns.length));
}

export function splitFeaturesAtLongSegments(
  features: LineCleanupFeature[],
  options: SplitFeatureOptions = {},
): SplitFeaturesResult {
  const output: LineCleanupFeature[] = [];
  let splitFeatureCount = 0;
  let emittedPartCount = 0;

  for (const feature of features) {
    const parts = splitFeatureAtLongSegments(feature, options);
    output.push(...parts);
    if (parts.length > 1) {
      splitFeatureCount += 1;
      emittedPartCount += parts.length;
    }
  }

  return {
    features: output,
    splitFeatureCount,
    emittedPartCount,
  };
}
