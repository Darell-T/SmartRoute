import type { LineFeature } from "../shared/types.ts";

export type SameColorJunctionBundleArtifacts = {
  visualFeatures?: LineFeature[];
};

export type SameColorJunctionStageInput = {
  bundleArtifacts: SameColorJunctionBundleArtifacts;
  sameColorSnapDistM: number;
  fanoutBlendM: number;
};
