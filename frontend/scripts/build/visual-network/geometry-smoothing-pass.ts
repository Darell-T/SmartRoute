import { countSharpCorners, smoothSharpCorners } from "../smooth-polyline.ts";
import type { LineFeature, Position } from "./types.ts";

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

export function applyGeometrySmoothingPass({
  features,
  angleThresholdDeg,
  iterations,
  ratio,
  maxFilletM,
}: GeometrySmoothingPassInput): GeometrySmoothingPassResult {
  let smoothedFeatureCount = 0;
  let smoothedCornerCount = 0;
  if (features) {
    for (const f of features) {
      if (f.geometry?.type !== "LineString") continue;
      const before = f.geometry.coordinates;
      if (!Array.isArray(before) || before.length < 3) continue;
      const sharpBefore = countSharpCorners(before, angleThresholdDeg);
      if (sharpBefore === 0) continue;
      const after = smoothSharpCorners(before, {
        angleThresholdDeg,
        iterations,
        ratio,
        maxFilletM,
      });
      if (after === before) continue;
      // Endpoint-preservation invariant: junctions must not move.
      const eqPt = (p: Position, q: Position) => p[0] === q[0] && p[1] === q[1];
      if (!eqPt(after[0], before[0]) || !eqPt(after[after.length - 1], before[before.length - 1])) {
        console.error(
          `[visual-network] *** smoothing moved an endpoint on ${f.properties?.bundle_id ?? "?"} -- refusing. ***`,
        );
        process.exit(1);
      }
      f.geometry.coordinates = after;
      smoothedFeatureCount += 1;
      smoothedCornerCount += sharpBefore;
    }
  }

  return {
    smoothedFeatureCount,
    smoothedCornerCount,
  };
}
