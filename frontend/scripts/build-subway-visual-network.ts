#!/usr/bin/env node
//
// SmartRoute subway visual-network builder.
//
// Architectural pivot from the failed pixel-skeleton pipeline:
//   - GTFS stop sequences are the authoritative source of route continuity.
//   - Geometry is just a visual coat on stop-pair edges.
//   - Shared corridors emerge from edges with near-identical sliced geometry.
//   - Connectivity validation is a hard gate before any production rendering.
//
// This script runs offline. The output artifacts are:
//   - subway-network.visual-debug-topology.json (Gate 2A)
//   - subway-network.visual-debug-edges.geojson (Gate 2B)
//   - subway-network.visual-debug-corridors.json (Gate 2C)
//   - subway-network.visual-debug-route-components.json (Gate 2D)
//   - artifacts/debug/subway-network.visual.candidate.geojson (always written)
//   - subway-network.visual.geojson (ONLY promoted after all gates pass)
//
// This script does NOT delete the legacy skeleton artifacts; they remain
// orphan debug data until the runtime opt-in flips over.

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import {
  OPEN_DATA_SOURCE_DATASET_ID,
  OPEN_DATA_SOURCE_NAME,
} from "./build/opendata-subway-lines.ts";
import {
  compareRouteIds,
  normalizeRouteId,
} from "./build/visual-network/shared/route-config.ts";
import { parseZipEntries } from "./build/visual-network/inputs/gtfs-ingest.ts";
import { buildGtfsTopologyStage } from "./build/visual-network/inputs/gtfs-topology-stage.ts";
import {
  HAUSDORFF_MAX_M,
  JUNCTION_BRIDGE_MAX_M,
  LANE_WIDTH_METERS,
  RESAMPLE_INTERVAL_M,
  geometryStats,
} from "./build/visual-network/shared/geometry-utils.ts";
import { buildOpenDataVisualInputStage } from "./build/visual-network/inputs/opendata-visual-input-stage.ts";
import { buildCorridorMetadataStage } from "./build/visual-network/core/corridor-metadata-stage.ts";
import { applyPhase3dSameColorMergeStage } from "./build/visual-network/core/phase-3d-same-color-merge-stage.ts";
import { buildStageDSpinePrepStage } from "./build/visual-network/core/stage-d-spine-prep-stage.ts";
import { applyPhase3cLaneContinuityStage } from "./build/visual-network/core/phase-3c-lane-continuity-stage.ts";
import { applyLaneOffsetFinalizationStage } from "./build/visual-network/core/lane-offset-finalization-stage.ts";
import { applyVisualRepairPipelineStage } from "./build/visual-network/core/visual-repair-pipeline-stage.ts";
import { writeVisualArtifactStage } from "./build/visual-network/output/artifact-writer-stage.ts";
import { reportFinalTopologySummaryStage } from "./build/visual-network/validation/final-reporting-stage.ts";
import { runValidationReportingStage } from "./build/visual-network/validation/validation-reporting-stage.ts";

// --- Pragmatic feature-bag types from the mechanical Batch 26 .ts conversion
// live in visual-network/shared/types.ts and remain intentionally permissive. ---

const here = __dirname;
const frontendRoot = resolve(here, "..");
const publicDir = resolve(frontendRoot, "public");
// Engineering-only debug artifacts go OUTSIDE public/ so they are never served
// in production. Only runtime artifacts (subway-network.visual.geojson, etc.)
// stay in public/. This directory is git-ignored.
const debugDir = resolve(frontendRoot, "artifacts", "debug");
mkdirSync(debugDir, { recursive: true });
const cacheDir = resolve(frontendRoot, ".gtfs-cache");
const ZIP_PATH = resolve(cacheDir, "google_transit.zip");

const OUT_TOPOLOGY_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-topology.json",
);
const OUT_EDGES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-edges.geojson",
);
const OUT_OPENDATA_LINES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-opendata-lines.geojson",
);
const OUT_OPENDATA_OVERLAPS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-opendata-overlaps.geojson",
);
const OUT_CORRIDORS_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-corridors.json",
);
const OUT_CORRIDORS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-corridors.geojson",
);
const OUT_ROUTE_COMPONENTS_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-route-components.json",
);
const OUT_ANOMALIES_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-anomalies.json",
);
const OUT_ANOMALIES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-anomalies.geojson",
);
const OUT_RENDER_LANE_CONTINUITY_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-render-lane-continuity.json",
);
const OUT_MISSING_ROUTE_LANES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-missing-route-lanes.geojson",
);
const OUT_JUNCTION_ANCHORS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-junction-anchors.geojson",
);
const OUT_JUNCTION_SNAPS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-junction-snaps.geojson",
);
const OUT_BUNDLES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-bundles.geojson",
);
const OUT_BUNDLE_LANES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-bundle-lanes.geojson",
);
const OUT_BUNDLE_GAPS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-bundle-gaps.geojson",
);
const OUT_VISUAL_CANDIDATE = resolve(
  debugDir,
  "subway-network.visual.candidate.geojson",
);
const OUT_VISUAL_FINAL = resolve(
  publicDir,
  "subway-network.visual.geojson",
);
const OUT_SPINES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-spines.geojson",
);
const OPEN_DATA_LINES_PATH = resolve(publicDir, "subway-lines-nyc-opendata.geojson");
const STATIONS_GEOJSON_PATH = resolve(publicDir, "subway-network.stations.geojson");
const OUT_PHYSICAL_BUNDLES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-physical-bundles.geojson");
const OUT_PHYSICAL_BUNDLE_LANES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-physical-bundle-lanes.geojson");
const OUT_PHYSICAL_BUNDLE_REJECTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-physical-bundle-rejects.geojson");
const OUT_TRANSITIVE_BUNDLES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-transitive-bundles.geojson");
const OUT_LANE_ORDERS_JSON = resolve(debugDir, "subway-network.visual-debug-lane-orders.json");
const OUT_BRANCH_TRANSITIONS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-branch-transitions.geojson");
const OUT_SAME_COLOR_MERGES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-same-color-merges.geojson");
const OUT_CROSS_COLOR_SPREAD_GEOJSON = resolve(debugDir, "subway-network.visual-debug-cross-color-spread.geojson");
const OUT_CROSS_COLOR_SEGMENTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-cross-color-segments.geojson");
const OUT_MATERIALIZED_BUNDLES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-materialized-bundles.geojson");
const OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-bundle-fanouts.geojson");
const OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-bundle-splits.geojson");
const OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-bundle-junction-defects.geojson");

if (!existsSync(STATIONS_GEOJSON_PATH)) {
  throw new Error(
    `${STATIONS_GEOJSON_PATH} is required input and currently has no generator; keep the checked-in station artifact present before running transit builds.`,
  );
}

// =====================================================================
// Tunables (Gate 2A)
// =====================================================================

// Minimum number of trips for a (route, direction, terminal_pair) to count
// as a "branch worth rendering". Filters out late-night specials, one-off
// reroutes, and yard moves while keeping the everyday + peak service variants.
const MIN_TRIPS_PER_BRANCH = 5;
const OPEN_DATA_MIN_FRAGMENT_LENGTH_M = 15;
// JUNCTION_BRIDGE_MAX_M (legacy buildJunctionBridges gap-bridge max, 90m) is
// owned by visual-network/shared/geometry-utils.ts and imported above.
// Max distance for promoting a branch_transition. Distinct from
// JUNCTION_BRIDGE_MAX_M (legacy buildJunctionBridges). The audit in Phase 3a
// showed all production-quality transitions are <= ~5m at junction stations,
// plus a long-tail outlier at 42.85m (G at Fulton St) that we drop for now.
// 35m gives ample headroom for legitimate transitions while excluding outliers.
const BRANCH_TRANSITION_MAX_M = 35;
// Fix 3: per-slot lane width baked into geometry at build time. The constants
// are owned by visual-network/shared/geometry-utils.ts and imported here. Pushed 12->18m
// (user-authorized) for Apple-Maps parallel-lane clarity: at the old pitch
// bundled colors collapsed behind the strongest one. 18m is the practical
// ceiling -- the widest shared-stop bar scales with pitch and the 60m cap
// (subway-station-overlay.check.mjs) is the binding limit (was ~45.5m at 14m,
// ~58.5m projected at 18m). Widening the BAKE (vs the runtime screen-offset) is
// the seam-safer lever: both endpoints at a split move by the same vector. This
// is the +50% regime that historically tore trunks, so the build's endpoint
// tripwires (exit(1) on any moved junction endpoint) gate it -- if the build
// fails or the bar cap trips, fall back 18 -> 17 -> 16.
const PHYSICAL_BUNDLE_SUBSTITUTE_CONFIDENCE_MIN = 0.75;
const BUNDLE_OVERLAP_DIST_MAX_M = 15;
const BUNDLE_SHARED_LEN_MIN_M = 250;
const BUNDLE_SPLIT_SAMPLE_M = 5;
const FANOUT_BLEND_M = 100;

// =====================================================================
// Phase 2A — GTFS topology + per-route branches
// =====================================================================

console.log("[visual-network] reading GTFS zip:", ZIP_PATH);
if (!existsSync(ZIP_PATH)) {
  throw new Error(
    `GTFS cache missing at ${ZIP_PATH}. Run "npm run build:network" first ` +
    `to populate the cache (regenerate-canonical-from-gtfs.mjs downloads it).`,
  );
}
const zipBuffer = readFileSync(ZIP_PATH);
const gtfs = parseZipEntries(zipBuffer, [
  "stops.txt",
  "trips.txt",
  "stop_times.txt",
  "routes.txt",
]);

// --- Build GTFS-derived topology stage outputs. ---
const {
  stopsById,
  branchesByRoute,
  droppedLowFreqBranches,
  topologyDoc,
  edgeFeatures,
  topologyEdgeDiagnostics,
  expectedOpenDataRouteIds,
  expectedEdges,
} = buildGtfsTopologyStage({
  gtfs,
  minTripsPerBranch: MIN_TRIPS_PER_BRANCH,
  normalizeRouteId,
  geometryStats,
  log: (message) => console.log(message),
});

mkdirSync(dirname(OUT_TOPOLOGY_JSON), { recursive: true });
writeFileSync(OUT_TOPOLOGY_JSON, `${JSON.stringify(topologyDoc, null, 2)}\n`);
console.log(`[visual-network] wrote ${OUT_TOPOLOGY_JSON}`);

const SPARSE_LONG_SLICE_M = 300;
const MAX_SEGMENT_ANOMALY_M = 250;
const PROJECTION_ANOMALY_M = 125;

const OVERLAP_MIN_RATIO = 0.6;
const TANGENT_MAX_DIFF_DEG = 30;
const GRID_CELL_M = 50;
const CONTAINMENT_AVG_DISTANCE_MAX_M = 15;
const CONTAINMENT_OVERLAP_MIN_RATIO = 0.85;
const OVERLAP_SHARED_LEN_MIN_M = 250;

// Bug 3 / DeKalb: round sharp single-vertex elbows in the final render geometry.
const SMOOTH_ANGLE_THRESHOLD_DEG = 35; // only corners sharper than this are cut
const SMOOTH_ITERATIONS = 3;
const SMOOTH_RATIO = 0.22;             // Chaikin cut fraction of the adjacent leg
const SMOOTH_MAX_FILLET_M = 18;        // hard cap on cut distance from the corner

// Apple-look tight-curve simplification: relax hairpins (a lot of total turning
// packed into a short arc, e.g. the 5 Mott Haven curl) toward a gentler arc.
const TIGHT_CURVE_TURN_DEG = 65;   // total |turn| within the window that marks a run "tight"
const TIGHT_CURVE_WINDOW_M = 50;   // arc half-window used to accumulate turning
const TIGHT_CURVE_ITERATIONS = 45; // Laplacian relaxation passes on tight vertices
const TIGHT_CURVE_LAMBDA = 0.5;    // relaxation strength (0..1)

// Off-revenue re-route (single owner for off-shape excursions like the 5 at
// Mott Haven): replace OpenData wander > this many meters from the route's GTFS
// revenue shape with the shape's own sub-path. Runs as the LAST geometry pass.
const OFF_REVENUE_MAX_M = 55;

// Same-color convergence snap: pull a dangling lane endpoint onto the trunk it
// merges into so converging same-color lanes touch instead of hanging short.
const SAME_COLOR_SNAP_DIST_M = 14;

// Route gap bridging: close small same-route seams left at fanout/junction
// boundaries (base-vs-member geometry differs by up to the overlap tolerance).
const BRIDGE_MIN_GAP_M = 6;            // endpoints closer than this are already joined
const BRIDGE_MAX_GAP_M = 28;          // never bridge wider than this (avoid chord-cutting real gaps)
const BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M = JUNCTION_BRIDGE_MAX_M;

// Same-color collapse: same-color features whose vertices fall within this of a
// longer same-color line are snapped onto it (rendered as one line). Tuned a bit
// above LANE_WIDTH_METERS so adjacent same-color lanes on one track merge, while
// genuinely-separate same-color tracks (further apart) stay distinct.
const SAME_COLOR_COLLAPSE_DIST_M = 12;

// Densify OpenData chords longer than this (km-scale straight segments) before
// lane offsetting + smoothing, so coarse corridors can render as clean curves.
const DENSIFY_MAX_SEGMENT_M = 250;
const DENSIFY_STEP_M = 40;

const {
  pairsConsidered,
  pairsMatched,
  matchedPairs,
  corridorFeatures,
  corridorRows,
} = buildOpenDataVisualInputStage({
  openDataLinesPath: OPEN_DATA_LINES_PATH,
  expectedOpenDataRouteIds,
  expectedEdges,
  topologyEdgeDiagnostics,
  openDataMinFragmentLengthM: OPEN_DATA_MIN_FRAGMENT_LENGTH_M,
  paths: {
    opendataLinesGeoJson: OUT_OPENDATA_LINES_GEOJSON,
    edgesGeoJson: OUT_EDGES_GEOJSON,
    opendataOverlapsGeoJson: OUT_OPENDATA_OVERLAPS_GEOJSON,
  },
  parameters: {
    overlapMinRatio: OVERLAP_MIN_RATIO,
    overlapSharedLenMinM: OVERLAP_SHARED_LEN_MIN_M,
    containmentAvgDistanceMaxM: CONTAINMENT_AVG_DISTANCE_MAX_M,
    tangentMaxDiffDeg: TANGENT_MAX_DIFF_DEG,
  },
});
const JUNCTION_SNAP_MAX_M = 25;

const { junctionSnapDiagnostics, laneChainDiagnostics } = buildCorridorMetadataStage({
  corridorFeatures,
  junctionSnapMaxM: JUNCTION_SNAP_MAX_M,
});
const edgeById = new Map(
  edgeFeatures.map((feature) => [feature.properties.edge_id, feature]),
);

applyPhase3dSameColorMergeStage({
  corridorFeatures,
  sameColorMergesGeoJsonPath: OUT_SAME_COLOR_MERGES_GEOJSON,
});

const { bundleArtifacts } = buildStageDSpinePrepStage({
  corridorFeatures,
  paths: {
    spinesGeoJson: OUT_SPINES_GEOJSON,
    transitiveBundlesGeoJson: OUT_TRANSITIVE_BUNDLES_GEOJSON,
    materializedBundlesGeoJson: OUT_MATERIALIZED_BUNDLES_GEOJSON,
    materializedBundleFanoutsGeoJson: OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON,
    materializedBundleSplitsGeoJson: OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON,
    materializedBundleDefectsGeoJson: OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON,
    physicalBundlesGeoJson: OUT_PHYSICAL_BUNDLES_GEOJSON,
    physicalBundleLanesGeoJson: OUT_PHYSICAL_BUNDLE_LANES_GEOJSON,
    physicalBundleRejectsGeoJson: OUT_PHYSICAL_BUNDLE_REJECTS_GEOJSON,
  },
  parameters: {
    openDataMinFragmentLengthM: OPEN_DATA_MIN_FRAGMENT_LENGTH_M,
    densifyMaxSegmentM: DENSIFY_MAX_SEGMENT_M,
    densifyStepM: DENSIFY_STEP_M,
    physicalBundleSubstituteConfidenceMin: PHYSICAL_BUNDLE_SUBSTITUTE_CONFIDENCE_MIN,
    bundleOverlapDistMaxM: BUNDLE_OVERLAP_DIST_MAX_M,
    bundleSharedLenMinM: BUNDLE_SHARED_LEN_MIN_M,
    bundleSplitSampleM: BUNDLE_SPLIT_SAMPLE_M,
    fanoutBlendM: FANOUT_BLEND_M,
    laneWidthM: LANE_WIDTH_METERS,
  },
});

applyPhase3cLaneContinuityStage({
  bundleArtifacts,
  stopsById,
  branchTransitionsGeoJsonPath: OUT_BRANCH_TRANSITIONS_GEOJSON,
  branchTransitionMaxM: BRANCH_TRANSITION_MAX_M,
});

applyLaneOffsetFinalizationStage({
  bundleArtifacts,
  crossColorSpreadGeoJsonPath: OUT_CROSS_COLOR_SPREAD_GEOJSON,
  crossColorSegmentsGeoJsonPath: OUT_CROSS_COLOR_SEGMENTS_GEOJSON,
  laneOrdersJsonPath: OUT_LANE_ORDERS_JSON,
});

const { perRouteStats, validationFailures } = runValidationReportingStage({
  edgeFeatures,
  corridorFeatures,
  corridorRows,
  pairsConsidered,
  pairsMatched,
  matchedPairs,
  junctionSnapDiagnostics,
  laneChainDiagnostics,
  bundleArtifacts,
  edgeById,
  stopsById,
  paths: {
    corridorsGeoJson: OUT_CORRIDORS_GEOJSON,
    corridorsJson: OUT_CORRIDORS_JSON,
    junctionAnchorsGeoJson: OUT_JUNCTION_ANCHORS_GEOJSON,
    junctionSnapsGeoJson: OUT_JUNCTION_SNAPS_GEOJSON,
    materializedBundlesGeoJson: OUT_MATERIALIZED_BUNDLES_GEOJSON,
    materializedBundleFanoutsGeoJson: OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON,
    materializedBundleSplitsGeoJson: OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON,
    materializedBundleDefectsGeoJson: OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON,
    bundlesGeoJson: OUT_BUNDLES_GEOJSON,
    bundleLanesGeoJson: OUT_BUNDLE_LANES_GEOJSON,
    bundleGapsGeoJson: OUT_BUNDLE_GAPS_GEOJSON,
    missingRouteLanesGeoJson: OUT_MISSING_ROUTE_LANES_GEOJSON,
    renderLaneContinuityJson: OUT_RENDER_LANE_CONTINUITY_JSON,
    anomaliesGeoJson: OUT_ANOMALIES_GEOJSON,
    anomaliesJson: OUT_ANOMALIES_JSON,
    routeComponentsJson: OUT_ROUTE_COMPONENTS_JSON,
  },
  parameters: {
    resampleIntervalM: RESAMPLE_INTERVAL_M,
    hausdorffMaxM: HAUSDORFF_MAX_M,
    overlapMinRatio: OVERLAP_MIN_RATIO,
    tangentMaxDiffDeg: TANGENT_MAX_DIFF_DEG,
    containmentAvgDistanceMaxM: CONTAINMENT_AVG_DISTANCE_MAX_M,
    containmentOverlapMinRatio: CONTAINMENT_OVERLAP_MIN_RATIO,
    gridCellM: GRID_CELL_M,
    junctionSnapMaxM: JUNCTION_SNAP_MAX_M,
    maxSegmentAnomalyM: MAX_SEGMENT_ANOMALY_M,
    sparseLongSliceM: SPARSE_LONG_SLICE_M,
    projectionAnomalyM: PROJECTION_ANOMALY_M,
    openDataMinFragmentLengthM: OPEN_DATA_MIN_FRAGMENT_LENGTH_M,
  },
});

applyVisualRepairPipelineStage({
  bundleArtifacts,
  canonicalGeoJsonPath: resolve(publicDir, "subway-network.canonical.geojson"),
  stationsGeoJsonPath: STATIONS_GEOJSON_PATH,
  branchesByRoute,
  stopsById,
  sameColorCollapseDistM: SAME_COLOR_COLLAPSE_DIST_M,
  smoothAngleThresholdDeg: SMOOTH_ANGLE_THRESHOLD_DEG,
  smoothIterations: SMOOTH_ITERATIONS,
  smoothRatio: SMOOTH_RATIO,
  smoothMaxFilletM: SMOOTH_MAX_FILLET_M,
  tightCurveTurnDeg: TIGHT_CURVE_TURN_DEG,
  tightCurveWindowM: TIGHT_CURVE_WINDOW_M,
  tightCurveIterations: TIGHT_CURVE_ITERATIONS,
  tightCurveLambda: TIGHT_CURVE_LAMBDA,
  sameColorSnapDistM: SAME_COLOR_SNAP_DIST_M,
  fanoutBlendM: FANOUT_BLEND_M,
  bridgeMinGapM: BRIDGE_MIN_GAP_M,
  bridgeMaxGapM: BRIDGE_MAX_GAP_M,
  bridgeSubsetConnectorMaxGapM: BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M,
  offRevenueMaxM: OFF_REVENUE_MAX_M,
});

writeVisualArtifactStage({
  generatedAt: new Date().toISOString(),
  openDataSourceName: OPEN_DATA_SOURCE_NAME,
  openDataSourceDatasetId: OPEN_DATA_SOURCE_DATASET_ID,
  perRouteStats,
  validationFailures,
  bundleArtifacts,
  candidatePath: OUT_VISUAL_CANDIDATE,
  finalPath: OUT_VISUAL_FINAL,
  parameters: {
    minTripsPerBranch: MIN_TRIPS_PER_BRANCH,
    resampleIntervalM: RESAMPLE_INTERVAL_M,
    hausdorffMaxM: HAUSDORFF_MAX_M,
    overlapMinRatio: OVERLAP_MIN_RATIO,
    tangentMaxDiffDeg: TANGENT_MAX_DIFF_DEG,
    containmentAvgDistanceMaxM: CONTAINMENT_AVG_DISTANCE_MAX_M,
    containmentOverlapMinRatio: CONTAINMENT_OVERLAP_MIN_RATIO,
  },
});

reportFinalTopologySummaryStage({
  topologyDoc,
  minTripsPerBranch: MIN_TRIPS_PER_BRANCH,
  droppedLowFreqBranches,
  stopsById,
});
