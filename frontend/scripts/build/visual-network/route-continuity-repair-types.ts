import type { LineFeature } from "./types.ts";

export type RouteContinuityRepairBundleArtifacts = {
  visualFeatures?: LineFeature[];
};

export type RouteContinuityRepairStageInput = {
  bundleArtifacts: RouteContinuityRepairBundleArtifacts;
  canonicalGeoJsonPath: string;
  bridgeMinGapM: number;
  bridgeMaxGapM: number;
  bridgeSubsetConnectorMaxGapM: number;
  offRevenueMaxM: number;
};
