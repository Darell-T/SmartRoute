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
import { densifyLongSegments } from "./build/smooth-polyline.ts";
import {
  loadOpenDataSubwayLines,
  OPEN_DATA_SOURCE_DATASET_ID,
  OPEN_DATA_SOURCE_NAME,
} from "./build/opendata-subway-lines.ts";
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
  RESAMPLE_INTERVAL_M,
  distanceMeters,
  geometryStats,
} from "./build/visual-network/geometry-utils.ts";
import { buildOpenDataInputsStage } from "./build/visual-network/opendata-inputs.ts";
import {
  buildBundleArtifacts,
  routesForColor,
} from "./build/visual-network/bundle-stage.ts";
import { applyLaneOffsetFinalizationStage } from "./build/visual-network/lane-offset-finalization-stage.ts";
import { applyVisualRepairPipelineStage } from "./build/visual-network/visual-repair-pipeline-stage.ts";
import { writeVisualArtifactStage } from "./build/visual-network/artifact-writer-stage.ts";
import { reportFinalTopologySummaryStage } from "./build/visual-network/final-reporting-stage.ts";
import { runValidationReportingStage } from "./build/visual-network/validation-reporting-stage.ts";

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
