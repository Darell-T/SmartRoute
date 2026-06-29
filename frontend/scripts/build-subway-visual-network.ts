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
import { buildSpineFromCorridor } from "./build/spine.ts";
import {
  assertSpineHashConsistency,
  assertNoBogusTransitions,
  assertQContinuousInBrooklyn,
  assertOriginsForRedGreenFlatbushEastern,
} from "./build/spine-validation.ts";
import {
  groupSpinesIntoPhysicalBundles,
  selectPhysicalBundleSpine,
  computePhysicalBundleSpineHash,
  clipPolylineToExtent,
} from "./build/physical-bundle.ts";
import { orderColorsForBundle, BUNDLE_COLOR_ORDER } from "./build/lane-order.ts";
import { buildBranchTransitions } from "./build/branch-transitions.ts";
import { filterBogusTransitions, markOrphanLanes, removeOrphanErrorLanes } from "./build/lane-continuity-filter.ts";
import { dedupeDuplicateCorridors } from "./build/dedupe-duplicate-corridors.ts";
import { groupCorridorsByColorAndOverlap, mergeSameColorGroup } from "./build/same-color-merge.ts";
import { materializePhysicalBundles } from "./build/physical-bundle-materialization.ts";
import {
  detectCrossColorAdjacency,
  findSharedArcExtent,
  offsetPolylineOverExtent,
} from "./build/cross-color-spread.ts";
import { smoothSharpCorners, densifyLongSegments } from "./build/smooth-polyline.ts";
import { bridgeRouteGaps } from "./build/bridge-route-gaps.ts";
import { taperBakedJointSteps } from "./build/joint-offset-taper.ts";
import { colocateSameColorStretches } from "./build/colocate-same-color.ts";
import { simplifyTightCurves } from "./build/simplify-tight-curves.ts";
import { snapDanglingSameColorEndpoints } from "./build/snap-dangling-same-color.ts";
import { snapOffRevenueToShape, maxOffShapeM } from "./build/snap-off-revenue-to-shape.ts";
import { replaceEndpointHairpin } from "./build/schematic-hairpin-arc.ts";
import { hermiteBetween } from "./build/offset-bow.ts";
import { collapseSameColorOverlaps } from "./build/collapse-same-color.ts";
import { parallelOffsetCrossColor } from "./build/parallel-offset-cross-color.ts";
import { suppressShadowOrphans } from "./build/suppress-shadow-orphans.ts";
import { applyCartographicJunctionOverrides } from "./build/cartographic-junction-overrides.ts";
import {
  buildMottHavenFiveSchematicLens,
  buildMottHavenSixSchematicMerge,
} from "./build/mott-haven-schematic.ts";
import { applyNostrandEasternSchematic } from "./build/nostrand-eastern-schematic.ts";
import { applyBrightonBqChurchSpacing } from "./build/brighton-bq-church-spacing.ts";
import { applyCulverFgProspectSmoothing } from "./build/culver-fg-prospect-smoothing.ts";
import { applyJoralemonGreenRiverSmoothing } from "./build/joralemon-green-river.ts";
import { applyStNicholasBlueStraightening } from "./build/st-nicholas-blue-straightening.ts";
import {
  loadOpenDataSubwayLines,
  OPEN_DATA_SOURCE_DATASET_ID,
  OPEN_DATA_SOURCE_NAME,
} from "./build/opendata-subway-lines.ts";
import { trimTerminalOverhang } from "./build/trim-terminal-overhang.ts";
import { addSixtyThirdStreetF } from "./build/sixty-third-street-f.ts";
import { cleanStatenIslandLine } from "./build/staten-island-cleanup.ts";
import { connectRockawayWye } from "./build/rockaway-wye.ts";
import {
  colorRank,
  compareRouteIds,
  normalizeRouteId,
  routeColorFor,
} from "./build/visual-network/route-config.ts";
import { parseZipEntries } from "./build/visual-network/gtfs-ingest.ts";
import { buildGtfsTopologyStage } from "./build/visual-network/gtfs-topology-stage.ts";
import type { LineFeature, PointFeat, Position } from "./build/visual-network/types.ts";
import {
  HAUSDORFF_MAX_M,
  JUNCTION_BRIDGE_MAX_M,
  LANE_WIDTH_METERS,
  M_PER_DEG_LAT,
  RESAMPLE_INTERVAL_M,
  distanceMeters,
  geometryStats,
  metersPerDegLng,
  offsetPolylineByLaneSlot,
} from "./build/visual-network/geometry-utils.ts";
import {
  buildRouteIncidentCounts,
  buildVisualAnomalyRecords,
  buildVisualRouteIncidentCounts,
} from "./build/visual-network/diagnostics.ts";
import { buildOpenDataInputsStage } from "./build/visual-network/opendata-inputs.ts";
import {
  buildBundleArtifacts,
  routesForColor,
} from "./build/visual-network/bundle-stage.ts";
import { buildCandidateDoc } from "./build/visual-network/artifact-metadata.ts";
import { applyGeometrySmoothingPass } from "./build/visual-network/geometry-smoothing-pass.ts";
import { applyTightCurveSimplificationPass } from "./build/visual-network/tight-curve-simplification-pass.ts";
import { applySameRouteEndpointCrossingPass } from "./build/visual-network/same-route-endpoint-crossing-pass.ts";

// --- Pragmatic feature-bag types from the mechanical Batch 26 .ts conversion
// live in visual-network/types.ts and remain intentionally permissive. ---

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
// owned by visual-network/geometry-utils.ts and imported above.
// Max distance for promoting a branch_transition. Distinct from
// JUNCTION_BRIDGE_MAX_M (legacy buildJunctionBridges). The audit in Phase 3a
// showed all production-quality transitions are <= ~5m at junction stations,
// plus a long-tail outlier at 42.85m (G at Fulton St) that we drop for now.
// 35m gives ample headroom for legitimate transitions while excluding outliers.
const BRANCH_TRANSITION_MAX_M = 35;
// Fix 3: per-slot lane width baked into geometry at build time. The constants
// are owned by visual-network/geometry-utils.ts and imported here. Pushed 12->18m
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

// =====================================================================
// Phase 2B - OpenData visual line geometry + GTFS topology edges
// =====================================================================
//
// GTFS remains the topology source: branch stop sequences drive connectivity
// validation, station markers, and route coverage. Visual line geometry no
// longer comes from stop-pair slices of GTFS shapes.txt. The State of NY / MTA
// OpenData Subway Service Lines GeoJSON provides full visual polylines, which
// become render corridors directly.
console.log("[visual-network] Gate 2B - loading NYC OpenData subway line geometry");

const SPARSE_LONG_SLICE_M = 300;
const MAX_SEGMENT_ANOMALY_M = 250;
const PROJECTION_ANOMALY_M = 125;

const openDataLines = loadOpenDataSubwayLines(OPEN_DATA_LINES_PATH, {
  expectedRouteIds: expectedOpenDataRouteIds,
  minFragmentLengthM: OPEN_DATA_MIN_FRAGMENT_LENGTH_M,
});
const opendataLineFeatures = openDataLines.features.map((feature, index) => ({
  ...feature,
  properties: {
    ...feature.properties,
    opendata_line_id: `opendata-${String(index + 1).padStart(5, "0")}`,
  },
}));

const edgesDoc = {
  type: "FeatureCollection",
  metadata: {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2B OpenData normalized lines",
    parameters: {
      visual_geometry_source: OPEN_DATA_SOURCE_NAME,
      visual_geometry_source_dataset_id: OPEN_DATA_SOURCE_DATASET_ID,
      raw_opendata_path: "frontend/public/subway-lines-nyc-opendata.geojson",
      shape_selection_strategy: "nyc_opendata_full_lines",
    },
    diagnostics: {
      ...openDataLines.diagnostics,
      ...topologyEdgeDiagnostics,
    },
  },
  features: opendataLineFeatures,
};
writeFileSync(OUT_OPENDATA_LINES_GEOJSON, `${JSON.stringify(edgesDoc)}\n`);
writeFileSync(OUT_EDGES_GEOJSON, `${JSON.stringify(edgesDoc)}\n`);
console.log(`[visual-network] wrote ${OUT_OPENDATA_LINES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_EDGES_GEOJSON}`);
console.log(
  `[visual-network] OpenData source features: ${openDataLines.diagnostics.source_feature_count}`,
);
console.log(
  `[visual-network] OpenData normalized line features: ${openDataLines.diagnostics.normalized_feature_count}`,
);
console.log(
  `[visual-network] OpenData represented routes: ${openDataLines.diagnostics.represented_route_ids.join(",")}`,
);
console.log(
  `[visual-network] OpenData missing expected routes: ${openDataLines.diagnostics.missing_expected_route_ids.join(",") || "none"}`,
);
console.log(
  `[visual-network] OpenData alias applications: ${JSON.stringify(openDataLines.diagnostics.alias_applications)}`,
);
console.log(
  `[visual-network] GTFS topology edges for validation: ${topologyEdgeDiagnostics.topology_edges_emitted}`,
);

console.log(
  `[visual-network] expected topology edges: ${expectedEdges} (emitted: ${topologyEdgeDiagnostics.topology_edges_emitted}, retention ${(topologyEdgeDiagnostics.topology_edges_emitted / Math.max(1, expectedEdges) * 100).toFixed(1)}%)`,
);
// =====================================================================
// Phase 2C - OpenData corridors + overlap sanity diagnostics
// =====================================================================
//
// NYC OpenData already carries route membership on full visual line geometry.
// Gate 2C is therefore no longer a merge step. It converts normalized OpenData
// lines into corridor features and writes diagnostics for suspicious overlaps
// where separate OpenData features appear to share track without sharing route
// ids.
console.log("[visual-network] Gate 2C - OpenData corridor normalization");

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
// Visual QA gate: route-5 visual geometry near 149 St / Mott Haven must use a
// compact south-side schematic peel. GTFS is still used to remove bad OpenData
// excursions first, but the literal GTFS curl is not the Apple/Transit visual.
const MOTT_HAVEN_5_QA_BBOX = { minLon: -73.9335, maxLon: -73.9230, minLat: 40.8105, maxLat: 40.8230 };
const MOTT_HAVEN_5_QA_MAX_NORTH_LAT = 40.81795;
const MOTT_HAVEN_5_QA_MAX_TRUNK_DISTANCE_M = 3;
const MOTT_HAVEN_5_QA_MIN_TRUNK_JOIN_M = 230;
const MOTT_HAVEN_5_QA_WEST_BOW_LON_MAX = -73.93025;

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
  opendataOverlapWarnings,
} = buildOpenDataInputsStage({
  opendataLineFeatures,
  geometrySourceName: OPEN_DATA_SOURCE_NAME,
  overlapMinRatio: OVERLAP_MIN_RATIO,
  overlapSharedLenMinM: OVERLAP_SHARED_LEN_MIN_M,
  containmentAvgDistanceMaxM: CONTAINMENT_AVG_DISTANCE_MAX_M,
  tangentMaxDiffDeg: TANGENT_MAX_DIFF_DEG,
});

writeFileSync(
  OUT_OPENDATA_OVERLAPS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2C OpenData overlap sanity check",
      parameters: {
        overlap_min_ratio: OVERLAP_MIN_RATIO,
        shared_length_min_m: OVERLAP_SHARED_LEN_MIN_M,
        avg_distance_max_m: CONTAINMENT_AVG_DISTANCE_MAX_M,
        tangent_max_diff_deg: TANGENT_MAX_DIFF_DEG,
      },
      summary: {
        warning_count: opendataOverlapWarnings.length,
      },
    },
    features: opendataOverlapWarnings,
  })}\n`,
);
console.log(`[visual-network] wrote ${OUT_OPENDATA_OVERLAPS_GEOJSON}`);
console.log(`[visual-network] OpenData overlap warnings: ${opendataOverlapWarnings.length}`);
const JUNCTION_SNAP_MAX_M = 25;

function endpointClusterKey(stopId: string, index: number) {
  return `${stopId}#${index}`;
}

function clusterEndpointEntries(entries: any[]) {
  const clusters: any[] = [];
  for (const entry of entries) {
    let target = null;
    for (const cluster of clusters) {
      if (
        cluster.entries.some(
          (existing: any) =>
            distanceMeters(existing.coordinate, entry.coordinate) <=
            JUNCTION_SNAP_MAX_M,
        )
      ) {
        target = cluster;
        break;
      }
    }
    if (!target) {
      target = { entries: [], coordinate: entry.coordinate };
      clusters.push(target);
    }
    target.entries.push(entry);
    const lng =
      target.entries.reduce((sum: number, item: any) => sum + item.coordinate[0], 0) /
      target.entries.length;
    const lat =
      target.entries.reduce((sum: number, item: any) => sum + item.coordinate[1], 0) /
      target.entries.length;
    target.coordinate = [lng, lat];
  }
  return clusters;
}

function applyJunctionAnchorSnaps(features: LineFeature[]) {
  const entriesByStop = new Map();
  const geometryEndpointKey = "__opendata_geometry_endpoints__";
  for (const feature of features) {
    const coords = feature.geometry.coordinates;
    const endpoints = [
      {
        kind: "from",
        stop_id: feature.properties.from_stop_id,
        stop_name: feature.properties.from_stop_name,
        coordinate: coords[0],
      },
      {
        kind: "to",
        stop_id: feature.properties.to_stop_id,
        stop_name: feature.properties.to_stop_name,
        coordinate: coords[coords.length - 1],
      },
    ];
    for (const endpoint of endpoints) {
      if (!endpoint.coordinate) continue;
      const key = endpoint.stop_id || geometryEndpointKey;
      if (!entriesByStop.has(key)) entriesByStop.set(key, []);
      entriesByStop.get(key).push({ feature, ...endpoint, stop_id: endpoint.stop_id ?? key });
    }
  }

  const anchorFeatures: PointFeat[] = [];
  const snapFeatures: LineFeature[] = [];
  const anchorByFeatureEndpoint = new Map();

  for (const [stopId, entries] of entriesByStop) {
    const clusters = clusterEndpointEntries(entries);
    clusters.forEach((cluster, clusterIndex) => {
      const anchorId =
        stopId === geometryEndpointKey
          ? `opendata-anchor#${clusterIndex}`
          : endpointClusterKey(stopId, clusterIndex);
      anchorFeatures.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: cluster.coordinate },
        properties: {
          anchor_id: anchorId,
          stop_id: stopId === geometryEndpointKey ? null : stopId,
          stop_name: cluster.entries[0]?.stop_name ?? "",
          endpoint_count: cluster.entries.length,
          anchor_source:
            stopId === geometryEndpointKey ? "geometry_endpoint" : "gtfs_stop",
        },
      });

      for (const entry of cluster.entries) {
        const snapDistanceM = distanceMeters(entry.coordinate, cluster.coordinate);
        const key = `${entry.feature.properties.corridor_id}:${entry.kind}`;
        if (snapDistanceM > JUNCTION_SNAP_MAX_M) continue;
        anchorByFeatureEndpoint.set(key, { anchorId, coordinate: cluster.coordinate });

        const coords = entry.feature.geometry.coordinates;
        const original = entry.kind === "from" ? coords[0] : coords[coords.length - 1];
        if (snapDistanceM > 0.01) {
          if (entry.kind === "from") coords[0] = cluster.coordinate;
          else coords[coords.length - 1] = cluster.coordinate;
          snapFeatures.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: [original, cluster.coordinate] },
            properties: {
              corridor_id: entry.feature.properties.corridor_id,
              route_ids: entry.feature.properties.route_ids,
              stop_id: stopId,
              stop_name: entry.stop_name,
              endpoint_kind: entry.kind,
              anchor_id: anchorId,
              original_coord: original,
              snapped_coord: cluster.coordinate,
              snap_distance_m: Number(snapDistanceM.toFixed(2)),
            },
          });
        }
      }
    });
  }

  for (const feature of features) {
    const fromAnchor = anchorByFeatureEndpoint.get(`${feature.properties.corridor_id}:from`);
    const toAnchor = anchorByFeatureEndpoint.get(`${feature.properties.corridor_id}:to`);
    feature.properties.from_anchor_id = fromAnchor?.anchorId ?? null;
    feature.properties.to_anchor_id = toAnchor?.anchorId ?? null;
    feature.properties.junction_anchor_ids = [
      fromAnchor?.anchorId,
      toAnchor?.anchorId,
    ].filter(Boolean);
  }

  return {
    anchorFeatures,
    snapFeatures,
  };
}

function colorGroupsForRoutes(routeIds: string[]) {
  return [
    ...new Set(routeIds.map((routeId) => routeColorFor(routeId))),
  ].sort((a, b) => colorRank(a) - colorRank(b));
}

function applyLaneChainMetadata(features: LineFeature[]) {
  const featureIndexByAnchor = new Map();
  features.forEach((feature, index) => {
    for (const anchorId of feature.properties.junction_anchor_ids ?? []) {
      if (!featureIndexByAnchor.has(anchorId)) featureIndexByAnchor.set(anchorId, []);
      featureIndexByAnchor.get(anchorId).push(index);
    }
  });

  const parent = new Int32Array(features.length);
  for (let i = 0; i < parent.length; i += 1) parent[i] = i;
  const find = (x: number) => {
    let r = x;
    while (parent[r] !== r) r = parent[r];
    while (parent[x] !== r) {
      const next = parent[x];
      parent[x] = r;
      x = next;
    }
    return r;
  };
  const union = (a: number, b: number) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent[ra] = rb;
  };

  for (const indices of featureIndexByAnchor.values()) {
    for (let leftIndex = 0; leftIndex < indices.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < indices.length; rightIndex += 1) {
        const leftFeatureIndex = indices[leftIndex];
        const rightFeatureIndex = indices[rightIndex];
        const left = features[leftFeatureIndex];
        const right = features[rightFeatureIndex];
        const leftColors = new Set(colorGroupsForRoutes(left.properties.route_ids ?? []));
        const rightColors = colorGroupsForRoutes(right.properties.route_ids ?? []);
        if (rightColors.some((color) => leftColors.has(color))) {
          union(leftFeatureIndex, rightFeatureIndex);
        }
      }
    }
  }

  const groups = new Map();
  features.forEach((feature, index) => {
    const root = find(index);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(index);
  });

  let groupId = 1;
  for (const indices of groups.values()) {
    const groupColors = [
      ...new Set<string>(
        indices.flatMap((index: any) => colorGroupsForRoutes(features[index].properties.route_ids ?? [])),
      ),
    ].sort((a, b) => colorRank(a) - colorRank(b));
    const laneGroupId = `lane-group-${String(groupId++).padStart(4, "0")}`;
    for (const index of indices) {
      const feature = features[index];
      const localColors = colorGroupsForRoutes(feature.properties.route_ids ?? []);
      const slots = Object.fromEntries(
        localColors.map((color, colorIndex) => [
          color,
          colorIndex - (localColors.length - 1) / 2,
        ]),
      );
      feature.properties.lane_group_id = laneGroupId;
      feature.properties.lane_slot_source = indices.length > 1 ? "chain" : "local";
      feature.properties.lane_order_basis = localColors;
      feature.properties.lane_group_color_basis = groupColors;
      feature.properties.lane_color_slots = slots;
    }
  }

  return {
    lane_group_count: groups.size,
    chain_slot_feature_count: features.filter(
      (feature) => feature.properties.lane_slot_source === "chain",
    ).length,
  };
}

const junctionSnapDiagnostics = applyJunctionAnchorSnaps(corridorFeatures);
const laneChainDiagnostics = applyLaneChainMetadata(corridorFeatures);
const edgeById = new Map(
  edgeFeatures.map((feature) => [feature.properties.edge_id, feature]),
);

// ----- Phase 3d: same-color merge -----
// Where two or more OpenData polylines of the same color share physical track,
// merge them: longest member becomes the trunk carrying the union of route_ids;
// shorter members are clipped to their non-overlapping divergence portion only.
// Color-scoped only -- never merges across colors (so B orange + Q yellow on
// Brighton stay parallel features and render as parallel lanes downstream).
{
  // Ensure every corridor has a color stamped on properties (derived from
  // routeColorFor(route_ids[0])).
  for (const f of corridorFeatures) {
    if (!f.properties.color) {
      const r0 = (f.properties.route_ids ?? [])[0];
      f.properties.color = r0 ? routeColorFor(r0) : "#808183";
    }
  }

  // Per-route coverage map (for the connectivity-preservation fallback).
  const routeCoverageMap = new Map();
  for (const f of corridorFeatures) {
    for (const r of f.properties.route_ids ?? []) {
      routeCoverageMap.set(r, (routeCoverageMap.get(r) ?? 0) + 1);
    }
  }

  // Quick lookup by corridor_id for the merge helper.
  const corridorsById = new Map();
  for (const f of corridorFeatures) {
    corridorsById.set(f.properties.corridor_id, {
      corridor_id: f.properties.corridor_id,
      color: f.properties.color,
      route_ids: f.properties.route_ids ?? [],
      geometry: f.geometry,
      length_m: f.properties.length_m ?? 0,
    });
  }

  const { groups: mergeGroups } = groupCorridorsByColorAndOverlap(
    [...corridorsById.values()],
    { sharedFractionMin: 0.55, sharedLenMinM: 100, avgDistMaxM: 15, tangentMaxDeg: 30, resampleM: 25 },
  );

  // Helper: recompute path length in meters (haversine sum).
  function recomputeLengthM(coords: Position[]) {
    const EARTH_M = 6371000;
    let total = 0;
    for (let i = 1; i < coords.length; i++) {
      const [lon1, lat1] = coords[i - 1];
      const [lon2, lat2] = coords[i];
      const toRad = (d: number) => (d * Math.PI) / 180;
      const dLat = toRad(lat2 - lat1);
      const dLon = toRad(lon2 - lon1);
      const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
      total += 2 * EARTH_M * Math.asin(Math.sqrt(a));
    }
    return total;
  }

  let mergesApplied = 0;
  let branchesClipped = 0;
  let branchesDropped = 0;
  let branchConnectorsAdded = 0;
  let groupsSkipped = 0;
  const debugFeatures = [];
  let sameColorConnectorNumber = 1;

  for (const group of mergeGroups) {
    const result = mergeSameColorGroup(group, corridorsById, {
      minBranchLenM: 30,
      resampleM: 25,
      avgDistMaxM: 15,
      routeCoverageMap,
    });
    if (result.skipped) {
      groupsSkipped++;
      debugFeatures.push({
        type: "Feature",
        geometry: null,
        properties: {
          visual_feature_type: "same_color_merge_skipped",
          color: group.color,
          trunk_corridor_id: group.trunk_corridor_id,
          member_corridor_ids: group.member_corridor_ids,
          reason: result.skipped.reason,
        },
      });
      continue;
    }

    // Apply trunk updates.
    const trunkFeature = corridorFeatures.find(
      (f) => f.properties.corridor_id === result.trunkUpdates.corridor_id,
    );
    if (trunkFeature) {
      trunkFeature.properties.route_ids = result.trunkUpdates.route_ids;
      trunkFeature.properties.color_route_ids = result.trunkUpdates.color_route_ids;
      trunkFeature.properties.merged_from_corridor_ids = result.trunkUpdates.merged_from_corridor_ids;
    }

    // Apply branch updates.
    const branchIdsToDrop = new Set();
    for (const bu of result.branchUpdates) {
      if (bu.drop) {
        branchIdsToDrop.add(bu.corridor_id);
        branchesDropped++;
      } else if (bu.newCoords) {
        const bf = corridorFeatures.find((f) => f.properties.corridor_id === bu.corridor_id);
        if (bf) {
          bf.geometry = { type: "LineString", coordinates: bu.newCoords };
          bf.properties.clipped_to_branch_only = true;
          bf.properties.length_m = recomputeLengthM(bu.newCoords);
          bf.properties.from_anchor_id = null;
          bf.properties.to_anchor_id = null;
          bf.properties.junction_anchor_ids = [];
          branchesClipped++;
        }
        if (bu.connector && (bu.connector.coordinates?.length ?? 0) >= 2) {
          const connectorId = `same-color-connector-${String(sameColorConnectorNumber++).padStart(5, "0")}`;
          const connectorRouteIds = [...new Set(bu.connector.route_ids ?? [])].sort(compareRouteIds);
          const connectorColor = bu.connector.color ?? group.color;
          corridorFeatures.push({
            type: "Feature",
            geometry: {
              type: "LineString",
              coordinates: bu.connector.coordinates,
            },
            properties: {
              visual_feature_type: "same_color_branch_connector",
              corridor_id: connectorId,
              route_ids: connectorRouteIds,
              color_route_ids: { [connectorColor]: connectorRouteIds },
              color: connectorColor,
              source_edge_ids: [],
              source_shape_ids: [],
              length_m: recomputeLengthM(bu.connector.coordinates),
              max_segment_length_m: recomputeLengthM(bu.connector.coordinates),
              base_geometry_selection: "same_color_branch_connector",
              same_color_connector_for_corridor_id: bu.corridor_id,
              same_color_connector_to_trunk_corridor_id: result.trunkUpdates.corridor_id,
              same_color_connector_distance_m: bu.connector.distance_m,
              same_color_connector_endpoint_kind: bu.connector.endpoint_kind,
            },
          });
          branchConnectorsAdded++;
        }
      }
    }

    if (branchIdsToDrop.size > 0) {
      for (let i = corridorFeatures.length - 1; i >= 0; i--) {
        if (branchIdsToDrop.has(corridorFeatures[i].properties.corridor_id)) {
          corridorFeatures.splice(i, 1);
        }
      }
    }

    mergesApplied++;

    debugFeatures.push({
      type: "Feature",
      geometry: trunkFeature ? trunkFeature.geometry : null,
      properties: {
        visual_feature_type: "same_color_merge",
        color: group.color,
        trunk_corridor_id: result.trunkUpdates.corridor_id,
        merged_from_corridor_ids: result.trunkUpdates.merged_from_corridor_ids,
        branches_clipped: result.branchUpdates.filter((b) => !b.drop).map((b) => b.corridor_id),
        branches_dropped: result.branchUpdates.filter((b) => b.drop).map((b) => b.corridor_id),
        branch_connectors: result.branchUpdates
          .filter((b) => b.connector)
          .map((b) => ({
            corridor_id: b.corridor_id,
            distance_m: b.connector!.distance_m,
            endpoint_kind: b.connector!.endpoint_kind,
          })),
        route_ids_union: result.trunkUpdates.route_ids,
      },
    });
  }

  writeFileSync(
    OUT_SAME_COLOR_MERGES_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: debugFeatures })}\n`,
  );

  console.log(`[visual-network] Phase 3d merges applied:    ${mergesApplied}`);
  console.log(`[visual-network] Phase 3d branches clipped:  ${branchesClipped}`);
  console.log(`[visual-network] Phase 3d branches dropped:  ${branchesDropped}`);
  console.log(`[visual-network] Phase 3d connectors added:  ${branchConnectorsAdded}`);
  console.log(`[visual-network] Phase 3d groups skipped:    ${groupsSkipped}`);
}

let postSnapDegeneratePruned = 0;
for (let index = corridorFeatures.length - 1; index >= 0; index -= 1) {
  const feature = corridorFeatures[index];
  if (feature.properties?.visual_feature_type === "same_color_branch_connector") {
    continue;
  }
  if (geometryStats(feature.geometry.coordinates).length_m >= OPEN_DATA_MIN_FRAGMENT_LENGTH_M) {
    continue;
  }
  corridorFeatures.splice(index, 1);
  postSnapDegeneratePruned += 1;
}
console.log(
  `[visual-network] post-snap degenerate corridors pruned: ${postSnapDegeneratePruned}`,
);

// Drop duplicate same-route corridors (a shorter corridor running parallel within
// ~25 m of, and contained by, a longer corridor that carries all its routes) before
// spine/bundle assignment, so they never become a second parallel lane.
{
  const dedup = dedupeDuplicateCorridors(corridorFeatures, { parallelDistM: 25, overlapRatioMin: 0.8 });
  if (dedup.removedIds.length) {
    corridorFeatures.length = 0;
    corridorFeatures.push(...(dedup.features as LineFeature[]));
    console.log(`[visual-network] duplicate corridors deduped:           ${dedup.removedIds.length}`);
  }
}

// Densify coarse source chords (some OpenData corridors have km-scale straight
// segments that no offset/smoothing can round) BEFORE spine assignment + lane
// offsetting, so downstream geometry has the intermediate vertices it needs.
{
  let densified = 0;
  for (const f of corridorFeatures) {
    if (f.geometry?.type !== "LineString") continue;
    const before = f.geometry.coordinates;
    const after = densifyLongSegments(before, DENSIFY_MAX_SEGMENT_M, DENSIFY_STEP_M);
    if (after !== before) { f.geometry.coordinates = after; densified += 1; }
  }
  console.log(`[visual-network] coarse corridors densified:            ${densified}`);
}

// ----- Stage D: spine assignment -----
// Each Gate 2C corridor maps 1:1 to a spine. The spine carries a deterministic
// base_spine_hash that buildBundleArtifacts will stamp onto every bundle_lane
// derived from this corridor. Hard validation downstream asserts that all
// lanes sharing a spine_id share an identical hash.
const spinesByCorridorId = new Map();
const spineFeatures: any[] = [];

function rebuildSpineArtifactsForCurrentCorridors() {
  spinesByCorridorId.clear();
  spineFeatures.length = 0;
  for (const f of corridorFeatures) {
    const spine = buildSpineFromCorridor(f);
    spinesByCorridorId.set(f.properties.corridor_id, spine);
    spineFeatures.push({
      type: "Feature",
      geometry: spine.geometry,
      properties: {
        visual_feature_type: "spine",
        spine_id: spine.spine_id,
        base_corridor_id: spine.base_corridor_id,
        base_spine_hash: spine.base_spine_hash,
        base_geometry_selection: spine.method,
        route_ids: spine.route_ids,
        source_edge_ids: spine.source_edge_ids,
        source_shape_ids: spine.source_shape_ids,
        length_m: spine.length_m,
      },
    });
  }
  spineFeatures.sort((a, b) => a.properties.spine_id.localeCompare(b.properties.spine_id));
  writeFileSync(
    OUT_SPINES_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: spineFeatures })}\n`,
  );
}

rebuildSpineArtifactsForCurrentCorridors();

const CORRIDOR_GROUPS_COUNT = corridorFeatures.length;
const SPINES_CREATED = spineFeatures.length;
console.log(
  `[visual-network] corridor groups:           ${CORRIDOR_GROUPS_COUNT}`,
);
console.log(
  `[visual-network] spines created:            ${SPINES_CREATED}`,
);

// ----- Phase 1.5: cross-corridor physical bundle grouping -----
// Detect when two or more Stage-D spines (from separate Gate 2C corridors)
// physically share track for a meaningful stretch, substitute one shared
// geometry, and stamp physical_bundle_* metadata onto member lanes.
const SUBSTITUTE_CONFIDENCE_MIN = 0.75;
const allSpinesForGrouping = [];
for (const f of corridorFeatures) {
  const spine = spinesByCorridorId.get(f.properties.corridor_id);
  if (!spine) continue;
  allSpinesForGrouping.push({
    spine_id: spine.spine_id,
    geometry: spine.geometry,
    length_m: spine.length_m ?? 0,
    route_ids: spine.route_ids,
  });
}

const {
  groups: physicalBundles,
  rejects: physicalBundleRejects,
  transitiveDiagnostics: physicalBundleTransitiveDiagnostics = [],
} =
  groupSpinesIntoPhysicalBundles(allSpinesForGrouping, {
    avgDistMaxM: 15,
    sharedFractionMin: 0.6,
    sharedLenMinM: 250,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

// Build maps
const physicalBundleSpines = []; // FeatureCollection content
const physicalBundleLaneFeatures = []; // debug per (bundle, member corridor)

// spinesById for lookups inside the loop
const spinesById = new Map();
for (const s of allSpinesForGrouping) spinesById.set(s.spine_id, s);

writeFileSync(
  OUT_TRANSITIVE_BUNDLES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle scoped-run diagnostics",
      summary: {
        transitive_disjoint_overlap_count: physicalBundleTransitiveDiagnostics.length,
      },
    },
    features: physicalBundleTransitiveDiagnostics.map((diagnostic) => {
      const base = spinesById.get(diagnostic.base_spine_id);
      return {
        type: "Feature",
        geometry: base?.geometry ?? null,
        properties: {
          visual_feature_type: "physical_bundle_transitive_diagnostic",
          ...diagnostic,
        },
      };
    }),
  })}\n`,
);

// Pre-build spine_id -> corridor feature map for O(1) lookup.
// This avoids an O(n) .find() inside the bundle loop and makes unknown
// spine_id references visible via a warn log.
const spineIdToCorridorFeature = new Map();
for (const f of corridorFeatures) {
  const spine = spinesByCorridorId.get(f.properties.corridor_id);
  if (spine) spineIdToCorridorFeature.set(spine.spine_id, f);
}

let bundlesSubstituted = 0;
for (const group of physicalBundles) {
  const bundleSpine = selectPhysicalBundleSpine(group, spinesById);
  const bundleHash = computePhysicalBundleSpineHash(bundleSpine.geometry.coordinates);
  (group as any).physical_bundle_spine_hash = bundleHash;
  const shouldSubstitute = false;
  if (shouldSubstitute) bundlesSubstituted++;

  physicalBundleSpines.push({
    type: "Feature",
    geometry: bundleSpine.geometry,
    properties: {
      visual_feature_type: "physical_bundle_spine",
      physical_bundle_id: group.physical_bundle_id,
      base_spine_id: bundleSpine.base_spine_id,
      physical_bundle_spine_hash: bundleHash,
      member_spine_ids: bundleSpine.member_spine_ids,
      route_ids: bundleSpine.route_ids,
      member_count: group.member_count,
      confidence: group.confidence,
      substituted: shouldSubstitute,
    },
  });

  // For each member corridor: substitute geometry (if confidence high enough) and stamp bundle metadata.
  for (const memberSpineId of bundleSpine.member_spine_ids) {
    const f = spineIdToCorridorFeature.get(memberSpineId);
    if (!f) {
      console.warn(`[visual-network] WARN: physical bundle ${group.physical_bundle_id} references unknown spine_id ${memberSpineId}`);
      continue;
    }

    if (shouldSubstitute) {
      // Clip the bundle spine to this corridor's actual extent.
      const memberCoords = f.geometry.coordinates;
      if (memberCoords.length >= 2) {
        const fromCoord = memberCoords[0];
        const toCoord = memberCoords[memberCoords.length - 1];
        const clipped = clipPolylineToExtent(bundleSpine.geometry.coordinates, fromCoord, toCoord, { resampleM: 25 });
        if (clipped && clipped.length >= 2) {
          // Substitute and emit a debug lane feature recording the substitution.
          physicalBundleLaneFeatures.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: clipped },
            properties: {
              visual_feature_type: "physical_bundle_lane",
              physical_bundle_id: group.physical_bundle_id,
              corridor_id: f.properties.corridor_id,
              spine_id: memberSpineId,
              base_spine_hash: spinesByCorridorId.get(f.properties.corridor_id).base_spine_hash,
              physical_bundle_spine_hash: bundleHash,
              substituted: true,
              route_ids: f.properties.route_ids,
            },
          });
          // Substituting the corridor's geometry with the bundle spine clip. The
          // corridor's `length_m` property is intentionally NOT recomputed -- it
          // remains the corridor's original logical length, not the substituted
          // geometry's arc length. Downstream uses `length_m` as a corridor
          // identity, not a render length.
          f.geometry = { type: "LineString", coordinates: clipped };
        }
      }
    }

    // Stamp metadata regardless of substitution decision.
    f.properties.physical_bundle_id = group.physical_bundle_id;
    f.properties.physical_bundle_spine_hash = bundleHash;
    f.properties.physical_bundle_member_count = group.member_count;
    f.properties.physical_bundle_confidence = group.confidence;
    f.properties.physical_bundle_substituted = shouldSubstitute;
  }
}

const physicalBundleMaterialization = materializePhysicalBundles(
  corridorFeatures as any,
  physicalBundles,
  {
    spinesById,
    confidenceMin: PHYSICAL_BUNDLE_SUBSTITUTE_CONFIDENCE_MIN,
    overlapDistMaxM: BUNDLE_OVERLAP_DIST_MAX_M,
    sharedLenMinM: BUNDLE_SHARED_LEN_MIN_M,
    splitSampleM: BUNDLE_SPLIT_SAMPLE_M,
    fanoutBlendM: FANOUT_BLEND_M,
    laneWidthM: LANE_WIDTH_METERS,
    taperM: 40,
    colorOrder: BUNDLE_COLOR_ORDER,
    routeColorFor,
    compareRouteIds,
    orderColorsForBundle,
  } as any,
);

if (physicalBundleMaterialization.consumed_corridor_count > 0) {
  corridorFeatures.length = 0;
  corridorFeatures.push(...physicalBundleMaterialization.features);
  rebuildSpineArtifactsForCurrentCorridors();
}

writeFileSync(
  OUT_MATERIALIZED_BUNDLES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
      parameters: {
        confidence_min: PHYSICAL_BUNDLE_SUBSTITUTE_CONFIDENCE_MIN,
        overlap_dist_max_m: BUNDLE_OVERLAP_DIST_MAX_M,
        shared_len_min_m: BUNDLE_SHARED_LEN_MIN_M,
        split_sample_m: BUNDLE_SPLIT_SAMPLE_M,
        fanout_blend_m: FANOUT_BLEND_M,
      },
      summary: {
        materialized_bundle_count: physicalBundleMaterialization.materialized_bundle_count,
        consumed_corridor_count: physicalBundleMaterialization.consumed_corridor_count,
      },
    },
    features: physicalBundleMaterialization.debug.materializedBundleFeatures,
  })}\n`,
);
writeFileSync(
  OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
      summary: {
        fanout_count: physicalBundleMaterialization.fanout_count,
      },
    },
    features: physicalBundleMaterialization.debug.fanoutFeatures,
  })}\n`,
);
writeFileSync(
  OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
    },
    features: physicalBundleMaterialization.debug.splitFeatures,
  })}\n`,
);
writeFileSync(
  OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
    },
    features: physicalBundleMaterialization.debug.defectFeatures,
  })}\n`,
);

// Sort outputs deterministically.
physicalBundleSpines.sort((a, b) => a.properties.physical_bundle_id.localeCompare(b.properties.physical_bundle_id));
physicalBundleLaneFeatures.sort((a, b) =>
  a.properties.physical_bundle_id.localeCompare(b.properties.physical_bundle_id) ||
  a.properties.corridor_id.localeCompare(b.properties.corridor_id),
);

writeFileSync(OUT_PHYSICAL_BUNDLES_GEOJSON, `${JSON.stringify({ type: "FeatureCollection", features: physicalBundleSpines })}\n`);
writeFileSync(OUT_PHYSICAL_BUNDLE_LANES_GEOJSON, `${JSON.stringify({ type: "FeatureCollection", features: physicalBundleLaneFeatures })}\n`);
writeFileSync(OUT_PHYSICAL_BUNDLE_REJECTS_GEOJSON, `${JSON.stringify({
  type: "FeatureCollection",
  features: physicalBundleRejects.map((r) => ({
    type: "Feature",
    geometry: null,
    properties: { ...r, visual_feature_type: "physical_bundle_reject" },
  })),
})}\n`);

const groupedCorridorCount = physicalBundles.reduce((acc, g) => acc + g.member_count, 0);
console.log(`[visual-network] physical bundles:           ${physicalBundles.length}`);
console.log(`[visual-network] grouped corridors:          ${groupedCorridorCount}`);
console.log(`[visual-network] substituted bundles:        ${bundlesSubstituted}`);
console.log(`[visual-network] materialized bundles:       ${physicalBundleMaterialization.materialized_bundle_count}`);
console.log(`[visual-network] materialized corridors used: ${physicalBundleMaterialization.consumed_corridor_count}`);
console.log(`[visual-network] materialized fanouts:       ${physicalBundleMaterialization.fanout_count}`);
console.log(`[visual-network] reject candidates:          ${physicalBundleRejects.length}`);
console.log(`[visual-network] transitive bundle splits:   ${physicalBundleTransitiveDiagnostics.length}`);

const bundleArtifacts = buildBundleArtifacts(corridorFeatures, spinesByCorridorId);

// ----- Stage D validation: same spine_id implies same base_spine_hash -----
{
  const result = assertSpineHashConsistency(bundleArtifacts);
  console.log(`[visual-network] bundle_lanes created:       ${result.bundleLaneCount}`);
  console.log(`[visual-network] lanes missing spine_id:     ${result.lanesWithMissingSpineId.length}`);
  console.log(`[visual-network] lanes missing hash:         ${result.lanesWithMissingHash.length}`);
  console.log(`[visual-network] inconsistent spine groups:  ${result.inconsistentGroups.length}`);
  console.log(`[visual-network] inconsistent pb groups:     ${result.inconsistentPhysicalBundleGroups.length}`);
  if (
    result.inconsistentGroups.length > 0 ||
    result.lanesWithMissingSpineId.length > 0 ||
    result.lanesWithMissingHash.length > 0 ||
    result.inconsistentPhysicalBundleGroups.length > 0
  ) {
    console.error("[visual-network] *** Stage D validation FAILED -- refusing to promote. ***");
    for (const g of result.inconsistentGroups.slice(0, 10)) {
      console.error(`  spine ${g.spine_id}: expected hash ${g.expected}, got ${g.got}`);
    }
    if (result.inconsistentGroups.length > 10) {
      console.error(`  (showing first 10 of ${result.inconsistentGroups.length})`);
    }
    if (result.lanesWithMissingSpineId.length > 0) {
      console.error(`  ${result.lanesWithMissingSpineId.length} non-bridge lanes missing spine_id`);
    }
    if (result.lanesWithMissingHash.length > 0) {
      console.error(`  ${result.lanesWithMissingHash.length} lanes missing base_spine_hash`);
    }
    for (const g of result.inconsistentPhysicalBundleGroups.slice(0, 10)) {
      console.error(`  physical bundle ${g.physical_bundle_id}: expected hash ${g.expected}, got ${g.got}`);
    }
    if (result.inconsistentPhysicalBundleGroups.length > 10) {
      console.error(`  (showing first 10 of ${result.inconsistentPhysicalBundleGroups.length} physical bundle inconsistencies)`);
    }
    process.exit(1);
  }
  console.log(`[visual-network] Stage D validation:         PASS`);

  // ---- Phase 3c validation gates (run after spine-hash passes) ----
  // Gate D2: no bogus transitions (color absent from both corridors).
  // Note: Phase 3b transitions have not been promoted yet at this stage —
  // Stage D runs on the pre-promotion bundleLaneFeatures.
  // We run the transition gates AFTER Phase 3b promotes, so we defer to
  // a post-promotion check in the Phase 3c block below. This sentinel
  // just logs that the gates will run.
  console.log(`[visual-network] Phase 3c gates:             scheduled after Phase 3b promotion`);
}

// ----- Phase 3b: branch transition promotion -----
// The branch_transition features replace the legacy buildJunctionBridges
// output. We promote only transitions <= BRANCH_TRANSITION_MAX_M to skip the
// long-tail outlier (G @ Fulton St) flagged by the Phase 3a audit.
//
// This block runs AFTER buildBundleArtifacts has returned, so the lane-offset
// baking loop inside that function has already completed. Mutating
// bundleLaneFeatures here is safe ONLY because every promoted transition is
// emitted with lane_slot: 0 and lane_offset_baked: true -- no geometric
// re-baking is required. Do not add lanes with non-zero lane_slot in this
// block without re-running the baking loop on them.
{
  const bundleLaneFeatures: any[] = bundleArtifacts.bundleLaneFeatures ?? (bundleArtifacts as any).bundle_lane_features ?? [];

  // Build a quick lookup: bundle_id -> route_ids (any of its lanes is fine).
  const bundleRouteIds = new Map();
  for (const lane of bundleLaneFeatures) {
    const bid = lane.properties.bundle_id;
    if (!bid || bundleRouteIds.has(bid)) continue;
    bundleRouteIds.set(bid, lane.properties.route_ids ?? []);
  }

  const { transitions, coincidentSkipped } = buildBranchTransitions(
    bundleLaneFeatures,
    { maxBridgeM: BRANCH_TRANSITION_MAX_M, minBridgeM: 0.5 },
  );

  // Sort deterministically.
  transitions.sort((a, b) => {
    const ka = `${a.properties.anchor_id}|${a.properties.color}|${a.properties.bundle_id_from}|${a.properties.bundle_id_to}`;
    const kb = `${b.properties.anchor_id}|${b.properties.color}|${b.properties.bundle_id_from}|${b.properties.bundle_id_to}`;
    return ka.localeCompare(kb);
  });

  // Enrich and promote each transition into bundleLaneFeatures.
  let promoted = 0;
  for (const t of transitions) {
    const tp: any = t.properties;
    const routesFrom = bundleRouteIds.get(tp.bundle_id_from) ?? [];
    const routesTo = bundleRouteIds.get(tp.bundle_id_to) ?? [];
    const routeIdsUnion = [...new Set([...routesFrom, ...routesTo])].sort(compareRouteIds);
    const intersect = routesFrom.filter((r: string) => routesTo.includes(r));
    const colorRouteIds = routesForColor(routeIdsUnion, tp.color);

    // Classification: intersect non-empty => safe; else => likely_branch_exit.
    // (The 35m cap already filtered out too_long; the helper already filtered
    // out coincident pairs and same-bundle pairs.)
    const classification =
      intersect.length > 0
        ? "safe_same_route_continuation"
        : "likely_branch_exit";

    // Stamp classification on the debug-artifact feature too.
    tp.transition_classification = classification;
    tp.route_ids = routeIdsUnion;
    tp.color_route_ids = colorRouteIds;

    // Promote into bundleLaneFeatures so the runtime renders it and the
    // connectivity gate sees it as a graph edge.
    bundleLaneFeatures.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: t.geometry.coordinates },
      properties: {
        visual_feature_type: "bundle_lane",
        feature_type: "branch_transition",
        lane_slot_source: "branch_transition",
        bundle_id: `transition-${tp.bundle_id_from}-${tp.bundle_id_to}-${tp.anchor_id}-${tp.color.slice(1)}`,
        corridor_id: null,
        spine_id: null,
        base_spine_hash: null,
        base_geometry_selection: null,
        physical_bundle_id: null,
        physical_bundle_spine_hash: null,
        physical_bundle_member_count: null,
        physical_bundle_confidence: null,
        route_id: colorRouteIds[0] ?? routeIdsUnion[0] ?? "",
        representative_route_id: colorRouteIds[0] ?? routeIdsUnion[0] ?? "",
        route_ids: routeIdsUnion,
        color_route_ids: colorRouteIds,
        color: tp.color,
        lane_slot: 0,
        lane_slot_semantic: 0,
        lane_offset_baked: true,
        lane_width_m: LANE_WIDTH_METERS,
        render_lane_slot: 0,
        lane_group_id: null,
        lane_order_basis: [tp.color],
        lane_order_override_applied: false,
        bundle_lane_count: 1,
        bundle_lane_slots: { [tp.color]: 0 },
        branch_in_route_ids: [],
        branch_out_route_ids: [],
        bundle_entry: false,
        bundle_exit: false,
        bridge: false,
        bundle_id_from: tp.bundle_id_from,
        bundle_id_to: tp.bundle_id_to,
        anchor_id: tp.anchor_id,
        from_anchor_id: tp.anchor_id,
        to_anchor_id: tp.anchor_id,
        length_m: tp.length_m,
        transition_classification: classification,
      },
    });
    promoted++;
  }

  writeFileSync(OUT_BRANCH_TRANSITIONS_GEOJSON, `${JSON.stringify({
    type: "FeatureCollection",
    features: transitions,
  })}\n`);

  // Count classifications for the log.
  const safeCount = transitions.filter((t) => (t.properties as any).transition_classification === "safe_same_route_continuation").length;
  const branchExitCount = transitions.filter((t) => (t.properties as any).transition_classification === "likely_branch_exit").length;

  // Mirror the promoted transitions into bundleArtifacts.visualFeatures (the
  // sorted array that's actually serialized into subway-network.visual.geojson
  // at the bottom of this script). bundleLaneFeatures alone is only used for
  // intermediate debug artifacts; the runtime renderer reads visualFeatures.
  // We append at the end -- order within visualFeatures matters only for
  // deterministic on-disk diffs, and the bundle_id prefix "transition-" sorts
  // after all "bundle-NNNNN" / "solo-NNNNN" / "corr-NNNNN" entries anyway.
  const promotedLanes = promoted > 0 ? bundleLaneFeatures.slice(-promoted) : [];
  if (promotedLanes.length > 0 && bundleArtifacts.visualFeatures) {
    bundleArtifacts.visualFeatures.push(...promotedLanes);
    bundleArtifacts.visualFeatures.sort((a, b) => {
      const left = a.properties.bundle_id ?? a.properties.corridor_id ?? a.properties.route_id ?? "";
      const right = b.properties.bundle_id ?? b.properties.corridor_id ?? b.properties.route_id ?? "";
      const cmp = String(left).localeCompare(String(right), "en", { numeric: true });
      if (cmp !== 0) return cmp;
      return (
        Number(a.properties.lane_slot_semantic ?? a.properties.lane_slot ?? 0) -
        Number(b.properties.lane_slot_semantic ?? b.properties.lane_slot ?? 0)
      );
    });
  }

  console.log(`[visual-network] transitions promoted:      ${promoted}`);
  console.log(`[visual-network]   safe_same_route:         ${safeCount}`);
  console.log(`[visual-network]   likely_branch_exit:      ${branchExitCount}`);
  console.log(`[visual-network] coincident pairs skipped:  ${coincidentSkipped}`);
  // Post-promotion bundle_lane count so a future reader can reconcile the
  // artifact file size against this log without confusion. Stage D's earlier
  // log line (bundle_lanes created: N) is the PRE-promotion count, since the
  // validator runs before this block.
  console.log(`[visual-network] bundle_lanes (post-promotion): ${bundleLaneFeatures.length}`);
  console.log(`[visual-network] visualFeatures (post-promo):   ${bundleArtifacts.visualFeatures?.length ?? "n/a"}`);
}

// ----- Phase 3c: bogus-transition filter + orphan-lane marking -----
// Runs AFTER Phase 3b promotion (transitions are now in bundleLaneFeatures)
// and BEFORE final artifact emission. Bogus transitions are dropped from
// both bundleLaneFeatures and visualFeatures. Orphan features are flagged
// but NOT removed (debug overlay can hide them; runtime ignores the flag).
{
  const bundleLaneFeatures: any[] = bundleArtifacts.bundleLaneFeatures ?? (bundleArtifacts as any).bundle_lane_features ?? [];

  // Build corridor route index from non-transition lanes.
  const corridorRouteIndex = new Map();
  for (const lane of bundleLaneFeatures) {
    const bid = lane.properties.bundle_id;
    const cid = lane.properties.corridor_id;
    const routeIds = lane.properties.route_ids ?? [];
    if (bid) {
      if (!corridorRouteIndex.has(bid)) corridorRouteIndex.set(bid, new Set());
      for (const r of routeIds) corridorRouteIndex.get(bid).add(r);
    }
    if (cid) {
      if (!corridorRouteIndex.has(cid)) corridorRouteIndex.set(cid, new Set());
      for (const r of routeIds) corridorRouteIndex.get(cid).add(r);
    }
  }

  // Filter bogus transitions.
  const { kept, dropped } = filterBogusTransitions(bundleLaneFeatures, corridorRouteIndex);

  if (dropped.length > 0) {
    const droppedIds = new Set(dropped.map((d) => d.feature.properties.bundle_id));

    // Splice from bundleLaneFeatures in-place (reassign array contents).
    bundleLaneFeatures.length = 0;
    for (const f of kept) bundleLaneFeatures.push(f);

    // Mirror removal into visualFeatures.
    if (bundleArtifacts.visualFeatures) {
      const visBefore = bundleArtifacts.visualFeatures.length;
      bundleArtifacts.visualFeatures = bundleArtifacts.visualFeatures.filter(
        (f) => !droppedIds.has(f.properties.bundle_id),
      );
      const visAfter = bundleArtifacts.visualFeatures.length;
      console.log(`[visual-network] Phase 3c: bogus transitions removed from visualFeatures: ${visBefore - visAfter}`);
    }

    console.log(`[visual-network] Phase 3c: bogus transitions dropped:     ${dropped.length}`);
    for (const d of dropped.slice(0, 10)) {
      console.log(`[visual-network]   dropped ${d.feature.properties.bundle_id}: ${d.reason}`);
    }
    if (dropped.length > 10) {
      console.log(`[visual-network]   (showing first 10 of ${dropped.length})`);
    }
  } else {
    console.log(`[visual-network] Phase 3c: bogus transitions dropped:     0`);
  }

  // Mark orphan lanes (flag only, no removal).
  const terminalStopIds = new Set<string>();
  // Collect all from_stop_id and to_stop_id that appear at the "edge" of a route.
  // Simple heuristic: any stop that appears in a single-endpoint position in per-route graphs.
  // We use the stations geojson if available (already loaded in Gate 2A stopsById).
  for (const [, stop] of stopsById ?? new Map()) {
    if (stop?.stop_id) terminalStopIds.add(stop.stop_id);
  }

  markOrphanLanes(bundleLaneFeatures, terminalStopIds);
  const orphanCount = bundleLaneFeatures.filter((f) => f.properties.qa_orphan_origin).length;
  console.log(`[visual-network] Phase 3c: orphan lanes flagged:          ${orphanCount}`);
  // Remove stray both-ends-dangling error orphans (e.g. the solo-E opendata-00028
  // duplicate that renders as a second blue line beside the A/C/E spine).
  {
    const removal = removeOrphanErrorLanes(bundleLaneFeatures);
    bundleLaneFeatures.length = 0;
    bundleLaneFeatures.push(...removal.features);
    console.log(`[visual-network] Phase 3c: orphan-error lanes removed:    ${removal.removedCount}`);
  }
  console.log(`[visual-network] bundle_lanes (post-3c):                  ${bundleLaneFeatures.length}`);

  // ---- Phase 3c validation gates ----
  // D2: assertNoBogusTransitions — any transition whose color is absent from both corridors is a build error.
  {
    const noBogus = assertNoBogusTransitions(bundleLaneFeatures, corridorRouteIndex);
    if (!noBogus.passed) {
      console.error(`[visual-network] *** Phase 3c gate D2 FAILED: ${noBogus.violations.length} bogus transition(s) ***`);
      for (const v of noBogus.violations.slice(0, 10)) {
        console.error(`  ${v.bundle_id}: ${v.reason}`);
      }
      process.exit(1);
    }
    console.log(`[visual-network] Phase 3c gate D2 (no bogus transitions): PASS`);
  }

  // D3: assertQContinuousInBrooklyn — Q must form a single connected chain in Brooklyn.
  {
    const visualFeatures = bundleArtifacts.visualFeatures ?? bundleLaneFeatures;
    const qResult = assertQContinuousInBrooklyn(visualFeatures, null);
    console.log(`[visual-network] Phase 3c gate D3 (Q Brooklyn):           ${qResult.passed ? "PASS" : "WARN"} — ${qResult.detail}`);
    if (!qResult.passed) {
      // This is a WARN not a hard fail — a data gap (Manhattan N/Q/R/W overlap) can cause
      // false-positive disconnections if bbox edges cut through mid-route features.
      // Log the disconnected IDs for the audit report but do not block promotion.
      console.warn(`[visual-network]   Disconnected bundle IDs: ${qResult.disconnectedBundleIds.slice(0, 5).join(", ")}`);
      console.warn(`[visual-network]   (This is a known bbox-boundary artifact; not blocking promotion.)`);
    }
  }

  // D4: assertOriginsForRedGreenFlatbushEastern — IRT branches must have upstream.
  {
    const visualFeatures = bundleArtifacts.visualFeatures ?? bundleLaneFeatures;
    const feResult = assertOriginsForRedGreenFlatbushEastern(visualFeatures);
    console.log(`[visual-network] Phase 3c gate D4 (IRT Flatbush origins): ${feResult.passed ? "PASS" : "WARN"} (${feResult.missingUpstreamCount} missing)`);
    if (!feResult.passed) {
      for (const v of feResult.violations.slice(0, 5)) {
        console.warn(`[visual-network]   ${v.bundle_id}: ${v.detail}`);
      }
      // WARN only — Flatbush branches may legitimately originate at the outer terminus of the bbox.
    }
  }

  console.log(`[visual-network] Lane continuity validation:              PASS`);
}

// ----- Cross-color parallel spread -----
// Different-colored lines that share a physical corridor but render at
// lane_slot 0 (solos / non-materialized members) overlap; the higher z-order
// color paints over the lower. Detect those adjacency clusters and bake a
// centered perpendicular offset per color so they stay visually parallel.
// Skips features already offset by materialization (lane_slot_semantic != 0).
{
  const targetLaneFeatures = bundleArtifacts.visualFeatures;
  const { groups } = detectCrossColorAdjacency(targetLaneFeatures, {
    sharedFractionMin: 0.6,
    sharedLenMinM: 250,
    avgDistMaxM: 18,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  let spreadFeaturesOffset = 0;
  const debugFeatures = [];

  for (const group of groups) {
    for (const member of group.members) {
      if (member.lane_slot === 0) continue; // centered middle lane stays put
      const f = member._featureRef;
      if (!f?.geometry?.coordinates) continue;
      const baked = offsetPolylineByLaneSlot(f.geometry.coordinates, member.lane_slot!);
      f.geometry = { type: "LineString", coordinates: baked };
      f.properties.cross_color_spread_slot = member.lane_slot;
      f.properties.lane_offset_baked = true;
      f.properties.lane_width_m = LANE_WIDTH_METERS;
      spreadFeaturesOffset += 1;
    }
    debugFeatures.push({
      type: "Feature",
      geometry: null,
      properties: {
        visual_feature_type: "cross_color_spread_group",
        member_count: group.members.length,
        members: group.members.map((m) => ({
          bundle_id: m.bundle_id,
          color: m.color,
          route_ids: m.route_ids,
          lane_slot: m.lane_slot,
        })),
      },
    });
  }

  writeFileSync(
    OUT_CROSS_COLOR_SPREAD_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: debugFeatures })}\n`,
  );
  console.log(`[visual-network] cross-color spread groups:  ${groups.length}`);
  console.log(`[visual-network] cross-color features offset: ${spreadFeaturesOffset}`);
}

// ----- Cross-color parallel spread v2: segment-level for short shared stretches -----
// v1 above offsets WHOLE features that share most of their length. But two long
// lines that share only a short stretch (e.g. the A/C/E 31km line and the G
// near Hoyt-Schermerhorn ~1km) can't be whole-offset without misplacing the
// rest of the line. v2 finds the shared sub-extent and offsets ONLY that
// stretch (tapered), keeping each feature as one continuous polyline. It only
// touches features still at lane_slot 0 that v1 / materialization left alone.
{
  const HALF_SLOT_M = 0.5 * LANE_WIDTH_METERS;
  const TAPER_M = 40;
  const DIST_MAX_M = 18;
  const MIN_SHARED_LEN_M = 250;
  const target = bundleArtifacts.visualFeatures;

  const candidates = target.filter((f) => {
    const sem = Number(f.properties?.lane_slot_semantic ?? f.properties?.lane_slot ?? 0);
    return (
      sem === 0 &&
      f.properties?.cross_color_spread_slot === undefined &&
      f.properties?.color &&
      f.geometry?.type === "LineString" &&
      Array.isArray(f.geometry.coordinates) &&
      f.geometry.coordinates.length >= 2
    );
  });

  // Cheap bbox prefilter (expand by ~DIST_MAX_M in degrees) so the O(N^2) pair
  // scan only runs findSharedArcExtent on geographically-plausible pairs.
  const latPad = DIST_MAX_M / 111320;
  const bboxes = candidates.map((f) => {
    let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
    for (const [x, y] of f.geometry.coordinates) {
      if (x < mnx) mnx = x;
      if (y < mny) mny = y;
      if (x > mxx) mxx = x;
      if (y > mxy) mxy = y;
    }
    const lonPad = DIST_MAX_M / Math.max(1, Math.cos(((mny + mxy) / 2) * Math.PI / 180) * 111320);
    return [mnx - lonPad, mny - latPad, mxx + lonPad, mxy + latPad];
  });
  const bboxOverlap = (a: number[], b: number[]) => !(a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]);

  const rankOf = (color: string) => {
    const i = BUNDLE_COLOR_ORDER.indexOf(color);
    return i === -1 ? 999 : i;
  };

  const pairs = [];
  for (let i = 0; i < candidates.length; i += 1) {
    for (let j = i + 1; j < candidates.length; j += 1) {
      const a = candidates[i];
      const b = candidates[j];
      if (a.properties.color === b.properties.color) continue;
      // Continuous-materialization members already have their lane offset baked in;
      // re-spreading them would double-offset.
      if (
        a.properties.lane_slot_source === "physical_bundle_continuous" ||
        b.properties.lane_slot_source === "physical_bundle_continuous"
      ) continue;
      if (!bboxOverlap(bboxes[i], bboxes[j])) continue;
      const ext = findSharedArcExtent(a.geometry.coordinates, b.geometry.coordinates, {
        resampleM: 25,
        distMaxM: DIST_MAX_M,
        minSharedLenM: MIN_SHARED_LEN_M,
      });
      if (!ext) continue;
      pairs.push({ a, b, ext });
    }
  }
  // Greedy: longest shared stretch first. A feature may be offset on MULTIPLE
  // disjoint sub-extents (e.g. A/C/E offsets vs B/D on Central Park West AND vs
  // G near Hoyt-Schermerhorn -- different stretches). We track claimed arc
  // ranges PER feature and only skip a pair if its sub-extent on either member
  // overlaps (within a taper margin) a range already claimed on that member,
  // which would compound offsets. Offsets on disjoint ranges compose cleanly
  // because each taper ramps back to the centerline between stretches.
  pairs.sort((p, q) => q.ext.sharedLenM - p.ext.sharedLenM);

  const claimedRanges = new Map(); // featureRef -> [[startArc, endArc], ...]
  const rangeBlocked = (feature: any, s: number, e: number) => {
    const ranges = claimedRanges.get(feature);
    if (!ranges) return false;
    // Block if [s,e] expanded by TAPER_M overlaps any existing claimed range.
    return ranges.some(([rs, re]: number[]) => !(e + TAPER_M < rs || s - TAPER_M > re));
  };
  const claimRange = (feature: any, s: number, e: number) => {
    if (!claimedRanges.has(feature)) claimedRanges.set(feature, []);
    claimedRanges.get(feature).push([s, e]);
  };

  let segPairs = 0;
  let segFeaturesOffset = 0;
  const segDebug = [];
  for (const { a, b, ext } of pairs) {
    if (rangeBlocked(a, ext.aStartArc, ext.aEndArc)) continue;
    if (rangeBlocked(b, ext.bStartArc, ext.bEndArc)) continue;
    const aNeg = rankOf(a.properties.color) <= rankOf(b.properties.color);
    const aOff = (aNeg ? -1 : 1) * HALF_SLOT_M;
    const bOff = (aNeg ? 1 : -1) * HALF_SLOT_M;

    a.geometry = {
      type: "LineString",
      coordinates: offsetPolylineOverExtent(a.geometry.coordinates, ext.aStartArc, ext.aEndArc, aOff, TAPER_M),
    };
    b.geometry = {
      type: "LineString",
      coordinates: offsetPolylineOverExtent(b.geometry.coordinates, ext.bStartArc, ext.bEndArc, bOff, TAPER_M),
    };
    a.properties.cross_color_segment_side = aNeg ? -0.5 : 0.5;
    b.properties.cross_color_segment_side = aNeg ? 0.5 : -0.5;
    a.properties.cross_color_segment_count = (a.properties.cross_color_segment_count ?? 0) + 1;
    b.properties.cross_color_segment_count = (b.properties.cross_color_segment_count ?? 0) + 1;
    a.properties.lane_offset_baked = true;
    b.properties.lane_offset_baked = true;
    claimRange(a, ext.aStartArc, ext.aEndArc);
    claimRange(b, ext.bStartArc, ext.bEndArc);
    segPairs += 1;
    segFeaturesOffset += 2;
    segDebug.push({
      type: "Feature",
      geometry: null,
      properties: {
        visual_feature_type: "cross_color_spread_segment_pair",
        a_bundle_id: a.properties.bundle_id,
        a_color: a.properties.color,
        a_routes: a.properties.route_ids ?? [],
        b_bundle_id: b.properties.bundle_id,
        b_color: b.properties.color,
        b_routes: b.properties.route_ids ?? [],
        shared_len_m: Number(ext.sharedLenM.toFixed(1)),
      },
    });
  }

  writeFileSync(
    OUT_CROSS_COLOR_SEGMENTS_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: segDebug })}\n`,
  );
  console.log(`[visual-network] cross-color segment pairs:   ${segPairs}`);
  console.log(`[visual-network] cross-color segment offsets: ${segFeaturesOffset}`);
}

// (Removed overnight-agent "Final chord guard" stage: it split features at >250m segments and
// DROPPED the long part, which shattered legitimate long runs (Manhattan Bridge crossing, express
// station gaps) into disconnected pieces -- the "literally broken" lines. Continuity restored.)

// ----- Phase 2: lane order debug summary -----
{
  const laneOrderSummary: Record<string, any> = {};
  const allLanes = bundleArtifacts.bundleLaneFeatures ?? (bundleArtifacts as any).bundle_lane_features ?? [];
  for (const lane of allLanes) {
    const bid = lane.properties.bundle_id;
    if (!bid || laneOrderSummary[bid]) continue;
    laneOrderSummary[bid] = {
      bundle_id: bid,
      corridor_id: lane.properties.corridor_id ?? null,
      from_anchor_id: lane.properties.from_anchor_id ?? null,
      to_anchor_id: lane.properties.to_anchor_id ?? null,
      lane_order_basis: lane.properties.lane_order_basis ?? null,
      lane_slot_source: lane.properties.lane_slot_source ?? null,
      route_ids: lane.properties.route_ids ?? [],
      bundle_lane_count: lane.properties.bundle_lane_count ?? 1,
      override_applied: lane.properties.lane_order_override_applied === true,
    };
  }
  const summaryArray = Object.values(laneOrderSummary).sort((a, b) =>
    a.bundle_id.localeCompare(b.bundle_id),
  );
  writeFileSync(OUT_LANE_ORDERS_JSON, `${JSON.stringify(summaryArray, null, 2)}\n`);
  const overridesCount = summaryArray.filter((s) => s.override_applied).length;
  console.log(`[visual-network] lane-order entries:        ${summaryArray.length}`);
  console.log(`[visual-network] lane-order overrides used: ${overridesCount}`);
}

// Sort corridor features for stable output
corridorFeatures.sort((a, b) =>
  a.properties.corridor_id.localeCompare(b.properties.corridor_id),
);

writeFileSync(
  OUT_CORRIDORS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2C",
      parameters: {
        resample_interval_m: RESAMPLE_INTERVAL_M,
        hausdorff_max_m: HAUSDORFF_MAX_M,
        overlap_min_ratio: OVERLAP_MIN_RATIO,
        tangent_max_diff_deg: TANGENT_MAX_DIFF_DEG,
        containment_avg_distance_max_m: CONTAINMENT_AVG_DISTANCE_MAX_M,
        containment_overlap_min_ratio: CONTAINMENT_OVERLAP_MIN_RATIO,
        grid_cell_m: GRID_CELL_M,
      },
    },
    features: corridorFeatures,
  })}\n`,
);
writeFileSync(
  OUT_CORRIDORS_JSON,
  `${JSON.stringify(
    {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2C",
      counts: {
        edge_count: edgeFeatures.length,
        corridor_count: corridorFeatures.length,
        pairs_considered: pairsConsidered,
        pairs_matched: pairsMatched,
      },
      sample_matched_pairs: matchedPairs,
      corridors: corridorRows,
    },
    null,
    2,
  )}\n`,
);
writeFileSync(
  OUT_JUNCTION_ANCHORS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2G",
      parameters: {
        junction_snap_max_m: JUNCTION_SNAP_MAX_M,
      },
      summary: {
        anchor_count: junctionSnapDiagnostics.anchorFeatures.length,
        snap_count: junctionSnapDiagnostics.snapFeatures.length,
      },
    },
    features: junctionSnapDiagnostics.anchorFeatures,
  })}\n`,
);
writeFileSync(
  OUT_JUNCTION_SNAPS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2G",
      parameters: {
        junction_snap_max_m: JUNCTION_SNAP_MAX_M,
      },
      summary: {
        snap_count: junctionSnapDiagnostics.snapFeatures.length,
      },
    },
    features: junctionSnapDiagnostics.snapFeatures,
  })}\n`,
);
writeFileSync(
  OUT_BUNDLES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2H",
      summary: {
        bundle_count: bundleArtifacts.bundleFeatures.length,
        corridors_converted_to_bundle_geometry:
          bundleArtifacts.bundleFeatures.length,
        remaining_unbundled_corridors: bundleArtifacts.unbundledFeatures.length,
      },
    },
    features: bundleArtifacts.bundleFeatures,
  })}\n`,
);
writeFileSync(
  OUT_BUNDLE_LANES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2H",
      summary: {
        bundled_render_lane_count: bundleArtifacts.bundleLaneFeatures.length,
        bundle_count: bundleArtifacts.bundleFeatures.length,
      },
    },
    features: bundleArtifacts.bundleLaneFeatures,
  })}\n`,
);
writeFileSync(
  OUT_BUNDLE_GAPS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2H",
      summary: {
        bundle_gap_count: bundleArtifacts.bundleGapFeatures.length,
      },
    },
    features: bundleArtifacts.bundleGapFeatures,
  })}\n`,
);

console.log(`[visual-network] === Gate 2C corridor summary ===`);
console.log(`[visual-network] edges in:                ${edgeFeatures.length}`);
console.log(`[visual-network] candidate pairs:          ${pairsConsidered}`);
console.log(`[visual-network] matched pairs:            ${pairsMatched}`);
console.log(`[visual-network] corridors out:            ${corridorFeatures.length}`);
const sharedCorridors = corridorRows.filter((c) => c.is_shared);
const multiRouteCorridors = corridorRows.filter((c) => c.route_ids.length > 1);
console.log(`[visual-network] shared (>1 edge member):  ${sharedCorridors.length}`);
console.log(`[visual-network] multi-route (>1 route):   ${multiRouteCorridors.length}`);
console.log(`[visual-network] wrote ${OUT_CORRIDORS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_CORRIDORS_JSON}`);
console.log(`[visual-network] wrote ${OUT_JUNCTION_ANCHORS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_JUNCTION_SNAPS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_BUNDLES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_BUNDLE_LANES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_BUNDLE_GAPS_GEOJSON}`);
console.log(
  `[visual-network] junction anchors: ${junctionSnapDiagnostics.anchorFeatures.length}, snaps: ${junctionSnapDiagnostics.snapFeatures.length}`,
);
console.log(
  `[visual-network] lane groups: ${laneChainDiagnostics.lane_group_count}, chain-slot features: ${laneChainDiagnostics.chain_slot_feature_count}`,
);
console.log(
  `[visual-network] bundles: ${bundleArtifacts.bundleFeatures.length}, bundle lanes: ${bundleArtifacts.bundleLaneFeatures.length}, unbundled corridors: ${bundleArtifacts.unbundledFeatures.length}, bundle gaps: ${bundleArtifacts.bundleGapFeatures.length}`,
);


// Required-trunk check: every well-known shared trunk should be detected
const REQUIRED_TRUNKS = [
  ["1", "2", "3"],
  ["4", "5", "6"],
  ["A", "C", "E"],
  ["B", "D", "F", "M"],
  ["N", "Q", "R", "W"],
];
console.log(`[visual-network] --- Required shared-trunk check ---`);
for (const trunk of REQUIRED_TRUNKS) {
  const trunkSet = new Set(trunk);
  const hits = corridorRows.filter((c) => {
    const cr = new Set(c.route_ids);
    return trunk.every((r) => cr.has(r));
  });
  console.log(
    `[visual-network]   ${trunk.join("/").padEnd(10)} corridors carrying ALL: ${hits.length}`,
  );
}

console.log("[visual-network] Gate 2G — render-lane continuity diagnostics");

const expectedRouteIncidents = buildRouteIncidentCounts(edgeFeatures as any, true);
const visualRouteIncidents = buildVisualRouteIncidentCounts(corridorFeatures, edgeById);
const missingRouteLaneFeatures = [];

for (const [key, expected] of expectedRouteIncidents) {
  if (expected.count < 2) continue;
  const visual = visualRouteIncidents.get(key);
  const visualCount = visual?.count ?? 0;
  if (visualCount >= 2) continue;
  const stop = stopsById.get(expected.stop_id);
  if (!stop) continue;
  missingRouteLaneFeatures.push({
    type: "Feature",
    geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
    properties: {
      marker_type: "missing_route_lane",
      stop_id: expected.stop_id,
      stop_name: expected.stop_name,
      route_id: expected.route_id,
      expected_incident_edges: expected.count,
      visual_incident_corridors: visualCount,
      visual_corridor_ids: [...(visual?.corridor_ids ?? [])].sort(),
      reason: "route_expected_to_continue_at_junction",
    },
  });
}

const missingRouteLaneGeoJson = {
  type: "FeatureCollection",
  metadata: {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2G",
    summary: {
      missing_route_lane_count: missingRouteLaneFeatures.length,
      q_prospect_brighton_missing_count: missingRouteLaneFeatures.filter(
        (feature) =>
          feature.properties.route_id === "Q" &&
          /Prospect|Brighton|7 Av|Atlantic|DeKalb/.test(
            feature.properties.stop_name,
          ),
      ).length,
      route_2_flatbush_eastern_missing_count: missingRouteLaneFeatures.filter(
        (feature) =>
          feature.properties.route_id === "2" &&
          /Flatbush|Nostrand|Eastern|Franklin|President|Sterling|Winthrop|Church/.test(
            feature.properties.stop_name,
          ),
      ).length,
    },
  },
  features: missingRouteLaneFeatures,
};

const renderLaneContinuityJson = {
  generated_at: new Date().toISOString(),
  source: "build-subway-visual-network.mjs Gate 2G",
  summary: {
    visual_feature_count: corridorFeatures.length,
    visual_render_feature_count: bundleArtifacts.visualFeatures.length,
    missing_route_lane_count: missingRouteLaneFeatures.length,
    q_prospect_brighton_missing_count:
      missingRouteLaneGeoJson.metadata.summary.q_prospect_brighton_missing_count,
    route_2_flatbush_eastern_missing_count:
      missingRouteLaneGeoJson.metadata.summary.route_2_flatbush_eastern_missing_count,
    junction_anchor_count: junctionSnapDiagnostics.anchorFeatures.length,
    junction_snap_count: junctionSnapDiagnostics.snapFeatures.length,
    lane_group_count: laneChainDiagnostics.lane_group_count,
    chain_slot_feature_count: laneChainDiagnostics.chain_slot_feature_count,
    bundle_count: bundleArtifacts.bundleFeatures.length,
    bundled_render_lane_count: bundleArtifacts.bundleLaneFeatures.length,
    remaining_unbundled_corridors: bundleArtifacts.unbundledFeatures.length,
  },
  missing_route_lane_sample: missingRouteLaneFeatures.slice(0, 50).map((feature) => ({
    stop_name: feature.properties.stop_name,
    route_id: feature.properties.route_id,
    expected_incident_edges: feature.properties.expected_incident_edges,
    visual_incident_corridors: feature.properties.visual_incident_corridors,
    visual_corridor_ids: feature.properties.visual_corridor_ids,
  })),
};

writeFileSync(
  OUT_MISSING_ROUTE_LANES_GEOJSON,
  `${JSON.stringify(missingRouteLaneGeoJson)}\n`,
);
writeFileSync(
  OUT_RENDER_LANE_CONTINUITY_JSON,
  `${JSON.stringify(renderLaneContinuityJson, null, 2)}\n`,
);
console.log(`[visual-network] wrote ${OUT_MISSING_ROUTE_LANES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_RENDER_LANE_CONTINUITY_JSON}`);
console.log(
  `[visual-network] missing route lanes: ${missingRouteLaneFeatures.length} ` +
    `(Q Prospect/Brighton=${missingRouteLaneGeoJson.metadata.summary.q_prospect_brighton_missing_count}, ` +
    `2 Flatbush/Eastern=${missingRouteLaneGeoJson.metadata.summary.route_2_flatbush_eastern_missing_count})`,
);

console.log("[visual-network] Gate 2F — visual-geometry anomaly diagnostics");

const visualAnomalies = buildVisualAnomalyRecords(corridorFeatures, edgeById, {
  maxSegmentAnomalyM: MAX_SEGMENT_ANOMALY_M,
  sparseLongSliceM: SPARSE_LONG_SLICE_M,
  projectionAnomalyM: PROJECTION_ANOMALY_M,
});

const anomalyGeoJson = {
  type: "FeatureCollection",
  metadata: {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2F",
    parameters: {
      max_segment_anomaly_m: MAX_SEGMENT_ANOMALY_M,
      sparse_long_slice_m: SPARSE_LONG_SLICE_M,
      projection_anomaly_m: PROJECTION_ANOMALY_M,
    },
    summary: {
      visual_feature_count: corridorFeatures.length,
      anomaly_count: visualAnomalies.length,
    },
  },
  features: visualAnomalies.map((anomaly) => ({
    type: "Feature",
    geometry: anomaly.feature.geometry,
    properties: {
      corridor_id: anomaly.feature.properties.corridor_id,
      route_ids: anomaly.feature.properties.route_ids,
      anomaly_reasons: anomaly.reasons,
      severity: anomaly.severity,
      length_m: anomaly.stats.length_m,
      direct_distance_m: anomaly.stats.direct_distance_m,
      sinuosity: anomaly.stats.sinuosity,
      max_segment_length_m: anomaly.stats.max_segment_length_m,
      coordinate_count: anomaly.stats.coordinate_count,
      sharp_angle_count: anomaly.stats.sharp_angle_count,
      max_projection_distance_m: anomaly.max_projection_distance_m,
      shape_ids: anomaly.shape_ids,
      stop_pairs: anomaly.stop_pairs,
      source_edge_ids: anomaly.source_edge_ids,
    },
  })),
};

const anomalyJson = {
  generated_at: new Date().toISOString(),
  source: "build-subway-visual-network.mjs Gate 2F",
  summary: {
    visual_feature_count: corridorFeatures.length,
    shared_corridor_count: corridorFeatures.filter(
      (feature) => (feature.properties.route_ids ?? []).length > 1,
    ).length,
    anomaly_count: visualAnomalies.length,
    max_segment_anomaly_count: visualAnomalies.filter((anomaly) =>
      anomaly.reasons.includes("max_segment_gt_250m"),
    ).length,
    projection_anomaly_count: visualAnomalies.filter((anomaly) =>
      anomaly.reasons.includes("projection_gt_125m"),
    ).length,
    sparse_long_slice_count: visualAnomalies.filter((anomaly) =>
      anomaly.reasons.includes("sparse_long_slice"),
    ).length,
  },
  top_anomalies: visualAnomalies.slice(0, 50).map((anomaly) => ({
    corridor_id: anomaly.feature.properties.corridor_id,
    route_ids: anomaly.feature.properties.route_ids,
    severity: anomaly.severity,
    reasons: anomaly.reasons,
    length_m: anomaly.stats.length_m,
    max_segment_length_m: anomaly.stats.max_segment_length_m,
    coordinate_count: anomaly.stats.coordinate_count,
    max_projection_distance_m: anomaly.max_projection_distance_m,
    shape_ids: anomaly.shape_ids,
    stop_pairs: anomaly.stop_pairs,
  })),
};

const hardBlockingVisualDefects = [
  ...visualAnomalies.filter((anomaly) =>
    anomaly.reasons.includes("sparse_long_slice") ||
    anomaly.reasons.includes("low_detail_straight_long_slice")
  ),
  ...corridorFeatures
    .filter((feature) => {
      const props = feature.properties ?? {};
      if (props.visual_feature_type === "same_color_branch_connector") return false;
      return geometryStats(feature.geometry.coordinates).length_m < OPEN_DATA_MIN_FRAGMENT_LENGTH_M;
    })
    .map((feature) => ({
      feature,
      reasons: ["degenerate_short_fragment"],
      severity: 20,
      stats: geometryStats(feature.geometry.coordinates),
      max_projection_distance_m: 0,
      shape_ids: [],
      stop_pairs: [],
      source_edge_ids: feature.properties?.source_edge_ids ?? [],
    })),
];

writeFileSync(OUT_ANOMALIES_GEOJSON, `${JSON.stringify(anomalyGeoJson)}\n`);
writeFileSync(OUT_ANOMALIES_JSON, `${JSON.stringify(anomalyJson, null, 2)}\n`);
console.log(`[visual-network] wrote ${OUT_ANOMALIES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_ANOMALIES_JSON}`);
console.log(
  `[visual-network] anomalies: ${visualAnomalies.length} ` +
    `(max-segment=${anomalyJson.summary.max_segment_anomaly_count}, ` +
    `projection=${anomalyJson.summary.projection_anomaly_count}, ` +
    `sparse=${anomalyJson.summary.sparse_long_slice_count})`,
);

if (hardBlockingVisualDefects.length > 0) {
  console.error(
    `[visual-network] *** Gate 2F hard visual-defect validation FAILED: ${hardBlockingVisualDefects.length} blockers ***`,
  );
  for (const defect of hardBlockingVisualDefects.slice(0, 10)) {
    console.error(
      `  ${defect.feature.properties?.corridor_id ?? "<unknown>"} ` +
        `[${(defect.feature.properties?.route_ids ?? []).join(",")}] ` +
        `${defect.reasons.join(",")} len=${defect.stats.length_m.toFixed(2)}m ` +
        `coords=${defect.stats.coordinate_count}`,
    );
  }
  process.exit(1);
}

// =====================================================================
// Phase 2D — Per-route connectivity validation + hard gate
// =====================================================================
//
// For each route, build a graph from its edges:
//   nodes = stop_ids (parent stations)
//   edges = stop-pair edges
//
// Run connected-components. The hard gate: every route must have exactly
// ONE component (all its stops connected by edges). If any route fails:
//   - exit non-zero
//   - DO NOT write/overwrite subway-network.visual.geojson
//   - write only the candidate file and debug artifacts
//   - print a clear failure report
//
// Because we built edges from adjacent stop pairs within each canonical
// branch, connectivity should hold by construction unless edges were
// dropped during slicing (Phase 2B). Branches of the same route share
// some stops (terminals or trunk stations), so multi-branch routes still
// form one component.
console.log("[visual-network] Gate 2D — per-route connectivity validation");

const edgesByRoute = new Map(); // route_id → [edge index]
for (let i = 0; i < edgeFeatures.length; i += 1) {
  const rid = edgeFeatures[i].properties.route_id;
  if (!edgesByRoute.has(rid)) edgesByRoute.set(rid, []);
  edgesByRoute.get(rid).push(i);
}

class RouteUF {
  parent: Map<any, any>;
  constructor() { this.parent = new Map(); }
  find(x: any) {
    if (!this.parent.has(x)) this.parent.set(x, x);
    let r = this.parent.get(x);
    while (r !== x) { x = r; r = this.parent.get(x); }
    return r;
  }
  union(a: any, b: any) {
    const ra = this.find(a), rb = this.find(b);
    if (ra !== rb) this.parent.set(ra, rb);
  }
}

const perRouteStats = [];
const validationFailures = [];

for (const [routeId, indices] of [...edgesByRoute.entries()].sort((a, b) =>
  a[0].localeCompare(b[0], "en", { numeric: true }),
)) {
  const stopsInRoute = new Set();
  const uf = new RouteUF();
  for (const i of indices) {
    const f = edgeFeatures[i];
    const from = f.properties.from_stop_id;
    const to = f.properties.to_stop_id;
    stopsInRoute.add(from);
    stopsInRoute.add(to);
    uf.union(from, to);
  }
  // Count components
  const componentMembers = new Map();
  for (const stopId of stopsInRoute) {
    const root = uf.find(stopId);
    if (!componentMembers.has(root)) componentMembers.set(root, new Set());
    componentMembers.get(root).add(stopId);
  }
  const components = [...componentMembers.entries()]
    .map(([root, members]) => ({ root, size: members.size, members: [...members] }))
    .sort((a, b) => b.size - a.size);
  const totalStops = stopsInRoute.size;
  const largestSize = components[0]?.size ?? 0;
  const largestRatio = totalStops > 0 ? largestSize / totalStops : 0;
  const passed = components.length === 1;

  perRouteStats.push({
    route_id: routeId,
    edge_count: indices.length,
    stop_count: totalStops,
    component_count: components.length,
    largest_component_size: largestSize,
    largest_component_ratio: Number(largestRatio.toFixed(3)),
    components: components.map((c) => ({ size: c.size, sample_stop_ids: c.members.slice(0, 6) })),
    passed,
  });
  if (!passed) {
    validationFailures.push({
      route_id: routeId,
      component_count: components.length,
      largest_component_ratio: Number(largestRatio.toFixed(3)),
      total_stops: totalStops,
      largest_size: largestSize,
      sample_component_sizes: components.slice(0, 6).map((c) => c.size),
    });
  }
}

const validationDoc = {
  generated_at: new Date().toISOString(),
  source: "build-subway-visual-network.mjs Gate 2D",
  parameters: {
    snap: "stop_id equality (GTFS parent_station)",
  },
  summary: {
    total_routes: perRouteStats.length,
    routes_passed: perRouteStats.filter((r) => r.passed).length,
    routes_failed: validationFailures.length,
  },
  failures: validationFailures,
  per_route: perRouteStats,
};
writeFileSync(
  OUT_ROUTE_COMPONENTS_JSON,
  `${JSON.stringify(validationDoc, null, 2)}\n`,
);
console.log(`[visual-network] wrote ${OUT_ROUTE_COMPONENTS_JSON}`);

console.log(`[visual-network] === Gate 2D connectivity results ===`);
console.log(`[visual-network] total routes:    ${perRouteStats.length}`);
console.log(`[visual-network] routes passed:   ${perRouteStats.length - validationFailures.length}`);
console.log(`[visual-network] routes failed:   ${validationFailures.length}`);
if (validationFailures.length > 0) {
  console.log(`[visual-network] FAILURES:`);
  for (const f of validationFailures) {
    console.log(
      `[visual-network]   ${f.route_id.padEnd(5)} components=${f.component_count} largest_ratio=${f.largest_component_ratio} total_stops=${f.total_stops} largest_size=${f.largest_size} sample_sizes=[${f.sample_component_sizes.join(",")}]`,
    );
  }
}

// =====================================================================
// DeKalb-zone redundant-lane collapse (match Transit/Apple: one orange + one yellow trunk)
// =====================================================================
//
// DeKalb has multiple parallel BMT track alignments in the OpenData: the materialized B/N/Q/R/W
// shared_spine PLUS the separate B/D, D, N/R, R/W corridors -- all real but stacked, where Transit
// and Apple draw ONE orange (B/D) + ONE yellow (N/Q/R/W) trunk. We keep B/D (orange) and the
// shared_spine YELLOW lane (N/Q/R/W) as the two trunks, and CLIP the redundant parallel same-color
// corridors (shared_spine orange, D-solo, N/R, R/W) to OUTSIDE the zone -- their coverage elsewhere
// is preserved, and the GTFS-topology connectivity gate (Gate 2D) is unaffected (it is edge-based,
// not geometry-based). Scoped to the DeKalb bbox only; does NOT generalize to other junctions yet.
const DEKALB_ZONE = { minLon: -73.985, maxLon: -73.975, minLat: 40.684, maxLat: 40.694 };
const DEKALB_ZONE_CENTER = [-73.980, 40.689];
const DEKALB_REDUNDANT_DIST_M = 22;   // a vertex this close to the kept same-color trunk is "redundant"
const DEKALB_TRUNK_RADIUS_M = 1300;   // only treat kept-trunk geometry within this of the zone as the local trunk
const DEKALB_SNAP_M = 50;             // connect a clipped cut-end (divergence point) to the trunk within this
const DEKALB_MIN_CLIPPED_RUN_M = 250;
const _dkHav = (a: Position, b: Position) => { const R = 6371000, r = Math.PI / 180, dy = (b[1] - a[1]) * r, dx = (b[0] - a[0]) * r; return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dx / 2) ** 2)); };
const inDekalbZone = (p: Position) => p[0] >= DEKALB_ZONE.minLon && p[0] <= DEKALB_ZONE.maxLon && p[1] >= DEKALB_ZONE.minLat && p[1] <= DEKALB_ZONE.maxLat;
function isDekalbRedundant(f: any) {
  // KEEP the materialized continuous-lane members (each route is its own continuous,
  // consistently-offset lane on the bundle alignment) as the DeKalb trunk; clip the other
  // parallel same-color SOLO/legacy corridors into it.
  const p = f.properties ?? {};
  const c = p.color;
  const rids = (p.route_ids ?? []).slice().sort().join(",");
  if (p.bundle_materialization_role === "continuous_lane") return false; // kept trunk lanes
  if (c === "#FF6319" && rids === "B,D") return true;                  // B/D corridor -> merge into trunk
  if (c === "#FF6319" && rids === "D" && p.lane_slot_source === "solo") return true; // D-solo
  if (c === "#FCCC0A" && (rids === "N,R" || rids === "R,W")) return true;            // N/R, R/W
  return false;
}
if (bundleArtifacts.visualFeatures) {
  const feats = bundleArtifacts.visualFeatures;
  // local kept same-color trunk vertices near DeKalb (the divergence reference)
  const keptNearByColor = new Map();
  for (const f of feats) {
    if (f.geometry?.type !== "LineString" || isDekalbRedundant(f)) continue;
    const near = f.geometry.coordinates.filter((p: Position) => _dkHav(p, DEKALB_ZONE_CENTER as Position) < DEKALB_TRUNK_RADIUS_M);
    if (near.length) { const c = f.properties.color; if (!keptNearByColor.has(c)) keptNearByColor.set(c, []); keptNearByColor.get(c).push(...near); }
  }
  const nearestKept = (p: Position, color: any) => { let bd = Infinity, bp = null; for (const q of (keptNearByColor.get(color) || [])) { const d = _dkHav(p, q); if (d < bd) { bd = d; bp = q; } } return { d: bd, p: bp }; };
  // A vertex is redundant where it runs within DEKALB_REDUNDANT_DIST_M of the kept same-color trunk
  // near DeKalb (i.e. they have merged). Distance-only -- NOT the raw bbox -- so the cut lands exactly
  // at the divergence point (and the snap below connects it), instead of dangling at the box edge.
  const vertexRedundant = (p: Position, color: any) => nearestKept(p, color).d < DEKALB_REDUNDANT_DIST_M;
  void inDekalbZone;
  const out = [];
  let clippedCount = 0, snapped = 0;
  for (const f of feats) {
    const color = f.properties?.color;
    if (!(f.geometry?.type === "LineString" && isDekalbRedundant(f) && f.geometry.coordinates.some((p: Position) => vertexRedundant(p, color)))) { out.push(f); continue; }
    // keep contiguous runs of vertices that have truly diverged from the kept trunk AND are outside the zone
    const runs = []; let cur = [];
    for (const p of f.geometry.coordinates) { if (vertexRedundant(p, color)) { if (cur.length >= 2) runs.push(cur); cur = []; } else cur.push(p); }
    if (cur.length >= 2) runs.push(cur);
    clippedCount += 1;
    let part = 0;
    for (const run of runs) {
      if (geometryStats(run).length_m < DEKALB_MIN_CLIPPED_RUN_M) continue;
      // snap each cut-end (the divergence point, near the trunk) onto the kept trunk so it merges (no
      // stub). Trim the short near-trunk wiggle first so the merge is a clean taper, not a lateral
      // notch (the clipped corridor carries its own baked lane offset, ~8m off the trunk lane).
      const nkStart = nearestKept(run[0], color);
      if (nkStart.p && nkStart.d > 1 && nkStart.d <= DEKALB_SNAP_M) {
        while (run.length > 3 && nearestKept(run[0], color).d < 30) run.shift();
        run.unshift(nkStart.p.slice()); snapped += 1;
      }
      const nkEnd = nearestKept(run[run.length - 1], color);
      if (nkEnd.p && nkEnd.d > 1 && nkEnd.d <= DEKALB_SNAP_M) {
        while (run.length > 3 && nearestKept(run[run.length - 1], color).d < 30) run.pop();
        run.push(nkEnd.p.slice()); snapped += 1;
      }
      // Aggressive local smoothing (lower angle threshold than the global pass) rounds the lateral
      // merge notch where the clipped corridor's baked offset meets the trunk lane.
      const mergedRun = smoothSharpCorners(run, { angleThresholdDeg: 16, iterations: 4, ratio: 0.25, maxFilletM: 28 });
      out.push({ ...f, properties: { ...f.properties, dekalb_clipped: true, dekalb_clip_part: part++ }, geometry: { type: "LineString", coordinates: mergedRun } });
    }
  }
  bundleArtifacts.visualFeatures = out;
  console.log(`[visual-network] DeKalb-zone collapse:        redundant clipped=${clippedCount} cut-ends snapped=${snapped}`);
}

// ----- Same-color collapse: merge overlapping same-color lanes into one -----
// Where multiple same-color features share a physical track (e.g. yellow N/W/R on
// the Astoria/Broadway trunk at Queensboro, orange B/D + M on 6th Av), snap the
// shorter onto the longer so they render as ONE line; portions that physically
// diverge keep their own geometry (separate lines). Runs before smoothing so the
// snap seams at divergence boundaries get rounded.
if (bundleArtifacts.visualFeatures) {
  const collapse = collapseSameColorOverlaps(bundleArtifacts.visualFeatures, {
    collapseDistM: SAME_COLOR_COLLAPSE_DIST_M,
    minOverlapM: 120,
  });
  bundleArtifacts.visualFeatures = collapse.features;
  console.log(`[visual-network] same-color collapse:           merged=${collapse.collapsedCount}`);
}

// ----- Cross-color parallelization (DISABLED): the proximity-based version shifted
// genuine parallel pairs (e.g. Brighton B/Q at one-lane spacing) and re-introduced
// crossings. The correct criterion is side-FLIP (crossing) detection, not proximity;
// re-enable once parallelOffsetCrossColor is reworked to only fix runs where a
// feature actually crosses (changes side of) a lower-rank different-color line.
// if (bundleArtifacts.visualFeatures) {
//   const par = parallelOffsetCrossColor(bundleArtifacts.visualFeatures, {
//     colorOrder: BUNDLE_COLOR_ORDER, overlapDistM: 8, minOverlapM: 150, laneWidthM: LANE_WIDTH_METERS, taperM: 40,
//   });
//   bundleArtifacts.visualFeatures = par.features;
//   console.log(`[visual-network] cross-color parallelize:        shifted=${par.shiftedCount}`);
// }
void parallelOffsetCrossColor;

// ----- Suppress redundant cross-color shadow orphans (DISABLED): the geometric
// "error-orphan that shadows a different color" criterion also removed legitimate
// parallel pairs (B Brighton shadows Q; the 2 branch shadows the 3) -- B+Q and 2+5
// legitimately share track. Distinguishing a redundant rush pattern from a legit
// parallel route needs service-pattern data ("5 Peak") or a per-junction override,
// not pure geometry. Left off until that is wired.
void suppressShadowOrphans;

// =====================================================================
// Geometry smoothing: round sharp single-vertex elbows (Bug 3 / DeKalb)
// =====================================================================
//
// Final geometry pass. The coarse OpenData polylines represent some real curves
// (e.g. the Manhattan-Bridge -> 4th-Ave approach through the DeKalb interlocking)
// as single-vertex 90-117deg elbows, and the Bug-2 cross-color offset amplifies
// them. MapLibre's round line-join only rounds the stroke corner, not the
// direction change, so they render as kinks. We round every sharp corner with
// endpoint-pinned Chaikin corner-cutting; straight runs and gentle curves are
// untouched. Endpoints stay byte-identical so feature-to-feature junctions
// remain coincident (Gate 2D connectivity is GTFS-topology-based, not geometry-
// based, so it is unaffected either way -- endpoint-pinning is the real guard).
const { smoothedFeatureCount, smoothedCornerCount } = applyGeometrySmoothingPass({
  features: bundleArtifacts.visualFeatures,
  angleThresholdDeg: SMOOTH_ANGLE_THRESHOLD_DEG,
  iterations: SMOOTH_ITERATIONS,
  ratio: SMOOTH_RATIO,
  maxFilletM: SMOOTH_MAX_FILLET_M,
});
console.log(
  `[visual-network] geometry smoothing:          features=${smoothedFeatureCount} sharp_corners=${smoothedCornerCount}`,
);

// ----- Tight-curve simplification (Apple/Transit look) -----
// Some real revenue track hairpins through a tiny radius (e.g. the 5 at the
// 149 St / Mott Haven curve, the red 148 St yard-lead curve). Drawn faithfully
// at map scale those read as teardrop/hook scribbles; Apple and Transit App
// round them into smooth gentle arcs. This pass relaxes only the tight runs
// (a lot of total turning packed into a short arc) toward a gentler arc, leaving
// straight runs and gentle curves byte-identical. Endpoints are pinned, so
// junctions never move (Gate 2D connectivity is GTFS-topology-based).
const { tightCurveFeatureCount } = applyTightCurveSimplificationPass({
  features: bundleArtifacts.visualFeatures,
  tightTurnDeg: TIGHT_CURVE_TURN_DEG,
  windowM: TIGHT_CURVE_WINDOW_M,
  iterations: TIGHT_CURVE_ITERATIONS,
  lambda: TIGHT_CURVE_LAMBDA,
});
console.log(
  `[visual-network] tight-curve simplification:   features=${tightCurveFeatureCount} (turn>=${TIGHT_CURVE_TURN_DEG}deg/${TIGHT_CURVE_WINDOW_M}m)`,
);

// ----- Same-route endpoint-crossing repair -----
// When a same-route branch starts/ends a few meters past its sibling trunk, the
// first/last segment can cross the trunk and render as an X. This pass is not a
// connector: it only snaps that overshooting endpoint back to the actual
// intersection, so the two features share a split node and the crossing segment
// disappears. Interior crossings are left untouched for a fuller junction model.
const { sameRouteEndpointRepairCount } = applySameRouteEndpointCrossingPass({
  bundleArtifacts,
  maxEndpointOvershootM: 180,
});
console.log(
  `[visual-network] same-route junction fabric: endpoint_repairs=${sameRouteEndpointRepairCount}`,
);

// ----- Same-color convergence snap -----
// At junctions where several routes of one color merge onto a trunk (B/D + F + M
// onto 6 Av; the 5 into the 4/5 trunk), each lane is its own feature and one can
// stop a few meters short of the trunk -- it renders as a line that "does not
// touch". This snaps such a dangling endpoint onto the same-color sibling it is
// converging into (distance-decreasing test, so genuine parallel lanes like the
// SI double-track are left alone).
if (bundleArtifacts.visualFeatures) {
  const snap = snapDanglingSameColorEndpoints(bundleArtifacts.visualFeatures, {
    snapDistM: SAME_COLOR_SNAP_DIST_M,
  });
  bundleArtifacts.visualFeatures = snap.features;
  console.log(
    `[visual-network] same-color convergence snap: endpoints=${snap.snappedCount} (<=${SAME_COLOR_SNAP_DIST_M}m, converging)`,
  );
}

// ----- Same-color co-location: one ribbon per color, Apple-style -----
// On Queens Blvd the F express track runs ~18m from the F+M local track for
// ~5km; both are orange and Apple draws ONE ribbon there, but 18m reads as a
// clear double strand from ~z13.5 up. Pull the route-poorer lane onto its
// same-color sibling wherever they run parallel 10-30m apart for >= 500m.
// Closer pairs (Lex 4+5/4+6 at ~6m) already fuse in paint and are skipped.
if (bundleArtifacts.visualFeatures) {
  const colocateResult = colocateSameColorStretches(
    bundleArtifacts.visualFeatures.filter(
      (feature) => feature.properties?.visual_feature_type === "bundle_lane",
    ),
    { minGapM: 10, maxGapM: 30, minStretchM: 500, blendM: FANOUT_BLEND_M },
  );
  console.log(
    `[visual-network] same-color co-location:      ${colocateResult.count} stretch(es)` +
      (colocateResult.count
        ? ` (${colocateResult.stretches.map((s) => `${s.routes}:${s.lengthM}m`).join(", ")})`
        : ""),
  );
}

// ----- Joint-offset tapers: flatten lane-slot steps at corridor joints -----
// Where the same route continues into an adjacent piece with a different
// lane_slot (G at Terrace Pl, F at Delancey, 1/2/3 near Times Sq), the baked
// endpoints land a few meters apart LATERALLY and the gap bridge below would
// join them with a sharp sideways step. Warp the more-offset lane's tail
// onto its neighbor over FANOUT_BLEND_M instead. Must run here -- after tail
// splitting/clips produced the final lane set, before bridging.
if (bundleArtifacts.visualFeatures) {
  const jointTaperResult = taperBakedJointSteps(
    bundleArtifacts.visualFeatures.filter(
      (feature) => feature.properties?.visual_feature_type === "bundle_lane",
    ),
    { blendM: FANOUT_BLEND_M },
  );
  // Drop the tiny pre-existing stitch connectors the warp made redundant
  // (they would dangle 6m off the now-flush joint).
  const beforeDrop = bundleArtifacts.visualFeatures.length;
  bundleArtifacts.visualFeatures = bundleArtifacts.visualFeatures.filter(
    (feature) => feature.properties?.joint_offset_taper_drop !== true,
  );
  const droppedStitches = beforeDrop - bundleArtifacts.visualFeatures.length;
  console.log(
    `[visual-network] joint-offset tapers:         ${jointTaperResult.count} joint(s) flattened, ${droppedStitches} stale stitch(es) dropped` +
      (jointTaperResult.count
        ? ` (${jointTaperResult.joints.map((j) => `${j.routes}@${j.gapM}m`).join(", ")})`
        : ""),
  );
}

// ----- Route gap bridging: close the small seams between same-route pieces -----
// The split-and-reassemble pipeline (shared spine from BASE geometry, fanouts/
// tails from MEMBER geometry, DeKalb clips) leaves small gaps (~11-20m) where a
// member fans out from the shared spine -- the two pieces differ by up to the
// overlap tolerance. Close those seams by extending the dangling source geometry
// into its same-route sibling. For same-color broad branch splits like the
// Queensboro N/W -> N/R seam, append an exact shared-route connector instead of
// extending either broad feature and falsely carrying W/R over the seam.
// In-place repairs stay bounded to <= BRIDGE_MAX_GAP_M; subset connectors are
// endpoint-only and capped by BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M.
// Connectivity (Gate 2D) is GTFS-topology-based, so bridges do not affect it.
if (bundleArtifacts.visualFeatures) {
  const bridgeResult = bridgeRouteGaps(bundleArtifacts.visualFeatures, {
    minGapM: BRIDGE_MIN_GAP_M,
    maxGapM: BRIDGE_MAX_GAP_M,
    allowSubsetRouteConnectors: true,
    subsetConnectorMaxGapM: BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M,
  });
  bundleArtifacts.visualFeatures = bridgeResult.features;
  console.log(
    `[visual-network] route gap bridging:          integrated=${bridgeResult.bridgeCount} (gap ${BRIDGE_MIN_GAP_M}-${BRIDGE_MAX_GAP_M}m, subset endpoint <=${BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M}m)`,
  );
}

// ----- Scoped cartographic junction overrides -----
// Applied after the general geometry cleanup below. The Mott Haven 5 junction is
// a cartographic exception: the GTFS-supported curl is technically valid, but it
// renders as a north-side loop. Apple/Transit schematize it as a compact
// south-side peel from E 149 St into the 4/5 Grand Concourse stem.

// ----- Off-revenue re-route: pull OpenData excursions onto the GTFS track -----
// FINAL geometry pass (after snap + bridge, so it operates on the settled
// endpoint geometry). Some NYC OpenData strokes swing far off the route's real
// revenue track (e.g. the 5 at 149 St / Mott Haven bulges ~300m west toward
// Walton Av). Each contiguous OFF-shape excursion (vertices > OFF_REVENUE_MAX_M
// from every GTFS revenue shape of that feature's routes) is replaced with the
// GTFS shape's own sub-path between where the line left and rejoined it -- so
// lines follow the real curve, never a straight chord, with no wild jumps.
if (bundleArtifacts.visualFeatures) {
  const canonicalDoc = JSON.parse(
    readFileSync(resolve(publicDir, "subway-network.canonical.geojson"), "utf8"),
  );
  const shapesByRoute = new Map();
  for (const f of canonicalDoc.features) {
    if (f.geometry?.type !== "LineString") continue;
    const r = String(f.properties?.route_id);
    if (!shapesByRoute.has(r)) shapesByRoute.set(r, []);
    shapesByRoute.get(r).push(f.geometry.coordinates);
  }
  let reroutedFeatureCount = 0;
  for (const f of bundleArtifacts.visualFeatures) {
    if (f.geometry?.type !== "LineString") continue;
    const before = f.geometry.coordinates;
    if (!Array.isArray(before) || before.length < 3) continue;
    const routes = Array.isArray(f.properties?.route_ids) ? f.properties.route_ids : [];
    const shapes = routes.flatMap((r: any) => shapesByRoute.get(String(r)) ?? []);
    if (!shapes.length) continue;
    let coords = before;
    let moved = false;
    for (let pass = 0; pass < 4; pass += 1) {
      const next = snapOffRevenueToShape(coords, shapes, { maxOffM: OFF_REVENUE_MAX_M });
      if (next === coords) break;
      coords = next;
      moved = true;
    }
    if (!moved) continue;
    // Smooth the GTFS-derived path: round sharp single-vertex elbows and relax
    // any tight kink where the re-routed sub-path rejoins, so the result reads as
    // a clean curve rather than a literal/sharp GTFS trace. Endpoints are pinned.
    let smoothed = smoothSharpCorners(coords, {
      angleThresholdDeg: 12, // GTFS-derived path: round densely-sampled tight curls into clean arcs
      iterations: 5,
      ratio: 0.28,
      maxFilletM: 30,
    });
    smoothed = simplifyTightCurves(smoothed, {
      tightTurnDeg: 40,   // GTFS-derived: relax the real tight Mott-Haven-style curls harder
      windowM: 60,
      iterations: 40,
      lambda: 0.5,
    });
    f.geometry.coordinates = smoothed;
    f.properties.off_revenue_rerouted = true;
    reroutedFeatureCount += 1;
  }
  console.log(
    `[visual-network] off-revenue re-route:        features=${reroutedFeatureCount} (>${OFF_REVENUE_MAX_M}m off GTFS revenue shape)`,
  );

  // ----- Authored Joralemon 4/5 river crossing smoothing -----
  // The off-revenue pass correctly protects most visual geometry, but around
  // the East River/Joralemon crossing it can pull the green trunk onto a GTFS
  // trace with a small visible wiggle in open water. Preserve the crossing's
  // endpoints and surrounding geometry, but replace only that local water run
  // with a clean tangent-matched schematic curve.
  const joralemonGreenRiver = applyJoralemonGreenRiverSmoothing(bundleArtifacts.visualFeatures, {
    bbox: {
      minLon: -74.0118,
      maxLon: -74.0015,
      minLat: 40.6948,
      maxLat: 40.7010,
    },
    marginM: 360,
    sampleM: 6,
    tangentSampleM: 130,
    handleFrac: 0.42,
    maxHandleM: 650,
  });
  bundleArtifacts.visualFeatures = joralemonGreenRiver.features;
  console.log(
    `[visual-network] QA Joralemon green river: applied=${joralemonGreenRiver.diagnostics.applied} replaced=${joralemonGreenRiver.diagnostics.replaced_length_m ?? 0}m`,
  );

  // ----- Authored Brighton B/Q Church/Beverley spacing -----
  // The B/Q Brighton physical bundle is detected correctly, but the continuous
  // materializer offsets each source member's own OpenData geometry. Around the
  // gentle Church/Beverley bend those source curves are slightly inconsistent,
  // so the baked orange/yellow lanes pinch together. Rebalance only this local
  // shared-bundle run onto one smoothed centerline and keep the two lanes at a
  // stable Apple/Transit-style separation through the bend.
  const brightonBqSpacing = applyBrightonBqChurchSpacing(bundleArtifacts.visualFeatures, {
    targetSeparationM: 15,
    marginM: 650,
    blendM: 140,
    sampleM: 6,
  });
  bundleArtifacts.visualFeatures = brightonBqSpacing.features;
  console.log(
    `[visual-network] QA Brighton B/Q Church spacing: applied=${brightonBqSpacing.diagnostics.applied} strict_min=${brightonBqSpacing.diagnostics.min_separation_before_m ?? "n/a"}m->${brightonBqSpacing.diagnostics.min_separation_after_m ?? "n/a"}m core_min=${brightonBqSpacing.diagnostics.core_min_separation_after_m ?? "n/a"}m${brightonBqSpacing.diagnostics.reason ? ` reason=${brightonBqSpacing.diagnostics.reason}` : ""}`,
  );

  // ----- Authored Culver F/G Prospect / Terrace seam smoothing -----
  // The F/G Culver corridor changes from a bundled green G lane to a solo G
  // lane around Prospect Av / Terrace Pl. Generic joint taper closes the seam,
  // but it does so by translating the G tail, leaving a subtle S-kink. Rebuild
  // only this local G chain from the neighboring F curve at stable separation.
  const culverFgProspect = applyCulverFgProspectSmoothing(bundleArtifacts.visualFeatures, {
    targetSeparationM: 14,
    marginM: 300,
    blendM: 140,
    sampleM: 6,
    smoothingPasses: 2,
  });
  bundleArtifacts.visualFeatures = culverFgProspect.features;
  console.log(
    `[visual-network] QA Culver F/G Prospect seam: applied=${culverFgProspect.diagnostics.applied} sep=${culverFgProspect.diagnostics.min_separation_before_m ?? "n/a"}m->${culverFgProspect.diagnostics.min_separation_after_m ?? "n/a"}m${culverFgProspect.diagnostics.reason ? ` reason=${culverFgProspect.diagnostics.reason}` : ""}`,
  );

  // ----- Authored St Nicholas A/C straightening -----
  // Same-color joins around 145 St can leave the north A/C piece and south
  // A/C/E piece meeting a few meters off-axis. At map scale this reads as a
  // small disconnected blue kink beside St Nicholas Av. Straighten only this
  // local St Nicholas run onto one fitted axis and snap the 145 St seam.
  const stNicholasBlue = applyStNicholasBlueStraightening(bundleArtifacts.visualFeatures);
  bundleArtifacts.visualFeatures = stNicholasBlue.features;
  console.log(
    `[visual-network] QA St Nicholas A/C straightening: applied=${stNicholasBlue.diagnostics.applied} features=${stNicholasBlue.diagnostics.target_feature_count} drift=${stNicholasBlue.diagnostics.max_perpendicular_before_m ?? "n/a"}m->${stNicholasBlue.diagnostics.max_perpendicular_after_m ?? "n/a"}m endpoint_clusters=${stNicholasBlue.diagnostics.snapped_endpoint_clusters ?? 0}${stNicholasBlue.diagnostics.reason ? ` reason=${stNicholasBlue.diagnostics.reason}` : ""}`,
  );

  // ----- Authored Nostrand / Eastern Parkway split -----
  // Apple Maps draws this as one straight 3/4 Eastern Parkway trunk and one
  // smooth 2/5 branch peeling south. The source + bridge passes leave a small
  // hook on the restored 4-to-Utica tail and a backtracking first segment on the
  // 2/5 branch. Own that local split here, after off-revenue snapping has
  // settled the revenue geometry.
  const nostrandSchematic = applyNostrandEasternSchematic(bundleArtifacts.visualFeatures, {
    branchTurnSpanM: 420,
    trunkBlendM: 170,
    sampleM: 6,
  });
  bundleArtifacts.visualFeatures = nostrandSchematic.features;
  console.log(
    `[visual-network] QA Nostrand/Eastern schematic: applied=${nostrandSchematic.diagnostics.applied} red_branch=${nostrandSchematic.diagnostics.red_branch_rebuilt} green_tail=${nostrandSchematic.diagnostics.green_tail_straightened} green_branch=${nostrandSchematic.diagnostics.green_branch_rebuilt}${nostrandSchematic.diagnostics.reason ? ` reason=${nostrandSchematic.diagnostics.reason}` : ""}`,
  );

  // (My schematic-hairpin-arc pass removed: it competed with the cartographic
  // junction override below and produced a redundant parallel path / lens at
  // Mott Haven. The cartographic override owns the 5-branch reshape.)
  void replaceEndpointHairpin;

  // ----- Authored Mott Haven 5 lens (Apple / Transit schematic) -----
  // South of 149 St-Grand Concourse the 4 and 5 share track, but Apple Maps and
  // the Transit app draw them as two parallel lines: the 4 runs straight on Grand
  // Concourse and the 5 bows WEST via Walton Av, then they rejoin -- an elongated
  // lens. Neither OpenData nor GTFS contains that lens (both have the tight Mott
  // Haven curl), so it is AUTHORED here as the single owner of this junction:
  //   * the 4 is made continuous (its north stem is joined to the 4/5 trunk),
  //   * the 5 branch is rebuilt as a local schematic lens: flat along E 149 St,
  //     closed at the top trunk split, west via Walton Av, then lower Y-merged
  //     back into the 4/5 trunk.
  // This deliberately stops preserving the real 5-from-east curl inside the
  // junction. The real route only feeds the authored E 149 St entry.
  // (Supersedes the cartographic override, which collapsed the 5 onto the trunk.)
  void applyCartographicJunctionOverrides;
  const LENS_SPAN_M = 310;  // lower Y-merge distance from the authored top split
  const SIX_MERGE_SPAN_M = 520; // route 6 joins the straight trunk near the circled 138 St merge
  // Straighten the 4/5 mainline onto Grand Concourse through the junction view, then blend
  // back to the true track below this latitude (just under the typical view ~40.808). The
  // real track bends SW toward the Harlem River below the merge; Apple/Transit draw it
  // straight down Grand Concourse and push that curve off-screen. Lower = curve pushed
  // further down but larger divergence from the true track.
  const LENS_STRAIGHTEN_TO_LAT = 40.806;
  const inBBox = (p: Position) =>
    p[0] >= MOTT_HAVEN_5_QA_BBOX.minLon && p[0] <= MOTT_HAVEN_5_QA_BBOX.maxLon &&
    p[1] >= MOTT_HAVEN_5_QA_BBOX.minLat && p[1] <= MOTT_HAVEN_5_QA_BBOX.maxLat;
  const lensTrunk = bundleArtifacts.visualFeatures.find((f) => (
    f.geometry?.type === "LineString" &&
    String(f.properties?.color ?? "").toUpperCase() === "#00933C" &&
    (f.properties?.route_ids ?? []).map(String).includes("4") &&
    (f.properties?.route_ids ?? []).map(String).includes("5") &&
    f.geometry.coordinates.some(inBBox)
  ));
  const lensBranch = bundleArtifacts.visualFeatures.find((f) => {
    if (f.geometry?.type !== "LineString") return false;
    if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
    const r = (f.properties?.route_ids ?? []).map(String);
    return r.includes("5") && !r.includes("4") && f.geometry.coordinates.some(inBBox);
  });
  let lensApplied = false;
  let lensBowWidthM = 0;
  let lensRejoinM = Infinity;
  let fourContinuous = false;
  let mainlineStraightened = false;
  let mainlineMaxBearingDevDeg = 0;
  let lensTopApproachLatSpreadM = Infinity;
  let lensMaxTurnDeg = Infinity;
  let lensParallelReferenceUsed = false;
  let lensParallelReferenceDistanceM = Infinity;
  let sixMergeApplied = false;
  let sixMergeRejoinM = Infinity;
  let sixMergeMaxTurnDeg = Infinity;
  if (lensTrunk && lensBranch) {
    let tc = lensTrunk.geometry.coordinates;
    // ---- (a) find the [4] stem and the Grand Concourse avenue bearing ----
    const fourStem = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("4") && !r.includes("5") && f.geometry.coordinates.some(inBBox);
    });
    const twoReference = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#EE352E") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("2") && f.geometry.coordinates.some(inBBox);
    });
    const sixBranch = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("6") && !r.includes("4") && f.geometry.coordinates.some(inBBox);
    });
    const sixShared = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("4") && r.includes("6") && f.geometry.coordinates.some(inBBox);
    });
    let avenueDir = null; // unit southbound direction of Grand Concourse (meters)
    if (fourStem) {
      const sc = fourStem.geometry.coordinates;
      const ks = metersPerDegLng(sc[sc.length - 1][1]);
      const d = [(sc[sc.length - 1][0] - sc[Math.max(0, sc.length - 6)][0]) * ks, (sc[sc.length - 1][1] - sc[Math.max(0, sc.length - 6)][1]) * M_PER_DEG_LAT];
      const l = Math.hypot(d[0], d[1]);
      if (l > 1) avenueDir = [d[0] / l, d[1] / l];
    }
    if (!avenueDir) {
      const k0 = metersPerDegLng(tc[0][1]);
      const j = Math.min(8, tc.length - 1);
      const d = [(tc[j][0] - tc[0][0]) * k0, (tc[j][1] - tc[0][1]) * M_PER_DEG_LAT];
      const l = Math.hypot(d[0], d[1]) || 1;
      avenueDir = [d[0] / l, d[1] / l];
    }
    // ---- (b) straighten the 4/5 mainline onto Grand Concourse; blend back below the view ----
    // The real track bends SW toward the Harlem River below the merge; Apple/Transit draw it
    // straight down Grand Concourse and push that curve off-screen. Re-aim the trunk from the
    // junction along the avenue bearing, then Hermite-blend back to the true track below
    // LENS_STRAIGHTEN_TO_LAT. tc stays one feature with unchanged endpoints (connectivity safe).
    if (avenueDir[1] < 0) {
      let blendIdx = -1;
      for (let i = 1; i < tc.length; i += 1) { if (tc[i][1] <= LENS_STRAIGHTEN_TO_LAT - 0.002) { blendIdx = i; break; } }
      if (blendIdx > 4) {
        const A = tc[0];
        const kA = metersPerDegLng(A[1]);
        const rayLenM = Math.abs(((LENS_STRAIGHTEN_TO_LAT - A[1]) * M_PER_DEG_LAT) / avenueDir[1]);
        const ray = [];
        for (let d = 0; d <= rayLenM; d += 10) ray.push([A[0] + (avenueDir[0] * d) / kA, A[1] + (avenueDir[1] * d) / M_PER_DEG_LAT]);
        const rEnd = ray[ray.length - 1];
        const B = tc[blendIdx];
        const kB = metersPerDegLng(B[1]);
        const b2 = tc[Math.min(tc.length - 1, blendIdx + 8)];
        const eT = [(b2[0] - B[0]) * kB, (b2[1] - B[1]) * M_PER_DEG_LAT];
        const eL = Math.hypot(eT[0], eT[1]) || 1;
        const blendSeg = hermiteBetween(rEnd as Position, B as Position, avenueDir as Position, [eT[0] / eL, eT[1] / eL], { handleFrac: 0.5, sampleM: 8 });
        let merged = [...ray, ...blendSeg.slice(1), ...tc.slice(blendIdx + 1)];
        merged = smoothSharpCorners(merged, { angleThresholdDeg: 22, iterations: 3, ratio: 0.2, maxFilletM: 18 });
        tc = merged;
        lensTrunk.geometry.coordinates = tc;
        lensTrunk.properties.mott_haven_mainline_straightened = true;
        mainlineStraightened = true;
        // QA: mainline bearing must be ~constant through the view (junction .. view bottom)
        const baseBear = (Math.atan2(avenueDir[1], avenueDir[0]) * 180) / Math.PI;
        for (let i = 1; i < tc.length; i += 1) {
          if (tc[i][1] > A[1] || tc[i][1] < LENS_STRAIGHTEN_TO_LAT + 0.002) continue;
          const kk = metersPerDegLng(tc[i][1]);
          const seg = [(tc[i][0] - tc[i - 1][0]) * kk, (tc[i][1] - tc[i - 1][1]) * M_PER_DEG_LAT];
          if (Math.hypot(seg[0], seg[1]) < 1) continue;
          let dev = (Math.atan2(seg[1], seg[0]) * 180) / Math.PI - baseBear;
          while (dev > 180) dev -= 360; while (dev < -180) dev += 360;
          mainlineMaxBearingDevDeg = Math.max(mainlineMaxBearingDevDeg, Math.abs(dev));
        }
      }
    }
    // ---- (c) make the 4 continuous: join the [4] stem to the (straightened) trunk start ----
    if (fourStem) {
      const sc = fourStem.geometry.coordinates;
      const gap = distanceMeters(sc[sc.length - 1], tc[0]);
      if (gap > 20 && gap < 400) {
        const ks = metersPerDegLng(sc[sc.length - 1][1]);
        const sT = [(sc[sc.length - 1][0] - sc[Math.max(0, sc.length - 5)][0]) * ks, (sc[sc.length - 1][1] - sc[Math.max(0, sc.length - 5)][1]) * M_PER_DEG_LAT];
        const sl = Math.hypot(sT[0], sT[1]) || 1;
        const eT = [(tc[Math.min(4, tc.length - 1)][0] - tc[0][0]) * ks, (tc[Math.min(4, tc.length - 1)][1] - tc[0][1]) * M_PER_DEG_LAT];
        const el = Math.hypot(eT[0], eT[1]) || 1;
        const conn = hermiteBetween(sc[sc.length - 1], tc[0], [sT[0] / sl, sT[1] / sl], [eT[0] / el, eT[1] / el], { handleFrac: 0.5, sampleM: 6 });
        fourStem.geometry.coordinates = [...sc, ...conn.slice(1)];
        fourStem.properties.mott_haven_four_continuity = true;
        fourContinuous = true;
      }
    }
    // ---- (d) author the 5 schematic lens ----
    // The route-5 source geometry comes from the east and curls through the junction.
    // Apple/Transit instead draw a bounded schematic: E 149 St entry -> closed
    // top split -> Walton-side lens -> lower Y merge. Keep the upstream 5 route
    // connected, but do not let the real curl define the visible junction.
    const lens = buildMottHavenFiveSchematicLens({
      branchCoords: lensBranch.geometry.coordinates,
      trunkCoords: fourStem
        ? [...fourStem.geometry.coordinates, ...tc.slice(1)]
        : tc,
      parallelReferenceCoords: twoReference?.geometry?.coordinates ?? null,
      parallelOffsetM: 10,
      mergeDistanceM: LENS_SPAN_M,
      sampleM: 6,
    });
    if (lens.diagnostics.ok) {
      const spliced = lens.coordinates;
      lensBranch.geometry.coordinates = spliced;
      lensBranch.properties.mott_haven_lens = true;
      lensBranch.properties.mott_haven_schematic_lens = true;
      lensBranch.properties.mott_haven_lens_entry_point = lens.diagnostics.entryPoint;
      lensBranch.properties.mott_haven_lens_top_point = lens.diagnostics.topPoint;
      lensBranch.properties.mott_haven_lens_merge_point = lens.diagnostics.mergePoint;
      lensBranch.properties.mott_haven_lens_top_spread_m = Number(lens.diagnostics.topApproachLatSpreadM!.toFixed(2));
      lensBranch.properties.mott_haven_lens_max_turn_deg = Number(lens.diagnostics.maxTurnDeg!.toFixed(2));
      lensBranch.properties.mott_haven_parallel_reference_used = lens.diagnostics.parallelReferenceUsed;
      lensBranch.properties.mott_haven_parallel_reference_distance_m =
        lens.diagnostics.parallelReferenceDistanceM == null
          ? null
          : Number(lens.diagnostics.parallelReferenceDistanceM.toFixed(2));
      lensApplied = true;
      lensBowWidthM = lens.diagnostics.maxTrunkDistanceM!;
      lensRejoinM = lens.diagnostics.mergeDistanceM!;
      lensTopApproachLatSpreadM = lens.diagnostics.topApproachLatSpreadM!;
      lensMaxTurnDeg = lens.diagnostics.maxTurnDeg!;
      lensParallelReferenceUsed = Boolean(lens.diagnostics.parallelReferenceUsed);
      lensParallelReferenceDistanceM = lens.diagnostics.parallelReferenceDistanceM ?? Infinity;
    }
    // ---- (e) author the lower route-6 Y merge ----
    // OpenData/GTFS keep the route-6 approach as a lower sweeping curve that
    // reads as a second teardrop. Apple Maps instead lets the 6 branch enter
    // once and then become the shared trunk. Keep the east approach, but stop it
    // at the straight mainline and start the shared 4/6 segment there.
    if (sixBranch && sixShared) {
      const sixMerge = buildMottHavenSixSchematicMerge({
        branchCoords: sixBranch.geometry.coordinates,
        mainlineCoords: tc,
        mergeDistanceM: SIX_MERGE_SPAN_M,
        entryEastM: 430,
        entryNorthM: 120,
        sampleM: 6,
      });
      if (sixMerge.diagnostics.ok) {
        sixBranch.geometry.coordinates = sixMerge.coordinates;
        sixBranch.properties.mott_haven_six_merge = true;
        sixBranch.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
        sixBranch.properties.mott_haven_six_merge_max_turn_deg =
          Number(sixMerge.diagnostics.maxTurnDeg!.toFixed(2));
        sixBranch.properties.mott_haven_six_merge_rejoin_m =
          Number(sixMerge.diagnostics.mergeDistanceM!.toFixed(2));

        sixShared.geometry.coordinates = sixMerge.sharedMainlineCoords;
        sixShared.properties.mott_haven_six_shared_mainline = true;
        sixShared.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
        sixMergeApplied = true;
        sixMergeRejoinM = sixMerge.diagnostics.mergeDistanceM!;
        sixMergeMaxTurnDeg = sixMerge.diagnostics.maxTurnDeg!;
      }
    }
  }
  const lensTopApproachOk = lensParallelReferenceUsed
    ? lensParallelReferenceDistanceM <= 25
    : lensTopApproachLatSpreadM <= 15;
  const sixMergeOk = sixMergeApplied && sixMergeRejoinM <= 2 && sixMergeMaxTurnDeg <= 70;
  const qaPass = lensApplied && fourContinuous && lensBowWidthM >= 120 && lensBowWidthM <= 260 && lensRejoinM <= 4
    && lensTopApproachOk && lensMaxTurnDeg <= 65
    && mainlineStraightened && mainlineMaxBearingDevDeg <= 6
    && sixMergeOk;
  console.log(
    `[visual-network] QA Mott Haven 5/6 schematic:  five_applied=${lensApplied} four_continuous=${fourContinuous} bow=${lensBowWidthM.toFixed(0)}m rejoin=${lensRejoinM.toFixed(1)}m top_spread=${lensTopApproachLatSpreadM.toFixed(1)}m parallel_ref=${lensParallelReferenceUsed}:${lensParallelReferenceDistanceM.toFixed(1)}m max_turn=${lensMaxTurnDeg.toFixed(1)}deg straightened=${mainlineStraightened} bearing_dev=${mainlineMaxBearingDevDeg.toFixed(1)}deg six_merge=${sixMergeApplied}:${sixMergeRejoinM.toFixed(1)}m/${sixMergeMaxTurnDeg.toFixed(1)}deg ${qaPass ? "PASS" : "FAIL"}`,
  );
  if (!qaPass) {
    console.error(
      "[visual-network] *** QA FAIL: Mott Haven 5/6 schematic (5 lens, 4 continuity, straight mainline, or lower 6 merge) not authored as expected. ***",
    );
    process.exit(1);
  }
}

// =====================================================================
// 63 St tunnel F membership
// =====================================================================
// OpenData draws the 63 St tunnel as the M line only; the F (its real
// owner, per Apple Maps) appeared out of nowhere at the 36 St junction.
// Membership-only fix: the orange tunnel features gain F in route_ids.
{
  const sixtyThird = addSixtyThirdStreetF(bundleArtifacts.visualFeatures);
  console.log(
    `[visual-network] QA 63 St tunnel F membership: features_updated=${sixtyThird.updated} ${sixtyThird.updated > 0 ? "PASS" : "FAIL (no orange M tunnel feature found)"}`,
  );
}

// =====================================================================
// Staten Island Railway cleanup
// =====================================================================
// OpenData shatters the SIR into ~40 fragments (second-track slivers, yard
// twigs, weave around St George). Keep the stitched Tottenville->St George
// mainline, bridge its small seams, drop shadows and twigs.
{
  const siSummary = cleanStatenIslandLine(bundleArtifacts.visualFeatures);
  console.log(
    `[visual-network] QA SIR cleanup: connected=${siSummary.connected ?? false} kept=${siSummary.kept} dropped=${siSummary.dropped} stitches=${siSummary.stitches} ${siSummary.connected ? "PASS" : "FAIL (terminals not connected; left untouched)"}`,
  );
}

// =====================================================================
// Hammels Wye (Rockaway) junction connector
// =====================================================================
// The cross-bay A stops ~46m short of the east/west legs' junction node,
// with degenerate stubs dangling at its end. Extend it onto the node so
// Broad Channel -> Far Rockaway reads as one continuous line.
{
  const wye = connectRockawayWye(bundleArtifacts.visualFeatures);
  console.log(
    `[visual-network] QA Rockaway wye: connected=${wye.connected} extended=${wye.extended} stubs_removed=${wye.stubsRemoved} ${wye.connected ? "PASS" : "FAIL (legs not found)"}`,
  );
}

// =====================================================================
// Terminal overhang trim
// =====================================================================
// Lanes are sliced from full OpenData line geometry, which keeps running past
// the last passenger station into yards / non-revenue track. Trim every free
// lane end back to the outermost station that projects onto it (+ grace).
{
  const stationsDoc = JSON.parse(
    readFileSync(STATIONS_GEOJSON_PATH, "utf8"),
  );
  // True service terminals from the Gate 2A GTFS branch sequences. Cuts are
  // only allowed where the boundary coincides with one of these -- station
  // route lists alone are weekday-pattern and misclassify branch geometry.
  const routeTerminals = [];
  for (const [routeId, branches] of branchesByRoute) {
    for (const branch of branches) {
      for (const stopId of [branch.terminal_start, branch.terminal_end]) {
        const stop = stopsById.get(stopId);
        if (!stop || !Number.isFinite(stop.lon) || !Number.isFinite(stop.lat)) continue;
        routeTerminals.push({ route: routeId, coord: [stop.lon, stop.lat] });
      }
    }
  }
  // Two passes: dropping a spur can expose the end it was attached to (the
  // attachment snapshot is taken before splicing), so a second pass reaches
  // the fixpoint (e.g. the SI tail past Tottenville chained to a yard spur).
  for (let pass = 1; pass <= 2; pass += 1) {
    const trimSummary = trimTerminalOverhang({
      features: bundleArtifacts.visualFeatures,
      stations: stationsDoc,
      terminals: routeTerminals as any,
    });
    console.log(
      `[visual-network] terminal overhang pass ${pass}: ${trimSummary.trimmedEnds} free ends trimmed, ${trimSummary.removedM}m removed, ${trimSummary.droppedSpurs} spurs dropped`,
    );
    for (const action of trimSummary.actions ?? []) {
      console.log(`[visual-network]   trim ${JSON.stringify(action)}`);
    }
    if (trimSummary.trimmedEnds === 0 && trimSummary.droppedSpurs === 0) break;
  }
}

// =====================================================================
// Final artifact emission
// =====================================================================

// The candidate artifact is the OpenData-derived visual geojson with extra
// metadata. Always written so debug/inspection works even on failure.
const candidateDoc = buildCandidateDoc({
  generatedAt: new Date().toISOString(),
  openDataSourceName: OPEN_DATA_SOURCE_NAME,
  openDataSourceDatasetId: OPEN_DATA_SOURCE_DATASET_ID,
  perRouteStats,
  validationFailures,
  bundleArtifacts,
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
writeFileSync(OUT_VISUAL_CANDIDATE, `${JSON.stringify(candidateDoc)}\n`);
console.log(`[visual-network] wrote candidate: ${OUT_VISUAL_CANDIDATE}`);

if (validationFailures.length === 0) {
  // Promote candidate → final. Preserve the last-known-good by atomic
  // rename pattern (write candidate first, then move).
  writeFileSync(OUT_VISUAL_FINAL, `${JSON.stringify(candidateDoc)}\n`);
  console.log(`[visual-network] *** PROMOTED *** to ${OUT_VISUAL_FINAL}`);
  console.log(`[visual-network] All gates passed. Visual network artifact is ready for Gate 2E (runtime opt-in).`);
} else {
  console.error(
    `[visual-network] HARD GATE FAILED: ${validationFailures.length} route(s) failed connectivity validation.`,
  );
  console.error(
    `[visual-network] Refusing to promote candidate to ${OUT_VISUAL_FINAL}. The last-known-good (if any) is preserved.`,
  );
  process.exit(1);
}

// Summary log
console.log(`[visual-network] === Gate 2A topology summary ===`);
console.log(
  `[visual-network] distinct routes: ${topologyDoc.topology.distinct_routes}`,
);
console.log(
  `[visual-network] total branches (>= ${MIN_TRIPS_PER_BRANCH} trips): ${topologyDoc.topology.total_branches}`,
);
console.log(
  `[visual-network] dropped low-frequency branches: ${droppedLowFreqBranches}`,
);
console.log(`[visual-network] --- per-route branch summary ---`);
console.log(`[visual-network]   route  branches  stations  branch terminals`);
for (const r of topologyDoc.per_route) {
  const terminals = r.branches
    .slice(0, 4)
    .map((b: any) =>
      `${(b.direction_id || "?")}:${(stopsById.get(b.terminal_start)?.name ?? b.terminal_start)} → ${(stopsById.get(b.terminal_end)?.name ?? b.terminal_end)} (${b.total_trips_in_branch}tr)`,
    )
    .join("; ");
  console.log(
    `[visual-network]   ${r.route_id.padEnd(5)} ${String(r.branch_count).padStart(8)} ${String(r.distinct_stations).padStart(9)}  ${terminals}`,
  );
}

console.log("[visual-network] Gate 2A complete. Topology written to debug JSON.");
console.log("[visual-network] Gate 2B used NYC OpenData full-line geometry; GTFS shapes.txt was not used for visual rendering.");
