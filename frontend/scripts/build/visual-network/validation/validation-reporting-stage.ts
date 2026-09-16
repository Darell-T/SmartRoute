import { writeFileSync } from "node:fs";
import { REMAINING_UNBUNDLED_CORRIDORS } from "../output/artifact-metadata.ts";
import {
  buildRouteIncidentCounts,
  buildVisualAnomalyRecords,
  buildVisualRouteIncidentCounts,
} from "../shared/diagnostics.ts";
import { geometryStats } from "../shared/geometry-utils.ts";
import { routeIdsOf } from "../shared/route-config.ts";
import type { BundleArtifacts, LineFeature, PointFeat } from "../shared/types.ts";

export type ValidationReportingPaths = {
  corridorsGeoJson: string;
  corridorsJson: string;
  junctionAnchorsGeoJson: string;
  junctionSnapsGeoJson: string;
  materializedBundlesGeoJson: string;
  materializedBundleFanoutsGeoJson: string;
  materializedBundleSplitsGeoJson: string;
  materializedBundleDefectsGeoJson: string;
  bundlesGeoJson: string;
  bundleLanesGeoJson: string;
  bundleGapsGeoJson: string;
  missingRouteLanesGeoJson: string;
  renderLaneContinuityJson: string;
  anomaliesGeoJson: string;
  anomaliesJson: string;
  routeComponentsJson: string;
};

export type ValidationReportingParameters = {
  resampleIntervalM: number;
  hausdorffMaxM: number;
  overlapMinRatio: number;
  tangentMaxDiffDeg: number;
  containmentAvgDistanceMaxM: number;
  containmentOverlapMinRatio: number;
  gridCellM: number;
  junctionSnapMaxM: number;
  maxSegmentAnomalyM: number;
  sparseLongSliceM: number;
  projectionAnomalyM: number;
  openDataMinFragmentLengthM: number;
};

export type RouteConnectivityStat = {
  route_id: string;
  edge_count: number;
  stop_count: number;
  component_count: number;
  largest_component_size: number;
  largest_component_ratio: number;
  components: Array<{ size: number; sample_stop_ids: string[] }>;
  passed: boolean;
};

export type RouteConnectivityFailure = {
  route_id: string;
  component_count: number;
  largest_component_ratio: number;
  total_stops: number;
  largest_size: number;
  sample_component_sizes: number[];
};

export type ValidationReportingStageResult = {
  perRouteStats: RouteConnectivityStat[];
  validationFailures: RouteConnectivityFailure[];
};

type JunctionSnapDiagnostics = {
  anchorFeatures: PointFeat[];
  snapFeatures: LineFeature[];
};

type LaneChainDiagnostics = {
  lane_group_count: number;
  chain_slot_feature_count: number;
};

type CorridorRow = {
  is_shared: boolean;
  route_ids: string[];
};

type ValidationReportingStop = {
  lon: number;
  lat: number;
  name: string;
};

type EdgeLookup = {
  get(edgeId: string): LineFeature | undefined;
};

type MissingRouteLaneFeature = {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: {
    marker_type: "missing_route_lane";
    stop_id: string;
    stop_name: string;
    route_id: string;
    expected_incident_edges: number;
    visual_incident_corridors: number;
    visual_corridor_ids: string[];
    reason: "route_expected_to_continue_at_junction";
  };
};

type ValidationReportingStageInput = {
  edgeFeatures: LineFeature[];
  corridorFeatures: LineFeature[];
  corridorRows: CorridorRow[];
  pairsConsidered: number;
  pairsMatched: number;
  matchedPairs: unknown[];
  junctionSnapDiagnostics: JunctionSnapDiagnostics;
  laneChainDiagnostics: LaneChainDiagnostics;
  bundleArtifacts: BundleArtifacts;
  edgeById: EdgeLookup;
  stopsById: Map<string, ValidationReportingStop>;
  paths: ValidationReportingPaths;
  parameters: ValidationReportingParameters;
};

const REQUIRED_TRUNKS = [
  ["1", "2", "3"],
  ["4", "5", "6"],
  ["A", "C", "E"],
  ["B", "D", "F", "M"],
  ["N", "Q", "R", "W"],
];

function writeCorridorArtifacts(
  corridorFeatures: LineFeature[],
  corridorRows: CorridorRow[],
  edgeFeatures: LineFeature[],
  pairsConsidered: number,
  pairsMatched: number,
  matchedPairs: unknown[],
  paths: ValidationReportingPaths,
  parameters: ValidationReportingParameters,
): void {
  corridorFeatures.sort((left, right) =>
    String(left.properties.corridor_id ?? "").localeCompare(
      String(right.properties.corridor_id ?? ""),
    ),
  );

  writeFileSync(
    paths.corridorsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs Gate 2C",
        parameters: {
          resample_interval_m: parameters.resampleIntervalM,
          hausdorff_max_m: parameters.hausdorffMaxM,
          overlap_min_ratio: parameters.overlapMinRatio,
          tangent_max_diff_deg: parameters.tangentMaxDiffDeg,
          containment_avg_distance_max_m: parameters.containmentAvgDistanceMaxM,
          containment_overlap_min_ratio: parameters.containmentOverlapMinRatio,
          grid_cell_m: parameters.gridCellM,
        },
      },
      features: corridorFeatures,
    })}\n`,
  );
  writeFileSync(
    paths.corridorsJson,
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
}

function writeJunctionArtifacts(
  junctionSnapDiagnostics: JunctionSnapDiagnostics,
  paths: ValidationReportingPaths,
  junctionSnapMaxM: number,
): void {
  writeFileSync(
    paths.junctionAnchorsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs Gate 2G",
        parameters: {
          junction_snap_max_m: junctionSnapMaxM,
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
    paths.junctionSnapsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs Gate 2G",
        parameters: {
          junction_snap_max_m: junctionSnapMaxM,
        },
        summary: {
          snap_count: junctionSnapDiagnostics.snapFeatures.length,
        },
      },
      features: junctionSnapDiagnostics.snapFeatures,
    })}\n`,
  );
}

function writeBundleArtifacts(bundleArtifacts: BundleArtifacts, paths: ValidationReportingPaths): void {
  writeFileSync(
    paths.bundlesGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs Gate 2H",
        summary: {
          bundle_count: bundleArtifacts.bundleFeatures.length,
          corridors_converted_to_bundle_geometry:
            bundleArtifacts.bundleFeatures.length,
          remaining_unbundled_corridors: REMAINING_UNBUNDLED_CORRIDORS,
        },
      },
      features: bundleArtifacts.bundleFeatures,
    })}\n`,
  );
  writeFileSync(
    paths.bundleLanesGeoJson,
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
    paths.bundleGapsGeoJson,
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
}

function logGate2cSummary(
  edgeFeatures: LineFeature[],
  corridorFeatures: LineFeature[],
  corridorRows: CorridorRow[],
  pairsConsidered: number,
  pairsMatched: number,
  junctionSnapDiagnostics: JunctionSnapDiagnostics,
  laneChainDiagnostics: LaneChainDiagnostics,
  bundleArtifacts: BundleArtifacts,
  paths: ValidationReportingPaths,
): void {
  console.log(`[visual-network] === Gate 2C corridor summary ===`);
  console.log(`[visual-network] edges in:                ${edgeFeatures.length}`);
  console.log(`[visual-network] candidate pairs:          ${pairsConsidered}`);
  console.log(`[visual-network] matched pairs:            ${pairsMatched}`);
  console.log(`[visual-network] corridors out:            ${corridorFeatures.length}`);
  const sharedCorridors = corridorRows.filter((row) => row.is_shared);
  const multiRouteCorridors = corridorRows.filter((row) => row.route_ids.length > 1);
  console.log(`[visual-network] shared (>1 edge member):  ${sharedCorridors.length}`);
  console.log(`[visual-network] multi-route (>1 route):   ${multiRouteCorridors.length}`);
  console.log(`[visual-network] wrote ${paths.corridorsGeoJson}`);
  console.log(`[visual-network] wrote ${paths.corridorsJson}`);
  console.log(`[visual-network] wrote ${paths.junctionAnchorsGeoJson}`);
  console.log(`[visual-network] wrote ${paths.junctionSnapsGeoJson}`);
  console.log(`[visual-network] wrote ${paths.materializedBundlesGeoJson}`);
  console.log(`[visual-network] wrote ${paths.materializedBundleFanoutsGeoJson}`);
  console.log(`[visual-network] wrote ${paths.materializedBundleSplitsGeoJson}`);
  console.log(`[visual-network] wrote ${paths.materializedBundleDefectsGeoJson}`);
  console.log(`[visual-network] wrote ${paths.bundlesGeoJson}`);
  console.log(`[visual-network] wrote ${paths.bundleLanesGeoJson}`);
  console.log(`[visual-network] wrote ${paths.bundleGapsGeoJson}`);
  console.log(
    `[visual-network] junction anchors: ${junctionSnapDiagnostics.anchorFeatures.length}, snaps: ${junctionSnapDiagnostics.snapFeatures.length}`,
  );
  console.log(
    `[visual-network] lane groups: ${laneChainDiagnostics.lane_group_count}, chain-slot features: ${laneChainDiagnostics.chain_slot_feature_count}`,
  );
  console.log(
    `[visual-network] bundles: ${bundleArtifacts.bundleFeatures.length}, bundle lanes: ${bundleArtifacts.bundleLaneFeatures.length}, unbundled corridors: ${REMAINING_UNBUNDLED_CORRIDORS}, bundle gaps: ${bundleArtifacts.bundleGapFeatures.length}`,
  );
}

function logRequiredTrunkCheck(corridorRows: CorridorRow[]): void {
  console.log(`[visual-network] --- Required shared-trunk check ---`);
  for (const trunk of REQUIRED_TRUNKS) {
    const hits = corridorRows.filter((row) => {
      const carried = new Set(row.route_ids);
      return trunk.every((routeId) => carried.has(routeId));
    });
    console.log(
      `[visual-network]   ${trunk.join("/").padEnd(10)} corridors carrying ALL: ${hits.length}`,
    );
  }
}

function collectMissingRouteLaneFeatures(
  edgeFeatures: LineFeature[],
  corridorFeatures: LineFeature[],
  edgeById: EdgeLookup,
  stopsById: Map<string, ValidationReportingStop>,
): MissingRouteLaneFeature[] {
  const expectedRouteIncidents = buildRouteIncidentCounts(edgeFeatures, true);
  const visualRouteIncidents = buildVisualRouteIncidentCounts(corridorFeatures, edgeById);
  const missingRouteLaneFeatures: MissingRouteLaneFeature[] = [];

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

  return missingRouteLaneFeatures;
}

function missingLaneNameCount(features: MissingRouteLaneFeature[], routeId: string, pattern: RegExp): number {
  return features.filter(
    (feature) => feature.properties.route_id === routeId && pattern.test(feature.properties.stop_name),
  ).length;
}

function writeContinuityDiagnostics(
  corridorFeatures: LineFeature[],
  missingRouteLaneFeatures: MissingRouteLaneFeature[],
  junctionSnapDiagnostics: JunctionSnapDiagnostics,
  laneChainDiagnostics: LaneChainDiagnostics,
  bundleArtifacts: BundleArtifacts,
  paths: ValidationReportingPaths,
): void {
  const qProspectBrightonMissingCount = missingLaneNameCount(
    missingRouteLaneFeatures,
    "Q",
    /Prospect|Brighton|7 Av|Atlantic|DeKalb/,
  );
  const route2FlatbushEasternMissingCount = missingLaneNameCount(
    missingRouteLaneFeatures,
    "2",
    /Flatbush|Nostrand|Eastern|Franklin|President|Sterling|Winthrop|Church/,
  );

  const missingRouteLaneGeoJson = {
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2G",
      summary: {
        missing_route_lane_count: missingRouteLaneFeatures.length,
        q_prospect_brighton_missing_count: qProspectBrightonMissingCount,
        route_2_flatbush_eastern_missing_count: route2FlatbushEasternMissingCount,
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
      q_prospect_brighton_missing_count: qProspectBrightonMissingCount,
      route_2_flatbush_eastern_missing_count: route2FlatbushEasternMissingCount,
      junction_anchor_count: junctionSnapDiagnostics.anchorFeatures.length,
      junction_snap_count: junctionSnapDiagnostics.snapFeatures.length,
      lane_group_count: laneChainDiagnostics.lane_group_count,
      chain_slot_feature_count: laneChainDiagnostics.chain_slot_feature_count,
      bundle_count: bundleArtifacts.bundleFeatures.length,
      bundled_render_lane_count: bundleArtifacts.bundleLaneFeatures.length,
      remaining_unbundled_corridors: REMAINING_UNBUNDLED_CORRIDORS,
    },
    missing_route_lane_sample: missingRouteLaneFeatures.slice(0, 50).map((feature) => ({
      stop_name: feature.properties.stop_name,
      route_id: feature.properties.route_id,
      expected_incident_edges: feature.properties.expected_incident_edges,
      visual_incident_corridors: feature.properties.visual_incident_corridors,
      visual_corridor_ids: feature.properties.visual_corridor_ids,
    })),
  };

  writeFileSync(paths.missingRouteLanesGeoJson, `${JSON.stringify(missingRouteLaneGeoJson)}\n`);
  writeFileSync(paths.renderLaneContinuityJson, `${JSON.stringify(renderLaneContinuityJson, null, 2)}\n`);
  console.log(`[visual-network] wrote ${paths.missingRouteLanesGeoJson}`);
  console.log(`[visual-network] wrote ${paths.renderLaneContinuityJson}`);
  console.log(
    `[visual-network] missing route lanes: ${missingRouteLaneFeatures.length} ` +
      `(Q Prospect/Brighton=${qProspectBrightonMissingCount}, ` +
      `2 Flatbush/Eastern=${route2FlatbushEasternMissingCount})`,
  );
}

function failHardVisualDefects(
  visualAnomalies: ReturnType<typeof buildVisualAnomalyRecords>,
  corridorFeatures: LineFeature[],
  minLengthM: number,
): void {
  const sparse = visualAnomalies.filter((anomaly) =>
    anomaly.reasons.includes("sparse_long_slice") ||
    anomaly.reasons.includes("low_detail_straight_long_slice"),
  );
  const short = corridorFeatures
    .filter((feature) => {
      if (feature.properties.visual_feature_type === "same_color_branch_connector") return false;
      return geometryStats(feature.geometry.coordinates).length_m < minLengthM;
    })
    .map((feature) => ({
      feature,
      reasons: ["degenerate_short_fragment"],
      stats: geometryStats(feature.geometry.coordinates),
    }));
  const defects = [...sparse, ...short];
  if (defects.length === 0) return;
  console.error(
    `[visual-network] *** Gate 2F hard visual-defect validation FAILED: ${defects.length} blockers ***`,
  );
  for (const defect of defects.slice(0, 10)) {
    console.error(
      `  ${defect.feature.properties?.corridor_id ?? "<unknown>"} ` +
        `[${routeIdsOf(defect.feature.properties).join(",")}] ` +
        `${defect.reasons.join(",")} len=${defect.stats.length_m.toFixed(2)}m ` +
        `coords=${defect.stats.coordinate_count}`,
    );
  }
  process.exit(1);
}

function writeAnomalyDiagnostics(
  corridorFeatures: LineFeature[],
  edgeById: EdgeLookup,
  parameters: ValidationReportingParameters,
  paths: ValidationReportingPaths,
): void {
  const visualAnomalies = buildVisualAnomalyRecords(corridorFeatures, edgeById, {
    maxSegmentAnomalyM: parameters.maxSegmentAnomalyM,
    sparseLongSliceM: parameters.sparseLongSliceM,
    projectionAnomalyM: parameters.projectionAnomalyM,
  });

  const anomalyGeoJson = {
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2F",
      parameters: {
        max_segment_anomaly_m: parameters.maxSegmentAnomalyM,
        sparse_long_slice_m: parameters.sparseLongSliceM,
        projection_anomaly_m: parameters.projectionAnomalyM,
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
        "shape_ids": anomaly.gtfsPolylineIds,
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
        (feature) => routeIdsOf(feature.properties).length > 1,
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
      "shape_ids": anomaly.gtfsPolylineIds,
      stop_pairs: anomaly.stop_pairs,
    })),
  };

  writeFileSync(paths.anomaliesGeoJson, `${JSON.stringify(anomalyGeoJson)}\n`);
  writeFileSync(paths.anomaliesJson, `${JSON.stringify(anomalyJson, null, 2)}\n`);
  console.log(`[visual-network] wrote ${paths.anomaliesGeoJson}`);
  console.log(`[visual-network] wrote ${paths.anomaliesJson}`);
  console.log(
    `[visual-network] anomalies: ${visualAnomalies.length} ` +
      `(max-segment=${anomalyJson.summary.max_segment_anomaly_count}, ` +
      `projection=${anomalyJson.summary.projection_anomaly_count}, ` +
      `sparse=${anomalyJson.summary.sparse_long_slice_count})`,
  );

  failHardVisualDefects(
    visualAnomalies, corridorFeatures, parameters.openDataMinFragmentLengthM,
  );
}

class RouteStopUnion {
  parent = new Map<string, string>();

  find(stopId: string): string {
    if (!this.parent.has(stopId)) this.parent.set(stopId, stopId);
    let current = stopId;
    let root = this.parent.get(current);
    while (root !== undefined && root !== current) {
      current = root;
      root = this.parent.get(current);
    }
    return current;
  }

  union(left: string, right: string): void {
    const leftRoot = this.find(left);
    const rightRoot = this.find(right);
    if (leftRoot !== rightRoot) this.parent.set(leftRoot, rightRoot);
  }
}

function routeConnectivityStat(routeId: string, indices: number[], edgeFeatures: LineFeature[]): RouteConnectivityStat {
  const stopsInRoute = new Set<string>();
  const unionFind = new RouteStopUnion();
  for (const index of indices) {
    const properties = edgeFeatures[index].properties;
    const fromStopId = String(properties.from_stop_id ?? "");
    const toStopId = String(properties.to_stop_id ?? "");
    stopsInRoute.add(fromStopId);
    stopsInRoute.add(toStopId);
    unionFind.union(fromStopId, toStopId);
  }
  const componentMembers = new Map<string, Set<string>>();
  for (const stopId of stopsInRoute) {
    const root = unionFind.find(stopId);
    const members = componentMembers.get(root);
    if (members) members.add(stopId);
    else componentMembers.set(root, new Set([stopId]));
  }
  const components = [...componentMembers.values()]
    .map((members) => ({ size: members.size, members: [...members] }))
    .sort((left, right) => right.size - left.size);
  const totalStops = stopsInRoute.size;
  const largestSize = components[0]?.size ?? 0;
  const largestRatio = totalStops > 0 ? largestSize / totalStops : 0;
  return {
    route_id: routeId,
    edge_count: indices.length,
    stop_count: totalStops,
    component_count: components.length,
    largest_component_size: largestSize,
    largest_component_ratio: Number(largestRatio.toFixed(3)),
    components: components.map((component) => ({ size: component.size, sample_stop_ids: component.members.slice(0, 6) })),
    passed: components.length === 1,
  };
}

function validateRouteConnectivity(
  edgeFeatures: LineFeature[],
  paths: ValidationReportingPaths,
): ValidationReportingStageResult {
  const edgesByRoute = new Map<string, number[]>();
  for (let i = 0; i < edgeFeatures.length; i += 1) {
    const routeId = String(edgeFeatures[i].properties.route_id ?? "");
    const indices = edgesByRoute.get(routeId);
    if (indices) indices.push(i);
    else edgesByRoute.set(routeId, [i]);
  }

  const perRouteStats: RouteConnectivityStat[] = [];
  const validationFailures: RouteConnectivityFailure[] = [];

  for (const [routeId, indices] of [...edgesByRoute.entries()].sort((left, right) =>
    left[0].localeCompare(right[0], "en", { numeric: true }),
  )) {
    const stats = routeConnectivityStat(routeId, indices, edgeFeatures);
    perRouteStats.push(stats);
    if (stats.passed) continue;
    validationFailures.push({
      route_id: routeId,
      component_count: stats.component_count,
      largest_component_ratio: stats.largest_component_ratio,
      total_stops: stats.stop_count,
      largest_size: stats.largest_component_size,
      sample_component_sizes: stats.components.slice(0, 6).map((component) => component.size),
    });
  }

  writeFileSync(
    paths.routeComponentsJson,
    `${JSON.stringify(
      {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs Gate 2D",
        parameters: {
          snap: "stop_id equality (GTFS parent_station)",
        },
        summary: {
          total_routes: perRouteStats.length,
          routes_passed: perRouteStats.filter((row) => row.passed).length,
          routes_failed: validationFailures.length,
        },
        failures: validationFailures,
        per_route: perRouteStats,
      },
      null,
      2,
    )}\n`,
  );
  console.log(`[visual-network] wrote ${paths.routeComponentsJson}`);
  console.log(`[visual-network] === Gate 2D connectivity results ===`);
  console.log(`[visual-network] total routes:    ${perRouteStats.length}`);
  console.log(`[visual-network] routes passed:   ${perRouteStats.length - validationFailures.length}`);
  console.log(`[visual-network] routes failed:   ${validationFailures.length}`);
  if (validationFailures.length > 0) {
    console.log(`[visual-network] FAILURES:`);
    for (const failure of validationFailures) {
      console.log(
        `[visual-network]   ${failure.route_id.padEnd(5)} components=${failure.component_count} largest_ratio=${failure.largest_component_ratio} total_stops=${failure.total_stops} largest_size=${failure.largest_size} sample_sizes=[${failure.sample_component_sizes.join(",")}]`,
      );
    }
  }

  return { perRouteStats, validationFailures };
}

export function runValidationReportingStage({
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
  paths,
  parameters,
}: ValidationReportingStageInput): ValidationReportingStageResult {
  writeCorridorArtifacts(
    corridorFeatures,
    corridorRows,
    edgeFeatures,
    pairsConsidered,
    pairsMatched,
    matchedPairs,
    paths,
    parameters,
  );
  writeJunctionArtifacts(junctionSnapDiagnostics, paths, parameters.junctionSnapMaxM);
  writeBundleArtifacts(bundleArtifacts, paths);
  logGate2cSummary(
    edgeFeatures,
    corridorFeatures,
    corridorRows,
    pairsConsidered,
    pairsMatched,
    junctionSnapDiagnostics,
    laneChainDiagnostics,
    bundleArtifacts,
    paths,
  );
  logRequiredTrunkCheck(corridorRows);

  console.log("[visual-network] Gate 2G — render-lane continuity diagnostics");
  writeContinuityDiagnostics(
    corridorFeatures,
    collectMissingRouteLaneFeatures(edgeFeatures, corridorFeatures, edgeById, stopsById),
    junctionSnapDiagnostics,
    laneChainDiagnostics,
    bundleArtifacts,
    paths,
  );

  console.log("[visual-network] Gate 2F — visual-geometry anomaly diagnostics");
  writeAnomalyDiagnostics(corridorFeatures, edgeById, parameters, paths);

  console.log("[visual-network] Gate 2D — per-route connectivity validation");
  return validateRouteConnectivity(edgeFeatures, paths);
}
