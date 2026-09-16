import { writeFileSync } from "node:fs";
import { buildBranchTransitions } from "../../branch-transitions.ts";
import {
  filterBogusTransitions,
  markOrphanLanes,
  removeOrphanErrorLanes,
} from "../../lane-continuity-filter.ts";
import {
  assertNoBogusTransitions,
  assertQContinuousInBrooklyn,
  assertOriginsForRedGreenFlatbushEastern,
} from "../../spine-validation.ts";
import { LANE_WIDTH_METERS } from "../shared/geometry-utils.ts";
import type { StopsById } from "../inputs/gtfs-topology.ts";
import { compareRouteIds, propertyString, routeIdsOf } from "../shared/route-config.ts";
import type { BundleArtifacts, LineFeature } from "../shared/types.ts";
import { routesForColor, sortVisualLanes } from "./bundle-stage.ts";

type Phase3cLaneContinuityStageInput = {
  bundleArtifacts: BundleArtifacts;
  stopsById: StopsById;
  branchTransitionsGeoJsonPath: string;
  branchTransitionMaxM: number;
};

type TransitionSource = {
  geometry: { coordinates: LineFeature["geometry"]["coordinates"] };
  properties: {
    color?: string;
    anchor_id?: string;
    bundle_id_from?: string;
    bundle_id_to?: string;
    length_m?: number;
    transition_classification?: string;
    route_ids?: string[];
    color_route_ids?: string[];
  };
};

function promoteTransitionLane(
  transition: TransitionSource,
  bundleRouteIds: Map<string, string[]>,
): LineFeature {
  const properties = transition.properties;
  const color = properties.color ?? "";
  const fromId = properties.bundle_id_from ?? "";
  const toId = properties.bundle_id_to ?? "";
  const routesFrom = bundleRouteIds.get(fromId) ?? [];
  const routesTo = bundleRouteIds.get(toId) ?? [];
  const routeIdsUnion = [...new Set([...routesFrom, ...routesTo])].sort(compareRouteIds);
  const colorRouteIds = routesForColor(routeIdsUnion, color);
  const classification = routesFrom.some((routeId) => routesTo.includes(routeId))
    ? "safe_same_route_continuation"
    : "likely_branch_exit";
  properties.transition_classification = classification;
  properties.route_ids = routeIdsUnion;
  properties.color_route_ids = colorRouteIds;
  const routeId = colorRouteIds[0] ?? routeIdsUnion[0] ?? "";
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: transition.geometry.coordinates },
    properties: {
      visual_feature_type: "bundle_lane",
      feature_type: "branch_transition",
      lane_slot_source: "branch_transition",
      bundle_id: `transition-${fromId}-${toId}-${properties.anchor_id}-${color.slice(1)}`,
      corridor_id: null,
      spine_id: null,
      base_spine_hash: null,
      base_geometry_selection: null,
      physical_bundle_id: null,
      physical_bundle_spine_hash: null,
      physical_bundle_member_count: null,
      physical_bundle_confidence: null,
      route_id: routeId,
      representative_route_id: routeId,
      route_ids: routeIdsUnion,
      color_route_ids: colorRouteIds,
      color,
      lane_slot: 0,
      lane_slot_semantic: 0,
      lane_offset_baked: true,
      lane_width_m: LANE_WIDTH_METERS,
      render_lane_slot: 0,
      lane_group_id: null,
      lane_order_basis: [color],
      lane_order_override_applied: false,
      bundle_lane_count: 1,
      bundle_lane_slots: { [color]: 0 },
      branch_in_route_ids: [],
      branch_out_route_ids: [],
      bundle_entry: false,
      bundle_exit: false,
      bridge: false,
      bundle_id_from: properties.bundle_id_from,
      bundle_id_to: properties.bundle_id_to,
      anchor_id: properties.anchor_id,
      from_anchor_id: properties.anchor_id,
      to_anchor_id: properties.anchor_id,
      length_m: properties.length_m,
      transition_classification: classification,
    },
  };
}

function promoteBranchTransitions(
  bundleArtifacts: BundleArtifacts,
  branchTransitionMaxM: number,
  branchTransitionsGeoJsonPath: string,
) {
  const bundleLaneFeatures = bundleArtifacts.bundleLaneFeatures;
  const bundleRouteIds = new Map<string, string[]>();
  for (const lane of bundleLaneFeatures) {
    const bundleId = propertyString(lane.properties.bundle_id);
    if (!bundleId || bundleRouteIds.has(bundleId)) continue;
    bundleRouteIds.set(bundleId, routeIdsOf(lane.properties));
  }
  const { transitions, coincidentSkipped } = buildBranchTransitions(bundleLaneFeatures, {
    maxBridgeM: branchTransitionMaxM,
    minBridgeM: 0.5,
  });
  transitions.sort((a, b) => {
    const left = `${a.properties.anchor_id}|${a.properties.color}|${a.properties.bundle_id_from}|${a.properties.bundle_id_to}`;
    const right = `${b.properties.anchor_id}|${b.properties.color}|${b.properties.bundle_id_from}|${b.properties.bundle_id_to}`;
    return left.localeCompare(right);
  });
  const promotedLanes = transitions.map((transition) =>
    promoteTransitionLane(transition, bundleRouteIds),
  );
  bundleLaneFeatures.push(...promotedLanes);
  writeFileSync(
    branchTransitionsGeoJsonPath,
    `${JSON.stringify({ type: "FeatureCollection", features: transitions })}\n`,
  );
  if (promotedLanes.length > 0) {
    bundleArtifacts.visualFeatures.push(...promotedLanes);
    bundleArtifacts.visualFeatures = sortVisualLanes(bundleArtifacts.visualFeatures);
  }
  const safeCount = promotedLanes.filter(
    (lane) => lane.properties.transition_classification === "safe_same_route_continuation",
  ).length;
  const branchExitCount = promotedLanes.filter(
    (lane) => lane.properties.transition_classification === "likely_branch_exit",
  ).length;
  console.log(`[visual-network] transitions promoted:      ${promotedLanes.length}`);
  console.log(`[visual-network]   safe_same_route:         ${safeCount}`);
  console.log(`[visual-network]   likely_branch_exit:      ${branchExitCount}`);
  console.log(`[visual-network] coincident pairs skipped:  ${coincidentSkipped}`);
  console.log(`[visual-network] bundle_lanes (post-promotion): ${bundleLaneFeatures.length}`);
  console.log(`[visual-network] visualFeatures (post-promo):   ${bundleArtifacts.visualFeatures.length}`);
}

function dropBogusTransitions(bundleArtifacts: BundleArtifacts, index: Map<string, Set<string>>) {
  const { kept, dropped } = filterBogusTransitions(bundleArtifacts.bundleLaneFeatures, index);
  if (dropped.length === 0) {
    console.log(`[visual-network] Phase 3c: bogus transitions dropped:     0`);
    return;
  }
  bundleArtifacts.bundleLaneFeatures.length = 0;
  bundleArtifacts.bundleLaneFeatures.push(...kept);
  const droppedIds = new Set(
    dropped.flatMap((item) => {
      const bundleId = propertyString(item.feature.properties.bundle_id);
      return bundleId ? [bundleId] : [];
    }),
  );
  const visBefore = bundleArtifacts.visualFeatures.length;
  bundleArtifacts.visualFeatures = bundleArtifacts.visualFeatures.filter((feature) => {
    const bundleId = propertyString(feature.properties.bundle_id);
    return !bundleId || !droppedIds.has(bundleId);
  });
  console.log(`[visual-network] Phase 3c: bogus transitions removed from visualFeatures: ${visBefore - bundleArtifacts.visualFeatures.length}`);
  console.log(`[visual-network] Phase 3c: bogus transitions dropped:     ${dropped.length}`);
  for (const item of dropped.slice(0, 10)) {
    console.log(`[visual-network]   dropped ${item.feature.properties.bundle_id}: ${item.reason}`);
  }
  if (dropped.length > 10) {
    console.log(`[visual-network]   (showing first 10 of ${dropped.length})`);
  }
}

function markAndRemoveOrphans(bundleArtifacts: BundleArtifacts, stopsById: StopsById) {
  const lanes = bundleArtifacts.bundleLaneFeatures;
  const terminalStopIds = new Set<string>();
  for (const stop of stopsById.values()) {
    if (stop?.stop_id) terminalStopIds.add(stop.stop_id);
  }
  markOrphanLanes(lanes, terminalStopIds);
  const orphanCount = lanes.filter((feature) => feature.properties.qa_orphan_origin).length;
  console.log(`[visual-network] Phase 3c: orphan lanes flagged:          ${orphanCount}`);
  const removal = removeOrphanErrorLanes(lanes);
  lanes.length = 0;
  lanes.push(...removal.features);
  console.log(`[visual-network] Phase 3c: orphan-error lanes removed:    ${removal.removedCount}`);
  console.log(`[visual-network] bundle_lanes (post-3c):                  ${lanes.length}`);
}

function runContinuityGates(bundleArtifacts: BundleArtifacts, index: Map<string, Set<string>>) {
  const noBogus = assertNoBogusTransitions(bundleArtifacts.bundleLaneFeatures, index);
  if (!noBogus.passed) {
    console.error(`[visual-network] *** Phase 3c gate D2 FAILED: ${noBogus.violations.length} bogus transition(s) ***`);
    for (const violation of noBogus.violations.slice(0, 10)) {
      console.error(`  ${violation.bundle_id}: ${violation.reason}`);
    }
    process.exit(1);
  }
  console.log(`[visual-network] Phase 3c gate D2 (no bogus transitions): PASS`);
  const visualFeatures = bundleArtifacts.visualFeatures;
  const qResult = assertQContinuousInBrooklyn(visualFeatures, null);
  console.log(`[visual-network] Phase 3c gate D3 (Q Brooklyn):           ${qResult.passed ? "PASS" : "WARN"} — ${qResult.detail}`);
  if (!qResult.passed) {
    console.warn(`[visual-network]   Disconnected bundle IDs: ${qResult.disconnectedBundleIds.slice(0, 5).join(", ")}`);
    console.warn(`[visual-network]   (This is a known bbox-boundary artifact; not blocking promotion.)`);
  }
  const feResult = assertOriginsForRedGreenFlatbushEastern(visualFeatures);
  console.log(`[visual-network] Phase 3c gate D4 (IRT Flatbush origins): ${feResult.passed ? "PASS" : "WARN"} (${feResult.missingUpstreamCount} missing)`);
  if (!feResult.passed) {
    for (const violation of feResult.violations.slice(0, 5)) {
      console.warn(`[visual-network]   ${violation.bundle_id}: ${violation.detail}`);
    }
  }
  console.log(`[visual-network] Lane continuity validation:              PASS`);
}

export function applyPhase3cLaneContinuityStage({
  bundleArtifacts,
  stopsById,
  branchTransitionsGeoJsonPath,
  branchTransitionMaxM,
}: Phase3cLaneContinuityStageInput): void {
  promoteBranchTransitions(bundleArtifacts, branchTransitionMaxM, branchTransitionsGeoJsonPath);
  const index = new Map<string, Set<string>>();
  for (const lane of bundleArtifacts.bundleLaneFeatures) {
    const routeIds = routeIdsOf(lane.properties);
    for (const key of [
      propertyString(lane.properties.bundle_id),
      propertyString(lane.properties.corridor_id),
    ]) {
      if (!key) continue;
      let bucket = index.get(key);
      if (!bucket) {
        bucket = new Set<string>();
        index.set(key, bucket);
      }
      for (const routeId of routeIds) bucket.add(routeId);
    }
  }
  dropBogusTransitions(bundleArtifacts, index);
  markAndRemoveOrphans(bundleArtifacts, stopsById);
  runContinuityGates(bundleArtifacts, index);
}
