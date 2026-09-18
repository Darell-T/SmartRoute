import { writeFileSync } from "node:fs";
import {
  groupCorridorsByColorAndOverlap,
  mergeSameColorGroup,
} from "../../same-color-merge.ts";
import type {
  SameColorCorridor,
  SameColorMergeAppliedResult,
  SameColorMergeGroup,
} from "../../same-color-merge.ts";
import { compareRouteIds, propertyNumber, propertyString, routeColorFor, routeIdsOf } from "../shared/route-config.ts";
import type { LineFeature, Position } from "../shared/types.ts";

const SOURCE_POLYLINE_IDS = "source_shape_ids";

type Phase3dSameColorMergeStageInput = {
  corridorFeatures: LineFeature[];
  sameColorMergesGeoJsonPath: string;
};

type SameColorMergeDebugFeature = {
  type: "Feature";
  geometry: LineFeature["geometry"] | null;
  properties: {
    visual_feature_type: string;
    color: string;
    trunk_corridor_id: string;
    member_corridor_ids?: string[];
    reason?: string;
    merged_from_corridor_ids?: string[];
    branches_clipped?: string[];
    branches_dropped?: string[];
    branch_connectors?: Array<{
      corridor_id: string;
      distance_m: number;
      endpoint_kind: string;
    }>;
    route_ids_union?: string[];
  };
};

type AppliedGroupStats = {
  branchesClipped: number;
  branchesDropped: number;
  branchConnectorsAdded: number;
  nextConnectorNumber: number;
  debugFeature: SameColorMergeDebugFeature;
};

function recomputeLengthM(coords: Position[]): number {
  const earthM = 6371000;
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    const [lon1, lat1] = coords[i - 1];
    const [lon2, lat2] = coords[i];
    const toRad = (degrees: number) => (degrees * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    total += 2 * earthM * Math.asin(Math.sqrt(a));
  }
  return total;
}

function corridorsByIdFromFeatures(corridorFeatures: LineFeature[]): Map<string, SameColorCorridor> {
  const corridorsById = new Map<string, SameColorCorridor>();
  for (const feature of corridorFeatures) {
    corridorsById.set(String(feature.properties.corridor_id), ({
    corridor_id: propertyString((feature).properties.corridor_id) ?? "",
    color: (feature).properties.color == null ? undefined : String((feature).properties.color),
    route_ids: routeIdsOf((feature).properties),
    geometry: (feature).geometry,
    length_m: propertyNumber((feature).properties.length_m) ?? null,
}));
  }
  return corridorsById;
}

function appliedMergeDebugFeature(
  group: SameColorMergeGroup,
  result: SameColorMergeAppliedResult,
  trunkFeature: LineFeature | undefined,
): SameColorMergeDebugFeature {
  const branchConnectors: Array<{ corridor_id: string; distance_m: number; endpoint_kind: string }> = [];
  for (const update of result.branchUpdates) {
    if (!update.connector) continue;
    branchConnectors.push({
      corridor_id: update.corridor_id,
      distance_m: update.connector.distance_m,
      endpoint_kind: update.connector.endpoint_kind,
    });
  }
  return {
    type: "Feature",
    geometry: trunkFeature ? trunkFeature.geometry : null,
    properties: {
      visual_feature_type: "same_color_merge",
      color: group.color,
      trunk_corridor_id: result.trunkUpdates.corridor_id,
      merged_from_corridor_ids: result.trunkUpdates.merged_from_corridor_ids,
      branches_clipped: result.branchUpdates.filter((update) => !update.drop).map((update) => update.corridor_id),
      branches_dropped: result.branchUpdates.filter((update) => update.drop).map((update) => update.corridor_id),
      branch_connectors: branchConnectors,
      route_ids_union: result.trunkUpdates.route_ids,
    },
  };
}

function sameColorConnectorFeature(
  update: SameColorMergeAppliedResult["branchUpdates"][number],
  result: SameColorMergeAppliedResult,
  group: SameColorMergeGroup,
  connectorId: string,
): LineFeature | null {
  if (update.drop || !update.connector || (update.connector.coordinates?.length ?? 0) < 2) return null;
  const connectorRouteIds = [...new Set(update.connector.route_ids ?? [])].sort(compareRouteIds);
  const connectorColor = update.connector.color ?? group.color;
  const lengthM = recomputeLengthM(update.connector.coordinates);
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: update.connector.coordinates,
    },
    properties: {
      visual_feature_type: "same_color_branch_connector",
      corridor_id: connectorId,
      route_ids: connectorRouteIds,
      color_route_ids: { [connectorColor]: connectorRouteIds },
      color: connectorColor,
      source_edge_ids: [],
      [SOURCE_POLYLINE_IDS]: [],
      length_m: lengthM,
      max_segment_length_m: lengthM,
      base_geometry_selection: "same_color_branch_connector",
      same_color_connector_for_corridor_id: update.corridor_id,
      same_color_connector_to_trunk_corridor_id: result.trunkUpdates.corridor_id,
      same_color_connector_distance_m: update.connector.distance_m,
      same_color_connector_endpoint_kind: update.connector.endpoint_kind,
    },
  };
}

function clipBranchFeature(
  update: SameColorMergeAppliedResult["branchUpdates"][number],
  corridorFeatures: LineFeature[],
): boolean {
  if (update.drop || !update.newCoords) return false;
  const branchFeature = corridorFeatures.find((feature) => feature.properties.corridor_id === update.corridor_id);
  if (!branchFeature) return false;
  branchFeature.geometry = { type: "LineString", coordinates: update.newCoords };
  branchFeature.properties.clipped_to_branch_only = true;
  branchFeature.properties.length_m = recomputeLengthM(update.newCoords);
  branchFeature.properties.from_anchor_id = null;
  branchFeature.properties.to_anchor_id = null;
  branchFeature.properties.junction_anchor_ids = [];
  return true;
}

function applyMergedGroup(
  result: SameColorMergeAppliedResult,
  group: SameColorMergeGroup,
  corridorFeatures: LineFeature[],
  connectorNumber: number,
): AppliedGroupStats {
  const trunkFeature = corridorFeatures.find(
    (feature) => feature.properties.corridor_id === result.trunkUpdates.corridor_id,
  );
  if (trunkFeature) {
    trunkFeature.properties.route_ids = result.trunkUpdates.route_ids;
    trunkFeature.properties.color_route_ids = result.trunkUpdates.color_route_ids;
    trunkFeature.properties.merged_from_corridor_ids = result.trunkUpdates.merged_from_corridor_ids;
  }
  const branchIdsToDrop = new Set<string>();
  let branchesClipped = 0;
  let branchesDropped = 0;
  let branchConnectorsAdded = 0;
  let nextConnectorNumber = connectorNumber;

  for (const update of result.branchUpdates) {
    if (update.drop) {
      branchIdsToDrop.add(update.corridor_id);
      branchesDropped += 1;
      continue;
    }
    if (clipBranchFeature(update, corridorFeatures)) branchesClipped += 1;
    const connectorId = `same-color-connector-${String(nextConnectorNumber).padStart(5, "0")}`;
    const connector = sameColorConnectorFeature(update, result, group, connectorId);
    if (!connector) continue;
    nextConnectorNumber += 1;
    corridorFeatures.push(connector);
    branchConnectorsAdded += 1;
  }

  if (branchIdsToDrop.size > 0) {
    for (let index = corridorFeatures.length - 1; index >= 0; index -= 1) {
      const corridorId = propertyString(corridorFeatures[index].properties.corridor_id) ?? "";
      if (branchIdsToDrop.has(corridorId)) corridorFeatures.splice(index, 1);
    }
  }

  return {
    branchesClipped,
    branchesDropped,
    branchConnectorsAdded,
    nextConnectorNumber,
    debugFeature: appliedMergeDebugFeature(group, result, trunkFeature),
  };
}

export function applyPhase3dSameColorMergeStage({
  corridorFeatures,
  sameColorMergesGeoJsonPath,
}: Phase3dSameColorMergeStageInput): void {
  const routeCoverageMap = new Map<string, number>();
  for (const feature of corridorFeatures) {
    const routeIds = routeIdsOf(feature.properties);
    if (!feature.properties.color) {
      feature.properties.color = routeIds[0] ? routeColorFor(routeIds[0]) : "#808183";
    }
    for (const routeId of routeIds) {
      routeCoverageMap.set(routeId, (routeCoverageMap.get(routeId) ?? 0) + 1);
    }
  }
  const corridorsById = corridorsByIdFromFeatures(corridorFeatures);
  const { groups: mergeGroups } = groupCorridorsByColorAndOverlap([...corridorsById.values()], {
    sharedFractionMin: 0.55,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  let mergesApplied = 0;
  let branchesClipped = 0;
  let branchesDropped = 0;
  let branchConnectorsAdded = 0;
  let groupsSkipped = 0;
  const debugFeatures: SameColorMergeDebugFeature[] = [];
  let sameColorConnectorNumber = 1;

  for (const group of mergeGroups) {
    const result = mergeSameColorGroup(group, corridorsById, {
      minBranchLenM: 30,
      resampleM: 25,
      avgDistMaxM: 15,
      routeCoverageMap,
    });
    if (result.skipped) {
      groupsSkipped += 1;
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
    const applied = applyMergedGroup(result, group, corridorFeatures, sameColorConnectorNumber);
    sameColorConnectorNumber = applied.nextConnectorNumber;
    branchesClipped += applied.branchesClipped;
    branchesDropped += applied.branchesDropped;
    branchConnectorsAdded += applied.branchConnectorsAdded;
    mergesApplied += 1;
    debugFeatures.push(applied.debugFeature);
  }

  writeFileSync(
    sameColorMergesGeoJsonPath,
    `${JSON.stringify({ type: "FeatureCollection", features: debugFeatures })}\n`,
  );

  console.log(`[visual-network] Phase 3d merges applied:    ${mergesApplied}`);
  console.log(`[visual-network] Phase 3d branches clipped:  ${branchesClipped}`);
  console.log(`[visual-network] Phase 3d branches dropped:  ${branchesDropped}`);
  console.log(`[visual-network] Phase 3d connectors added:  ${branchConnectorsAdded}`);
  console.log(`[visual-network] Phase 3d groups skipped:    ${groupsSkipped}`);
}
