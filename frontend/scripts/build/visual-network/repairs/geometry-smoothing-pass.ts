import { countSharpCorners, smoothSharpCorners } from "../../smooth-polyline.ts";
import type { LineFeature, Position } from "../shared/types.ts";

export type GeometrySmoothingPassInput = {
  features: LineFeature[] | undefined;
  angleThresholdDeg: number;
  iterations: number;
  ratio: number;
  maxFilletM: number;
};

export type GeometrySmoothingPassResult = {
  smoothedFeatureCount: number;
  smoothedCornerCount: number;
};

function samePoint(left: Position, right: Position): boolean {
  return left[0] === right[0] && left[1] === right[1];
}

function smoothLineStringFeature(
  feature: LineFeature,
  input: Omit<GeometrySmoothingPassInput, "features">,
): number {
  if (feature.geometry?.type !== "LineString") return 0;
  const before = feature.geometry.coordinates;
  if (!Array.isArray(before) || before.length < 3) return 0;
  const sharpBefore = countSharpCorners(before, input.angleThresholdDeg);
  if (sharpBefore === 0) return 0;
  const after = smoothSharpCorners(before, {
    angleThresholdDeg: input.angleThresholdDeg,
    iterations: input.iterations,
    ratio: input.ratio,
    maxFilletM: input.maxFilletM,
  });
  if (after === before) return 0;
  // Endpoint-preservation invariant: junctions must not move.
  if (!(samePoint((after)[0], (before)[0]) && samePoint((after)[(after).length - 1], (before)[(before).length - 1]))) {
    console.error(
      `[visual-network] *** smoothing moved an endpoint on ${feature.properties?.bundle_id ?? "?"} -- refusing. ***`,
    );
    process.exit(1);
  }
  feature.geometry.coordinates = after;
  return sharpBefore;
}

export function applyGeometrySmoothingPass({
  features,
  angleThresholdDeg,
  iterations,
  ratio,
  maxFilletM,
}: GeometrySmoothingPassInput): GeometrySmoothingPassResult {
  let smoothedFeatureCount = 0;
  let smoothedCornerCount = 0;
  for (const feature of features ?? []) {
    const corners = smoothLineStringFeature(feature, {
      angleThresholdDeg,
      iterations,
      ratio,
      maxFilletM,
    });
    if (corners === 0) continue;
    smoothedFeatureCount += 1;
    smoothedCornerCount += corners;
  }

  return {
    smoothedFeatureCount,
    smoothedCornerCount,
  };
}
