import { simplifyTightCurves } from "../../simplify-tight-curves.ts";
import type { LineFeature, Position } from "../shared/types.ts";

export type TightCurveSimplificationPassInput = {
  features: LineFeature[] | undefined;
  tightTurnDeg: number;
  windowM: number;
  iterations: number;
  lambda: number;
};

export type TightCurveSimplificationPassResult = {
  tightCurveFeatureCount: number;
};

export function applyTightCurveSimplificationPass({
  features,
  tightTurnDeg,
  windowM,
  iterations,
  lambda,
}: TightCurveSimplificationPassInput): TightCurveSimplificationPassResult {
  let tightCurveFeatureCount = 0;
  if (features) {
    for (const f of features) {
      if (f.geometry?.type !== "LineString") continue;
      const before = f.geometry.coordinates;
      if (!Array.isArray(before) || before.length < 5) continue;
      const after = simplifyTightCurves(before, {
        tightTurnDeg,
        windowM,
        iterations,
        lambda,
      });
      if (after === before) continue;
      const eqPt = (p: Position, q: Position) => p[0] === q[0] && p[1] === q[1];
      if (!eqPt(after[0], before[0]) || !eqPt(after[after.length - 1], before[before.length - 1])) {
        console.error(
          `[visual-network] *** tight-curve simplify moved an endpoint on ${f.properties?.bundle_id ?? "?"} -- refusing. ***`,
        );
        process.exit(1);
      }
      f.geometry.coordinates = after;
      tightCurveFeatureCount += 1;
    }
  }

  return {
    tightCurveFeatureCount,
  };
}
