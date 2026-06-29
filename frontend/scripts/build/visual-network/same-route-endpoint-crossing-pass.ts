import { repairSameRouteEndpointCrossings } from "../same-route-junction-fabric.ts";
import type { LineFeature } from "./types.ts";

export type SameRouteEndpointCrossingPassInput = {
  bundleArtifacts: {
    visualFeatures?: LineFeature[];
  };
  maxEndpointOvershootM: number;
};

export type SameRouteEndpointCrossingPassResult = {
  sameRouteEndpointRepairCount: number;
};

export function applySameRouteEndpointCrossingPass({
  bundleArtifacts,
  maxEndpointOvershootM,
}: SameRouteEndpointCrossingPassInput): SameRouteEndpointCrossingPassResult {
  let sameRouteEndpointRepairCount = 0;
  if (bundleArtifacts.visualFeatures) {
    const repair = repairSameRouteEndpointCrossings(bundleArtifacts.visualFeatures, {
      maxEndpointOvershootM,
    });
    bundleArtifacts.visualFeatures = repair.features;
    sameRouteEndpointRepairCount = repair.repairCount;
  }

  return {
    sameRouteEndpointRepairCount,
  };
}
