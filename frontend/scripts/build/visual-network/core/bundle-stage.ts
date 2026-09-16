// Groups corridor features into physical bundles, assigns deterministic lane
// slots, bakes per-lane offset geometry, and emits bundle, bundle_lane, and
// bundle_gap features. Solo corridors are bundle_lane features with one slot.
import type {
  BundleArtifacts,
  BundleSpineRef,
  LineFeature,
  PointFeat,
} from "../shared/types.ts";
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
  propertyString,
  routeColorFor,
  routeIdsOf,
  stringListOf,
} from "../shared/route-config.ts";

type IndexedCorridor = { feature: LineFeature; index: number };
type LaneSlotSource =
  | "physical_bundle_continuous"
  | "fanout"
  | "physical_bundle"
  | "solo"
  | "bundle";

type ClassifiedCorridor = {
  routeIds: string[];
  colors: string[];
  overrideApplied: boolean;
  isBundle: boolean;
  fromAnchorId: string | null;
  toAnchorId: string | null;
};

type BundleLaneSlots = Record<string, number>;

const GTFS_SOURCE_IDS_FIELD = "source_shape_ids";

function sortedBundleColors(routeIds: string[]) {
  return [
    ...new Set(routeIds.map((routeId) => routeColorFor(routeId))),
  ].sort((a, b) => bundleColorRank(a) - bundleColorRank(b));
}

function bundleLaneSlotsForColors(colors: string[]): BundleLaneSlots {
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
    ...new Set<string>(features.flatMap((feature) => routeIdsOf(feature.properties))),
  ].sort(compareRouteIds);
}

function routeDiff(left: string[], right: string[]) {
  const rightSet = new Set(right);
  return left.filter((routeId) => !rightSet.has(routeId));
}

const LANE_SLOT_SOURCE_BY_ROLE = new Map<string, LaneSlotSource>([
  ["continuous_lane", "physical_bundle_continuous"],
  ["fanout", "fanout"],
  ["shared_spine", "physical_bundle"],
]);

function corridorProvenance(props: LineFeature["properties"]) {
  return {
    physical_bundle_id: props.physical_bundle_id ?? null,
    materialized_bundle_id: props.materialized_bundle_id ?? null,
    bundle_materialization_role: props.bundle_materialization_role ?? null,
    fanout_from_lane_slot: props.fanout_from_lane_slot ?? null,
    fanout_to_lane_slot: props.fanout_to_lane_slot ?? null,
    fanout_blend_m: props.fanout_blend_m ?? null,
    source_corridor_id: props.source_corridor_id ?? null,
    shared_extent_start_m: props.shared_extent_start_m ?? null,
    shared_extent_end_m: props.shared_extent_end_m ?? null,
    from_stop_id: props.from_stop_id,
    to_stop_id: props.to_stop_id,
    from_stop_name: props.from_stop_name,
    to_stop_name: props.to_stop_name,
    length_m: props.length_m,
    [GTFS_SOURCE_IDS_FIELD]: props[GTFS_SOURCE_IDS_FIELD] ?? [],
    source_edge_ids: stringListOf(props.source_edge_ids),
  };
}

function physicalBundleSpineFields(props: LineFeature["properties"]) {
  return {
    physical_bundle_spine_hash: props.physical_bundle_spine_hash ?? null,
    physical_bundle_member_count: props.physical_bundle_member_count ?? null,
    physical_bundle_confidence: props.physical_bundle_confidence ?? null,
  };
}

function classifyCorridor(feature: LineFeature): ClassifiedCorridor {
  const routeIds = routeIdsOf(feature.properties).sort(compareRouteIds);
  const fromAnchorId = propertyString(feature.properties.from_anchor_id) ?? null;
  const toAnchorId = propertyString(feature.properties.to_anchor_id) ?? null;
  // Both anchors must be present. An empty "::" key would match a Phase 6
  // override written under that key against every anchorless corridor.
  const overrideKey =
    fromAnchorId && toAnchorId ? `${fromAnchorId}::${toAnchorId}` : null;
  const { colors, overrideApplied } = orderColorsForBundle(sortedBundleColors(routeIds), {
    overrideKey,
    overrides: BUNDLE_ORDER_OVERRIDES,
  });
  return {
    routeIds,
    colors,
    overrideApplied,
    isBundle: routeIds.length > 1,
    fromAnchorId,
    toAnchorId,
  };
}

function buildAnchorFeatureIndex(features: LineFeature[]) {
  const byAnchor = new Map<string, IndexedCorridor[]>();
  features.forEach((feature, index) => {
    const anchorIds = feature.properties.junction_anchor_ids;
    if (!Array.isArray(anchorIds)) return;
    for (const anchorId of anchorIds) {
      const key = String(anchorId);
      if (!byAnchor.has(key)) byAnchor.set(key, []);
      byAnchor.get(key)?.push({ feature, index });
    }
  });
  return byAnchor;
}

function adjacentRouteIdsAtAnchor(
  anchorFeatureIndex: Map<string, IndexedCorridor[]>,
  anchorId: string | null,
  ownCorridorId: string,
) {
  if (!anchorId) return [];
  const adjacent = (anchorFeatureIndex.get(anchorId) ?? [])
    .filter(({ feature }) => feature.properties.corridor_id !== ownCorridorId)
    .map(({ feature }) => feature);
  return unionRouteIds(adjacent);
}

function spineStamp(spine: BundleSpineRef | undefined) {
  return {
    spine_id: spine?.spine_id ?? null,
    base_spine_hash: spine?.base_spine_hash ?? null,
    base_geometry_selection: spine?.method ?? null,
  };
}

function laneOffsetAlreadyBaked(props: LineFeature["properties"], isContinuousLane: boolean) {
  if (isContinuousLane) return true;
  return props.lane_offset_baked ?? false;
}

function projectSoloLane(
  feature: LineFeature,
  classified: ClassifiedCorridor,
  spinesByCorridorId: Map<string, BundleSpineRef>,
  soloBundleId: string,
): LineFeature {
  const props = feature.properties;
  const soloColor = classified.colors[0] ?? "#808183";
  const routeId = classified.routeIds[0] ?? "";
  const materializationRole = propertyString(props.bundle_materialization_role) ?? null;
  const isContinuousLane = materializationRole === "continuous_lane";
  return {
    type: "Feature",
    geometry: feature.geometry,
    properties: {
      visual_feature_type: "bundle_lane",
      bundle_id: soloBundleId,
      corridor_id: props.corridor_id,
      route_id: routeId,
      representative_route_id: routeId,
      route_ids: classified.routeIds,
      color_route_ids: classified.routeIds,
      color: soloColor,
      lane_slot: isContinuousLane ? Number(props.lane_slot ?? 0) : 0,
      lane_offset_baked: laneOffsetAlreadyBaked(props, isContinuousLane),
      lane_group_id: props.materialized_bundle_id ?? soloBundleId,
      lane_slot_source: LANE_SLOT_SOURCE_BY_ROLE.get(materializationRole ?? "") ?? "solo",
      lane_order_basis: [soloColor],
      lane_order_override_applied: false,
      bundle_lane_count: 1,
      bundle_lane_slots: { [soloColor]: 0 },
      ...corridorProvenance(props),
      ...physicalBundleSpineFields(props),
      from_anchor_id: classified.fromAnchorId,
      to_anchor_id: classified.toAnchorId,
      member_corridor_ids: [props.corridor_id ?? null],
      branch_in_route_ids: [],
      branch_out_route_ids: [],
      bundle_entry: false,
      bundle_exit: false,
      ...spineStamp(spinesByCorridorId.get(propertyString(props.corridor_id) ?? "")),
    },
  };
}

function projectBundleFeature(
  feature: LineFeature,
  classified: ClassifiedCorridor,
  bundleId: string,
  laneSlots: BundleLaneSlots,
  branchInRouteIds: string[],
  branchOutRouteIds: string[],
): LineFeature {
  const props = feature.properties;
  return {
    type: "Feature",
    geometry: feature.geometry,
    properties: {
      visual_feature_type: "bundle",
      bundle_id: bundleId,
      corridor_id: props.corridor_id,
      bundle_route_ids: classified.routeIds,
      route_ids: classified.routeIds,
      bundle_color_groups: classified.colors.map((color) => ({
        color,
        route_ids: routesForColor(classified.routeIds, color),
      })),
      member_edge_ids: props.source_edge_ids ?? [],
      member_corridor_ids: [props.corridor_id ?? null],
      entry_node_ids: [classified.fromAnchorId].filter(Boolean),
      exit_node_ids: [classified.toAnchorId].filter(Boolean),
      from_anchor_id: classified.fromAnchorId,
      to_anchor_id: classified.toAnchorId,
      bundle_lane_count: classified.colors.length,
      bundle_lane_slots: laneSlots,
      lane_group_id: bundleId,
      lane_order_basis: classified.colors,
      lane_order_override_applied: classified.overrideApplied,
      ...corridorProvenance(props),
      branch_in_route_ids: branchInRouteIds,
      branch_out_route_ids: branchOutRouteIds,
      bundle_entry: branchInRouteIds.length > 0,
      bundle_exit: branchOutRouteIds.length > 0,
      base_geometry_source_edge_id:
        props.base_member_edge_id ?? props.longest_member_edge_id ?? null,
      base_geometry_selection: props.base_geometry_selection ?? "quality_density_length",
    },
  };
}

function projectOneBundleLane(
  feature: LineFeature,
  classified: ClassifiedCorridor,
  bundleId: string,
  laneSlots: BundleLaneSlots,
  branchInRouteIds: string[],
  branchOutRouteIds: string[],
  spine: BundleSpineRef | undefined,
  color: string,
): LineFeature {
  const props = feature.properties;
  const materializationRole = propertyString(props.bundle_materialization_role) ?? null;
  const isContinuousLane = materializationRole === "continuous_lane";
  const colorRouteIds = routesForColor(classified.routeIds, color);
  const routeId = colorRouteIds[0] ?? classified.routeIds[0];
  return {
    type: "Feature",
    geometry: feature.geometry,
    properties: {
      visual_feature_type: "bundle_lane",
      bundle_id: bundleId,
      corridor_id: props.corridor_id,
      route_id: routeId,
      representative_route_id: routeId,
      route_ids: classified.routeIds,
      color_route_ids: colorRouteIds,
      color,
      lane_slot: isContinuousLane
        ? Number(props.lane_slot ?? laneSlots[color])
        : laneSlots[color],
      lane_offset_baked: laneOffsetAlreadyBaked(props, isContinuousLane),
      lane_group_id: props.materialized_bundle_id ?? bundleId,
      lane_slot_source: LANE_SLOT_SOURCE_BY_ROLE.get(materializationRole ?? "") ?? "bundle",
      lane_order_basis: classified.colors,
      lane_order_override_applied: classified.overrideApplied,
      bundle_lane_count: classified.colors.length,
      bundle_lane_slots: laneSlots,
      ...corridorProvenance(props),
      ...physicalBundleSpineFields(props),
      branch_in_route_ids: branchInRouteIds,
      branch_out_route_ids: branchOutRouteIds,
      bundle_entry: branchInRouteIds.length > 0,
      bundle_exit: branchOutRouteIds.length > 0,
      from_anchor_id: classified.fromAnchorId,
      to_anchor_id: classified.toAnchorId,
      member_corridor_ids: [props.corridor_id ?? null],
      ...spineStamp(spine),
    },
  };
}

function projectBundleGaps(
  feature: LineFeature,
  classified: ClassifiedCorridor,
  bundleId: string,
  endpoints: Array<{
    anchorId: string | null;
    kind: "entry" | "exit";
    adjacentRouteIds: string[];
  }>,
): PointFeat[] {
  const gaps: PointFeat[] = [];
  const coords = feature.geometry.coordinates;
  for (const endpoint of endpoints) {
    if (!endpoint.anchorId) continue;
    const coordinate = endpoint.kind === "entry" ? coords[0] : coords[coords.length - 1];
    for (const color of classified.colors) {
      const colorRouteIds = routesForColor(classified.routeIds, color);
      if (colorRouteIds.some((routeId) => endpoint.adjacentRouteIds.includes(routeId))) {
        continue;
      }
      gaps.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: coordinate },
        properties: {
          marker_type: "bundle_gap",
          bundle_id: bundleId,
          corridor_id: feature.properties.corridor_id,
          anchor_id: endpoint.anchorId,
          endpoint_kind: endpoint.kind,
          route_ids: classified.routeIds,
          color_route_ids: colorRouteIds,
          color,
          reason: "no_same_route_adjacent_bundle_lane_at_anchor",
        },
      });
    }
  }
  return gaps;
}

function fanoutRampSlots(lane: LineFeature) {
  if (lane.properties.bundle_materialization_role !== "fanout") return null;
  const from = Number(lane.properties.fanout_from_lane_slot);
  const to = Number(lane.properties.fanout_to_lane_slot);
  if (!Number.isFinite(from) || !Number.isFinite(to) || (from === 0 && to === 0)) {
    return null;
  }
  return { from, to };
}

function stampZeroRuntimeSlot(lane: LineFeature, semanticSlot: number) {
  lane.properties.lane_offset_baked = true;
  lane.properties.lane_slot_semantic = semanticSlot;
  lane.properties.lane_slot = 0;
  lane.properties.render_lane_slot = 0;
  lane.properties.lane_width_m = LANE_WIDTH_METERS;
}

function bakeLaneGeometry(bundleLaneFeatures: LineFeature[]) {
  // MapLibre line-offset breaks at corners, so bake the offset into coordinates.
  // Continuous materialization already baked once; never re-bake that path.
  for (const lane of bundleLaneFeatures) {
    if (lane.properties.lane_slot_source === "physical_bundle_continuous") {
      stampZeroRuntimeSlot(lane, Number(lane.properties.lane_slot ?? 0));
      continue;
    }
    const ramp = fanoutRampSlots(lane);
    if (ramp) {
      lane.geometry = {
        type: "LineString",
        coordinates: offsetPolylineBySlotRamp(
          lane.geometry.coordinates,
          ramp.from,
          ramp.to,
          LANE_WIDTH_METERS,
        ),
      };
      stampZeroRuntimeSlot(
        lane,
        Math.abs(ramp.from) >= Math.abs(ramp.to) ? ramp.from : ramp.to,
      );
      lane.properties.fanout_slot_ramp_baked = true;
      continue;
    }
    const semanticSlot = Number(lane.properties.lane_slot ?? 0);
    if (semanticSlot === 0) {
      lane.properties.lane_offset_baked = true;
      lane.properties.lane_slot_semantic = semanticSlot;
      lane.properties.render_lane_slot = 0;
      continue;
    }
    lane.geometry = {
      type: "LineString",
      coordinates: offsetPolylineByLaneSlot(lane.geometry.coordinates, semanticSlot),
    };
    stampZeroRuntimeSlot(lane, semanticSlot);
  }
}

export function sortVisualLanes(bundleLaneFeatures: LineFeature[]) {
  return [...bundleLaneFeatures].sort((a, b) => {
    const left =
      a.properties.bundle_id ?? a.properties.corridor_id ?? a.properties.route_id ?? "";
    const right =
      b.properties.bundle_id ?? b.properties.corridor_id ?? b.properties.route_id ?? "";
    const idCompare = String(left).localeCompare(String(right), "en", { numeric: true });
    if (idCompare !== 0) return idCompare;
    return (
      Number(a.properties.lane_slot_semantic ?? a.properties.lane_slot ?? 0) -
      Number(b.properties.lane_slot_semantic ?? b.properties.lane_slot ?? 0)
    );
  });
}

export function buildBundleArtifacts(
  features: LineFeature[],
  spinesByCorridorId: Map<string, BundleSpineRef>,
): BundleArtifacts {
  const anchorFeatureIndex = buildAnchorFeatureIndex(features);
  const bundleFeatures: LineFeature[] = [];
  const bundleLaneFeatures: LineFeature[] = [];
  const bundleGapFeatures: PointFeat[] = [];
  let bundleNumber = 1;
  let soloNumber = 1;

  for (const feature of features) {
    const classified = classifyCorridor(feature);
    if (!classified.isBundle) {
      bundleLaneFeatures.push(
        projectSoloLane(
          feature,
          classified,
          spinesByCorridorId,
          `solo-${String(soloNumber++).padStart(5, "0")}`,
        ),
      );
      continue;
    }
    const bundleId = `bundle-${String(bundleNumber++).padStart(5, "0")}`;
    const laneSlots = bundleLaneSlotsForColors(classified.colors);
    const entryAdjacentRouteIds = adjacentRouteIdsAtAnchor(
      anchorFeatureIndex,
      classified.fromAnchorId,
      propertyString(feature.properties.corridor_id) ?? "",
    );
    const exitAdjacentRouteIds = adjacentRouteIdsAtAnchor(
      anchorFeatureIndex,
      classified.toAnchorId,
      propertyString(feature.properties.corridor_id) ?? "",
    );
    const branchInRouteIds = routeDiff(classified.routeIds, entryAdjacentRouteIds);
    const branchOutRouteIds = routeDiff(classified.routeIds, exitAdjacentRouteIds);
    bundleFeatures.push(
      projectBundleFeature(
        feature,
        classified,
        bundleId,
        laneSlots,
        branchInRouteIds,
        branchOutRouteIds,
      ),
    );
    bundleLaneFeatures.push(
      ...classified.colors.map((color) =>
        projectOneBundleLane(
          feature,
          classified,
          bundleId,
          laneSlots,
          branchInRouteIds,
          branchOutRouteIds,
          spinesByCorridorId.get(propertyString(feature.properties.corridor_id) ?? ""),
          color,
        ),
      ),
    );
    bundleGapFeatures.push(
      ...projectBundleGaps(feature, classified, bundleId, [
        { anchorId: classified.fromAnchorId, kind: "entry", adjacentRouteIds: entryAdjacentRouteIds },
        { anchorId: classified.toAnchorId, kind: "exit", adjacentRouteIds: exitAdjacentRouteIds },
      ]),
    );
  }

  bakeLaneGeometry(bundleLaneFeatures);
  return {
    bundleFeatures,
    bundleLaneFeatures,
    bundleGapFeatures,
    visualFeatures: sortVisualLanes(bundleLaneFeatures),
  };
}
