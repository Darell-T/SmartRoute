import type { LineFeature } from "./types.ts";

export type SameColorJunctionBundleArtifacts = {
  visualFeatures?: LineFeature[];
};

export type SameColorJunctionStageInput = {
  bundleArtifacts: SameColorJunctionBundleArtifacts;
  sameColorSnapDistM: number;
  fanoutBlendM: number;
};
