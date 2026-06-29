import { snapDanglingSameColorEndpoints } from "../snap-dangling-same-color.ts";
import type { LineFeature } from "./types.ts";

export type SameColorConvergenceSnapPassInput = {
  bundleArtifacts: {
    visualFeatures?: LineFeature[];
  };
  snapDistM: number;
};

export type SameColorConvergenceSnapPassResult = {
  sameColorConvergenceSnappedCount: number;
};

export function applySameColorConvergenceSnapPass({
  bundleArtifacts,
  snapDistM,
}: SameColorConvergenceSnapPassInput): SameColorConvergenceSnapPassResult {
  let sameColorConvergenceSnappedCount = 0;
  if (bundleArtifacts.visualFeatures) {
    const snap = snapDanglingSameColorEndpoints(bundleArtifacts.visualFeatures, {
      snapDistM,
    });
    bundleArtifacts.visualFeatures = snap.features;
    sameColorConvergenceSnappedCount = snap.snappedCount;
  }

  return {
    sameColorConvergenceSnappedCount,
  };
}
