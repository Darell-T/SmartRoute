import { writeFileSync } from "node:fs";
import {
  buildRouteIncidentCounts,
  buildVisualAnomalyRecords,
  buildVisualRouteIncidentCounts,
} from "../shared/diagnostics.ts";
import { geometryStats } from "../shared/geometry-utils.ts";
import type { LineFeature } from "../shared/types.ts";
import type {
  RouteConnectivityFailure,
  RouteConnectivityStat,
  ValidationReportingParameters,
  ValidationReportingPaths,
  ValidationReportingStageResult,
} from "./validation-reporting-types.ts";

type ValidationReportingBundleArtifacts = {
  bundleFeatures: any[];
  bundleLaneFeatures: any[];
  unbundledFeatures: any[];
  bundleGapFeatures: any[];
  visualFeatures: any[];
};

type ValidationReportingJunctionSnapDiagnostics = {
  anchorFeatures: any[];
  snapFeatures: any[];
};

type ValidationReportingLaneChainDiagnostics = {
  lane_group_count: number;
  chain_slot_feature_count: number;
};

type ValidationReportingStop = {
  lon: number;
  lat: number;
  name: string;
};

type ValidationReportingStageInput = {
  edgeFeatures: any[];
  corridorFeatures: LineFeature[];
  corridorRows: any[];
  pairsConsidered: number;
  pairsMatched: number;
  matchedPairs: any[];
  junctionSnapDiagnostics: ValidationReportingJunctionSnapDiagnostics;
  laneChainDiagnostics: ValidationReportingLaneChainDiagnostics;
  bundleArtifacts: ValidationReportingBundleArtifacts;
  edgeById: Map<any, any>;
  stopsById: Map<string, ValidationReportingStop>;
  paths: ValidationReportingPaths;
  parameters: ValidationReportingParameters;
};

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
  // Sort corridor features for stable output
  corridorFeatures.sort((a, b) =>
    a.properties.corridor_id.localeCompare(b.properties.corridor_id),
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
  writeFileSync(
    paths.junctionAnchorsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs Gate 2G",
        parameters: {
          junction_snap_max_m: parameters.junctionSnapMaxM,
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
          junction_snap_max_m: parameters.junctionSnapMaxM,
        },
        summary: {
          snap_count: junctionSnapDiagnostics.snapFeatures.length,
        },
      },
      features: junctionSnapDiagnostics.snapFeatures,
    })}\n`,
  );
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
          remaining_unbundled_corridors: bundleArtifacts.unbundledFeatures.length,
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

  console.log(`[visual-network] === Gate 2C corridor summary ===`);
  console.log(`[visual-network] edges in:                ${edgeFeatures.length}`);
  console.log(`[visual-network] candidate pairs:          ${pairsConsidered}`);
  console.log(`[visual-network] matched pairs:            ${pairsMatched}`);
  console.log(`[visual-network] corridors out:            ${corridorFeatures.length}`);
  const sharedCorridors = corridorRows.filter((c) => c.is_shared);
  const multiRouteCorridors = corridorRows.filter((c) => c.route_ids.length > 1);
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
  const missingRouteLaneFeatures: any[] = [];

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
    paths.missingRouteLanesGeoJson,
    `${JSON.stringify(missingRouteLaneGeoJson)}\n`,
  );
  writeFileSync(
    paths.renderLaneContinuityJson,
    `${JSON.stringify(renderLaneContinuityJson, null, 2)}\n`,
  );
  console.log(`[visual-network] wrote ${paths.missingRouteLanesGeoJson}`);
  console.log(`[visual-network] wrote ${paths.renderLaneContinuityJson}`);
  console.log(
    `[visual-network] missing route lanes: ${missingRouteLaneFeatures.length} ` +
      `(Q Prospect/Brighton=${missingRouteLaneGeoJson.metadata.summary.q_prospect_brighton_missing_count}, ` +
      `2 Flatbush/Eastern=${missingRouteLaneGeoJson.metadata.summary.route_2_flatbush_eastern_missing_count})`,
  );

  console.log("[visual-network] Gate 2F — visual-geometry anomaly diagnostics");

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
        return geometryStats(feature.geometry.coordinates).length_m < parameters.openDataMinFragmentLengthM;
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

  const perRouteStats: RouteConnectivityStat[] = [];
  const validationFailures: RouteConnectivityFailure[] = [];

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
    paths.routeComponentsJson,
    `${JSON.stringify(validationDoc, null, 2)}\n`,
  );
  console.log(`[visual-network] wrote ${paths.routeComponentsJson}`);

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

  return { perRouteStats, validationFailures };
}
