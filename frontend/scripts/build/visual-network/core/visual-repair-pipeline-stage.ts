import { applyAuthoredLocationPatchesStage } from "../repairs/authored-location-patches-stage.ts";
import { applyDekalbSameColorCollapseStage } from "../repairs/dekalb-same-color-collapse-stage.ts";
import { applyMottHavenStage } from "../repairs/mott-haven-stage.ts";
import { applyPostMottLocalFixesStage } from "../repairs/post-mott-local-fixes-stage.ts";
import { applyRouteContinuityRepairStage } from "../repairs/route-continuity-repair-stage.ts";
import { applyTerminalOverhangTrimStage } from "../repairs/terminal-overhang-trim-stage.ts";
import type { BranchesByRoute } from "../inputs/branch-selection.ts";
import type { StopsById } from "../inputs/gtfs-topology.ts";
import type { LineFeature } from "../shared/types.ts";

type VisualRepairPipelineBundleArtifacts = {
  visualFeatures: LineFeature[];
};

type VisualRepairPipelineStageInput = {
  bundleArtifacts: VisualRepairPipelineBundleArtifacts;
  canonicalGeoJsonPath: string;
  stationsGeoJsonPath: string;
  branchesByRoute: BranchesByRoute;
  stopsById: StopsById;
  sameColorCollapseDistM: number;
  smoothAngleThresholdDeg: number;
  smoothIterations: number;
  smoothRatio: number;
  smoothMaxFilletM: number;
  tightCurveTurnDeg: number;
  tightCurveWindowM: number;
  tightCurveIterations: number;
  tightCurveLambda: number;
  sameColorSnapDistM: number;
  fanoutBlendM: number;
  bridgeMinGapM: number;
  bridgeMaxGapM: number;
  bridgeSubsetConnectorMaxGapM: number;
  offRevenueMaxM: number;
};

export function applyVisualRepairPipelineStage({
  bundleArtifacts,
  canonicalGeoJsonPath,
  stationsGeoJsonPath,
  branchesByRoute,
  stopsById,
  sameColorCollapseDistM,
  smoothAngleThresholdDeg,
  smoothIterations,
  smoothRatio,
  smoothMaxFilletM,
  tightCurveTurnDeg,
  tightCurveWindowM,
  tightCurveIterations,
  tightCurveLambda,
  sameColorSnapDistM,
  fanoutBlendM,
  bridgeMinGapM,
  bridgeMaxGapM,
  bridgeSubsetConnectorMaxGapM,
  offRevenueMaxM,
}: VisualRepairPipelineStageInput): void {
  applyDekalbSameColorCollapseStage({
    bundleArtifacts,
    sameColorCollapseDistM,
    smoothAngleThresholdDeg,
    smoothIterations,
    smoothRatio,
    smoothMaxFilletM,
    tightCurveTurnDeg,
    tightCurveWindowM,
    tightCurveIterations,
    tightCurveLambda,
    sameColorSnapDistM,
    fanoutBlendM,
  });
  applyRouteContinuityRepairStage({
    bundleArtifacts,
    canonicalGeoJsonPath,
    bridgeMinGapM,
    bridgeMaxGapM,
    bridgeSubsetConnectorMaxGapM,
    offRevenueMaxM,
  });

  applyAuthoredLocationPatchesStage({ bundleArtifacts });

  applyMottHavenStage({ bundleArtifacts });

  applyPostMottLocalFixesStage({ bundleArtifacts });

  applyTerminalOverhangTrimStage({
    bundleArtifacts,
    stationsGeoJsonPath,
    branchesByRoute,
    stopsById,
  });
}
