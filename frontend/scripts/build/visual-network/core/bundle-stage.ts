// Groups corridor features into physical bundles, assigns deterministic lane
// slots, bakes per-lane offset geometry, and emits bundle, bundle_lane,
// unbundled, and bundle_gap features.
import type { LineFeature } from "../shared/types.ts";
import { orderColorsForBundle } from "../../lane-order.ts";
import { offsetPolylineBySlotRamp } from "../../cross-color-spread.ts";
import {
  LANE_WIDTH_METERS,
  offsetPolylineByLaneSlot,
} from "../shared/geometry-utils.ts";
import {
  BUNDLE_ORDER_OVERRIDES,
  bundleColorRank,
  compareRouteIds,
  routeColorFor,
} from "../shared/route-config.ts";

function sortedBundleColors(routeIds: string[]) {
  return [
    ...new Set(routeIds.map((routeId) => routeColorFor(routeId))),
  ].sort((a, b) => bundleColorRank(a) - bundleColorRank(b));
}

function bundleLaneSlotsForColors(colors: string[]) {
  return Object.fromEntries(
    colors.map((color, index) => [color, index - (colors.length - 1) / 2]),
  );
}

export function routesForColor(routeIds: string[], color: string) {
  return routeIds
    .filter((routeId) => routeColorFor(routeId) === color)
    .sort(compareRouteIds);
}

function unionRouteIds(features: LineFeature[]) {
  return [
    ...new Set<string>(features.flatMap((feature) => feature.properties.route_ids ?? [])),
  ].sort(compareRouteIds);
}

function routeDiff(left: string[], right: string[]) {
  const rightSet = new Set(right);
  return left.filter((routeId) => !rightSet.has(routeId));
}

function buildAnchorFeatureIndex(features: LineFeature[]) {
  const byAnchor = new Map();
  features.forEach((feature, index) => {
    for (const anchorId of feature.properties.junction_anchor_ids ?? []) {
      if (!byAnchor.has(anchorId)) byAnchor.set(anchorId, []);
      byAnchor.get(anchorId).push({ feature, index });
    }
  });
  return byAnchor;
}

function adjacentRouteIdsAtAnchor(anchorFeatureIndex: Map<any, any>, anchorId: string, ownCorridorId: string) {
  if (!anchorId) return [];
  const adjacent = (anchorFeatureIndex.get(anchorId) ?? [])
    .filter(({ feature }: any) => feature.properties.corridor_id !== ownCorridorId)
    .map(({ feature }: any) => feature);
  return unionRouteIds(adjacent);
}

export function buildBundleArtifacts(features: LineFeature[], spinesByCorridorId: Map<any, any>) {
  const anchorFeatureIndex = buildAnchorFeatureIndex(features);
  const bundleFeatures: any[] = [];
  const bundleLaneFeatures: any[] = [];
  const unbundledFeatures: any[] = [];
  const bundleGapFeatures: any[] = [];
  let bundleNumber = 1;
  let soloNumber = 1;

  for (const feature of features) {
    const routeIds = [...(feature.properties.route_ids ?? [])].sort(compareRouteIds);
    const heuristicColors = sortedBundleColors(routeIds);
    // Only look up overrides when BOTH anchors are present; otherwise the
    // override key would degenerate to "::" and any Phase 6 override accidentally
    // written under that key would match every anchorless bundle.
    const fromAnchorIdRaw = feature.properties.from_anchor_id ?? null;
    const toAnchorIdRaw = feature.properties.to_anchor_id ?? null;
    const overrideKey = (fromAnchorIdRaw && toAnchorIdRaw)
      ? `${fromAnchorIdRaw}::${toAnchorIdRaw}`
      : null;
    const { colors, overrideApplied } = orderColorsForBundle(heuristicColors, {
      overrideKey,
      overrides: BUNDLE_ORDER_OVERRIDES,
    });
    const isBundle = routeIds.length > 1;

    if (!isBundle) {
      // Fix 2: promote solo corridors to bundle_lane with lane_slot=0 and
      // bundle_lane_count=1. The runtime now renders every line through
      // the same code path. No raw "corridor" type survives in the final.
      const soloBundleId = `solo-${String(soloNumber++).padStart(5, "0")}`;
      const soloColor = colors[0] ?? "#808183";
      const laneSlots = { [soloColor]: 0 };
      const props = feature.properties;
      const fromAnchorId = props.from_anchor_id ?? null;
      const toAnchorId = props.to_anchor_id ?? null;
      const spine = spinesByCorridorId?.get(props.corridor_id);
      const materializationRole = props.bundle_materialization_role ?? null;
      const isContinuousLane = materializationRole === "continuous_lane";
      const materializedLaneSlotSource =
        isContinuousLane
          ? "physical_bundle_continuous"
          : materializationRole === "fanout"
            ? "fanout"
            : materializationRole === "shared_spine"
              ? "physical_bundle"
              : "solo";
      // Continuous lanes already baked their lane offset in materialization; keep the
      // slot + provenance so the later cross-color pass leaves them alone (no double-offset).
      const materializedLaneSlot = isContinuousLane ? Number(props.lane_slot ?? 0) : 0;
      bundleLaneFeatures.push({
        type: "Feature",
        geometry: feature.geometry,
        properties: {
          visual_feature_type: "bundle_lane",
          bundle_id: soloBundleId,
          corridor_id: props.corridor_id,
          route_id: routeIds[0] ?? "",
          representative_route_id: routeIds[0] ?? "",
          route_ids: routeIds,
          color_route_ids: routeIds,
          color: soloColor,
          lane_slot: materializedLaneSlot,
          lane_offset_baked: isContinuousLane ? true : (props.lane_offset_baked ?? false),
          lane_group_id: props.materialized_bundle_id ?? soloBundleId,
          lane_slot_source: materializedLaneSlotSource,
          lane_order_basis: [soloColor],
          lane_order_override_applied: false,
          bundle_lane_count: 1,
          bundle_lane_slots: laneSlots,
          physical_bundle_id: feature.properties.physical_bundle_id ?? null,
          materialized_bundle_id: props.materialized_bundle_id ?? null,
          bundle_materialization_role: materializationRole,
          fanout_from_lane_slot: props.fanout_from_lane_slot ?? null,
          fanout_to_lane_slot: props.fanout_to_lane_slot ?? null,
          fanout_blend_m: props.fanout_blend_m ?? null,
          source_corridor_id: props.source_corridor_id ?? null,
          shared_extent_start_m: props.shared_extent_start_m ?? null,
          shared_extent_end_m: props.shared_extent_end_m ?? null,
          branch_in_route_ids: [],
          branch_out_route_ids: [],
          bundle_entry: false,
          bundle_exit: false,
          from_stop_id: props.from_stop_id,
          to_stop_id: props.to_stop_id,
          from_stop_name: props.from_stop_name,
          to_stop_name: props.to_stop_name,
          from_anchor_id: fromAnchorId,
          to_anchor_id: toAnchorId,
          length_m: props.length_m,
          source_shape_ids: props.source_shape_ids ?? [],
          source_edge_ids: props.source_edge_ids ?? [],
          member_corridor_ids: [props.corridor_id],
          spine_id: spine?.spine_id ?? null,
          base_spine_hash: spine?.base_spine_hash ?? null,
          base_geometry_selection: spine?.method ?? null,
          physical_bundle_spine_hash: feature.properties.physical_bundle_spine_hash ?? null,
          physical_bundle_member_count: feature.properties.physical_bundle_member_count ?? null,
          physical_bundle_confidence: feature.properties.physical_bundle_confidence ?? null,
        },
      });
      continue;
    }

    const bundleId = `bundle-${String(bundleNumber++).padStart(5, "0")}`;
    const laneSlots = bundleLaneSlotsForColors(colors);
    const fromAnchorId = feature.properties.from_anchor_id ?? null;
    const toAnchorId = feature.properties.to_anchor_id ?? null;
    const entryAdjacentRouteIds = adjacentRouteIdsAtAnchor(
      anchorFeatureIndex,
      fromAnchorId,
      feature.properties.corridor_id,
    );
    const exitAdjacentRouteIds = adjacentRouteIdsAtAnchor(
      anchorFeatureIndex,
      toAnchorId,
      feature.properties.corridor_id,
    );
    const branchInRouteIds = routeDiff(routeIds, entryAdjacentRouteIds);
    const branchOutRouteIds = routeDiff(routeIds, exitAdjacentRouteIds);

    const bundleProperties = {
      visual_feature_type: "bundle",
      bundle_id: bundleId,
      corridor_id: feature.properties.corridor_id,
      bundle_route_ids: routeIds,
      route_ids: routeIds,
      bundle_color_groups: colors.map((color) => ({
        color,
        route_ids: routesForColor(routeIds, color),
      })),
      member_edge_ids: feature.properties.source_edge_ids ?? [],
      member_corridor_ids: [feature.properties.corridor_id],
      entry_node_ids: [fromAnchorId].filter(Boolean),
      exit_node_ids: [toAnchorId].filter(Boolean),
      from_anchor_id: fromAnchorId,
      to_anchor_id: toAnchorId,
      from_stop_id: feature.properties.from_stop_id,
      to_stop_id: feature.properties.to_stop_id,
      from_stop_name: feature.properties.from_stop_name,
      to_stop_name: feature.properties.to_stop_name,
      length_m: feature.properties.length_m,
      bundle_lane_count: colors.length,
      bundle_lane_slots: laneSlots,
      lane_group_id: bundleId,
      lane_order_basis: colors,
      lane_order_override_applied: overrideApplied,
      physical_bundle_id: feature.properties.physical_bundle_id ?? null,
      materialized_bundle_id: feature.properties.materialized_bundle_id ?? null,
      bundle_materialization_role: feature.properties.bundle_materialization_role ?? null,
      fanout_from_lane_slot: feature.properties.fanout_from_lane_slot ?? null,
      fanout_to_lane_slot: feature.properties.fanout_to_lane_slot ?? null,
      fanout_blend_m: feature.properties.fanout_blend_m ?? null,
      source_corridor_id: feature.properties.source_corridor_id ?? null,
      shared_extent_start_m: feature.properties.shared_extent_start_m ?? null,
      shared_extent_end_m: feature.properties.shared_extent_end_m ?? null,
      branch_in_route_ids: branchInRouteIds,
      branch_out_route_ids: branchOutRouteIds,
      bundle_entry: branchInRouteIds.length > 0,
      bundle_exit: branchOutRouteIds.length > 0,
      base_geometry_source_edge_id:
        feature.properties.base_member_edge_id ??
        feature.properties.longest_member_edge_id ??
        null,
      base_geometry_selection:
        feature.properties.base_geometry_selection ?? "quality_density_length",
      source_shape_ids: feature.properties.source_shape_ids ?? [],
      source_edge_ids: feature.properties.source_edge_ids ?? [],
    };

    bundleFeatures.push({
      type: "Feature",
      geometry: feature.geometry,
      properties: bundleProperties,
    });

    const bundleSpine = spinesByCorridorId?.get(feature.properties.corridor_id);
    for (const color of colors) {
      const colorRouteIds = routesForColor(routeIds, color);
      bundleLaneFeatures.push({
        type: "Feature",
        geometry: feature.geometry,
        properties: {
          visual_feature_type: "bundle_lane",
          bundle_id: bundleId,
          corridor_id: feature.properties.corridor_id,
          route_id: colorRouteIds[0] ?? routeIds[0],
          representative_route_id: colorRouteIds[0] ?? routeIds[0],
          route_ids: routeIds,
          color_route_ids: colorRouteIds,
          color,
          lane_slot:
            feature.properties.bundle_materialization_role === "continuous_lane"
              ? Number(feature.properties.lane_slot ?? laneSlots[color])
              : laneSlots[color],
          lane_offset_baked:
            feature.properties.bundle_materialization_role === "continuous_lane"
              ? true
              : (feature.properties.lane_offset_baked ?? false),
          lane_group_id: feature.properties.materialized_bundle_id ?? bundleId,
          lane_slot_source:
            feature.properties.bundle_materialization_role === "continuous_lane"
              ? "physical_bundle_continuous"
              : feature.properties.bundle_materialization_role === "shared_spine"
                ? "physical_bundle"
                : feature.properties.bundle_materialization_role === "fanout"
                  ? "fanout"
                  : "bundle",
          lane_order_basis: colors,
          lane_order_override_applied: overrideApplied,
          bundle_lane_count: colors.length,
          bundle_lane_slots: laneSlots,
          materialized_bundle_id: feature.properties.materialized_bundle_id ?? null,
          bundle_materialization_role: feature.properties.bundle_materialization_role ?? null,
          fanout_from_lane_slot: feature.properties.fanout_from_lane_slot ?? null,
          fanout_to_lane_slot: feature.properties.fanout_to_lane_slot ?? null,
          fanout_blend_m: feature.properties.fanout_blend_m ?? null,
          source_corridor_id: feature.properties.source_corridor_id ?? null,
          shared_extent_start_m: feature.properties.shared_extent_start_m ?? null,
          shared_extent_end_m: feature.properties.shared_extent_end_m ?? null,
          branch_in_route_ids: branchInRouteIds,
          branch_out_route_ids: branchOutRouteIds,
          bundle_entry: branchInRouteIds.length > 0,
          bundle_exit: branchOutRouteIds.length > 0,
          from_stop_id: feature.properties.from_stop_id,
          to_stop_id: feature.properties.to_stop_id,
          from_stop_name: feature.properties.from_stop_name,
          to_stop_name: feature.properties.to_stop_name,
          from_anchor_id: fromAnchorId,
          to_anchor_id: toAnchorId,
          length_m: feature.properties.length_m,
          source_shape_ids: feature.properties.source_shape_ids ?? [],
          source_edge_ids: feature.properties.source_edge_ids ?? [],
          member_corridor_ids: [feature.properties.corridor_id],
          spine_id: bundleSpine?.spine_id ?? null,
          base_spine_hash: bundleSpine?.base_spine_hash ?? null,
          base_geometry_selection: bundleSpine?.method ?? null,
          physical_bundle_id: feature.properties.physical_bundle_id ?? null,
          physical_bundle_spine_hash: feature.properties.physical_bundle_spine_hash ?? null,
          physical_bundle_member_count: feature.properties.physical_bundle_member_count ?? null,
          physical_bundle_confidence: feature.properties.physical_bundle_confidence ?? null,
        },
      });
    }

    for (const [anchorId, endpointKind, adjacentRouteIds] of [
      [fromAnchorId, "entry", entryAdjacentRouteIds],
      [toAnchorId, "exit", exitAdjacentRouteIds],
    ]) {
      if (!anchorId) continue;
      for (const color of colors) {
        const colorRouteIds = routesForColor(routeIds, color);
        if (colorRouteIds.some((routeId) => adjacentRouteIds.includes(routeId))) {
          continue;
        }
        const coordinate =
          endpointKind === "entry"
            ? feature.geometry.coordinates[0]
            : feature.geometry.coordinates[feature.geometry.coordinates.length - 1];
        bundleGapFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: coordinate },
          properties: {
            marker_type: "bundle_gap",
            bundle_id: bundleId,
            corridor_id: feature.properties.corridor_id,
            anchor_id: anchorId,
            endpoint_kind: endpointKind,
            route_ids: routeIds,
            color_route_ids: colorRouteIds,
            color,
            reason: "no_same_route_adjacent_bundle_lane_at_anchor",
          },
        });
      }
    }
  }

  // Bake lane offsets into geometry because MapLibre line-offset breaks at
  // corners. Retain the semantic slot as metadata and zero the runtime slot.
  for (const lane of bundleLaneFeatures) {
    // Continuous-materialization lanes already have their lane offset baked into
    // geometry by materializePhysicalBundles; never re-bake it (that was the
    // double/triple-offset). Just flag it and zero the runtime slot so MapLibre and
    // the later cross-color passes add no further offset.
    if (lane.properties.lane_slot_source === "physical_bundle_continuous") {
      lane.properties.lane_offset_baked = true;
      lane.properties.lane_slot_semantic = Number(lane.properties.lane_slot ?? 0);
      lane.properties.lane_slot = 0;
      lane.properties.render_lane_slot = 0;
      lane.properties.lane_width_m = LANE_WIDTH_METERS;
      continue;
    }
    const fanoutFromSlot = Number(lane.properties.fanout_from_lane_slot);
    const fanoutToSlot = Number(lane.properties.fanout_to_lane_slot);
    const shouldBakeFanoutRamp =
      lane.properties.bundle_materialization_role === "fanout" &&
      Number.isFinite(fanoutFromSlot) &&
      Number.isFinite(fanoutToSlot) &&
      (fanoutFromSlot !== 0 || fanoutToSlot !== 0);

    if (shouldBakeFanoutRamp) {
      lane.geometry = {
        type: "LineString",
        coordinates: offsetPolylineBySlotRamp(
          lane.geometry.coordinates,
          fanoutFromSlot,
          fanoutToSlot,
          LANE_WIDTH_METERS,
        ),
      };
      lane.properties.lane_offset_baked = true;
      lane.properties.fanout_slot_ramp_baked = true;
      lane.properties.lane_slot_semantic =
        Math.abs(fanoutFromSlot) >= Math.abs(fanoutToSlot)
          ? fanoutFromSlot
          : fanoutToSlot;
      lane.properties.lane_slot = 0;
      lane.properties.render_lane_slot = 0;
      lane.properties.lane_width_m = LANE_WIDTH_METERS;
      continue;
    }

    const semanticSlot = Number(lane.properties.lane_slot ?? 0);
    if (semanticSlot === 0) {
      // No offset needed, but flag the feature uniformly.
      lane.properties.lane_offset_baked = true;
      lane.properties.lane_slot_semantic = semanticSlot;
      lane.properties.render_lane_slot = 0;
      continue;
    }
    const baked = offsetPolylineByLaneSlot(
      lane.geometry.coordinates,
      semanticSlot,
    );
    lane.geometry = { type: "LineString", coordinates: baked };
    lane.properties.lane_offset_baked = true;
    lane.properties.lane_slot_semantic = semanticSlot;
    lane.properties.lane_slot = 0;
    lane.properties.render_lane_slot = 0;
    lane.properties.lane_width_m = LANE_WIDTH_METERS;
  }

  // Note: unbundledFeatures is now empty (Fix 2 promoted solos directly
  // into bundleLaneFeatures). Keep the field for backwards compatibility
  // with downstream summaries that read it.
  const visualFeatures = [...bundleLaneFeatures, ...unbundledFeatures].sort(
    (a, b) => {
      const left =
        a.properties.bundle_id ??
        a.properties.corridor_id ??
        a.properties.route_id ??
        "";
      const right =
        b.properties.bundle_id ??
        b.properties.corridor_id ??
        b.properties.route_id ??
        "";
      const idCompare = String(left).localeCompare(String(right), "en", {
        numeric: true,
      });
      if (idCompare !== 0) return idCompare;
      return (
        Number(a.properties.lane_slot_semantic ?? a.properties.lane_slot ?? 0) -
        Number(b.properties.lane_slot_semantic ?? b.properties.lane_slot ?? 0)
      );
    },
  );

  return {
    bundleFeatures,
    bundleLaneFeatures,
    unbundledFeatures,
    bundleGapFeatures,
    visualFeatures,
  };
}
