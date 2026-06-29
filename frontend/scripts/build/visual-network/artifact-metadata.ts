// Shipped-artifact metadata builder. Owns the visual.geojson document schema
// and the provenance literal baked into the shipped artifact. This is a pure
// builder: the document shape is byte-identical to the orchestrator original.
//
// The orchestrator deliberately keeps ALL fs writes and OUT_* path resolution
// (a module's __dirname differs from the orchestrator's, so paths must never be
// recomputed here) — this module returns data only.

export type CandidateDocParameters = {
  minTripsPerBranch: number;
  resampleIntervalM: number;
  hausdorffMaxM: number;
  overlapMinRatio: number;
  tangentMaxDiffDeg: number;
  containmentAvgDistanceMaxM: number;
  containmentOverlapMinRatio: number;
};

export type BuildCandidateDocInput = {
  generatedAt: string;
  openDataSourceName: string;
  openDataSourceDatasetId: string;
  perRouteStats: any[];
  validationFailures: any[];
  bundleArtifacts: {
    bundleFeatures: any[];
    bundleLaneFeatures: any[];
    unbundledFeatures: any[];
    bundleGapFeatures: any[];
    visualFeatures: any[];
  };
  parameters: CandidateDocParameters;
};

export function buildCandidateDoc(input: BuildCandidateDocInput) {
  const {
    generatedAt,
    openDataSourceName,
    openDataSourceDatasetId,
    perRouteStats,
    validationFailures,
    bundleArtifacts,
    parameters,
  } = input;
  return {
    type: "FeatureCollection",
    metadata: {
      generated_at: generatedAt,
      source: "build-subway-visual-network.mjs Gate 2A-2H",
      gates: {
        "2A": "topology",
        "2B": "opendata-full-lines",
        "2C": "opendata-corridor-normalization",
        "2D": "connectivity",
        "2H": "bundle-lane-render-geometry",
      },
      visual_geometry_source: openDataSourceName,
      visual_geometry_source_dataset_id: openDataSourceDatasetId,
      validation: {
        total_routes: perRouteStats.length,
        routes_passed: perRouteStats.length - validationFailures.length,
        routes_failed: validationFailures.length,
        passed: validationFailures.length === 0,
      },
      bundle_summary: {
        bundle_count: bundleArtifacts.bundleFeatures.length,
        bundled_render_lane_count: bundleArtifacts.bundleLaneFeatures.length,
        corridors_converted_to_bundle_geometry:
          bundleArtifacts.bundleFeatures.length,
        remaining_unbundled_corridors: bundleArtifacts.unbundledFeatures.length,
        bundle_gap_count: bundleArtifacts.bundleGapFeatures.length,
      },
      parameters: {
        min_trips_per_branch: parameters.minTripsPerBranch,
        resample_interval_m: parameters.resampleIntervalM,
        hausdorff_max_m: parameters.hausdorffMaxM,
        overlap_min_ratio: parameters.overlapMinRatio,
        tangent_max_diff_deg: parameters.tangentMaxDiffDeg,
        containment_avg_distance_max_m: parameters.containmentAvgDistanceMaxM,
        containment_overlap_min_ratio: parameters.containmentOverlapMinRatio,
        open_data_path: "frontend/public/subway-lines-nyc-opendata.geojson",
      },
    },
    features: bundleArtifacts.visualFeatures,
  };
}
