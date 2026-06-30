import { writeFileSync } from "node:fs";
import { groupCorridorsByColorAndOverlap, mergeSameColorGroup } from "../../same-color-merge.ts";
import { compareRouteIds, routeColorFor } from "../shared/route-config.ts";
import type { LineFeature, Position } from "../shared/types.ts";

type Phase3dSameColorMergeStageInput = {
  corridorFeatures: LineFeature[];
  sameColorMergesGeoJsonPath: string;
};

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

export function applyPhase3dSameColorMergeStage({
  corridorFeatures,
  sameColorMergesGeoJsonPath,
}: Phase3dSameColorMergeStageInput): void {
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
      sameColorMergesGeoJsonPath,
      `${JSON.stringify({ type: "FeatureCollection", features: debugFeatures })}\n`,
    );

    console.log(`[visual-network] Phase 3d merges applied:    ${mergesApplied}`);
    console.log(`[visual-network] Phase 3d branches clipped:  ${branchesClipped}`);
    console.log(`[visual-network] Phase 3d branches dropped:  ${branchesDropped}`);
    console.log(`[visual-network] Phase 3d connectors added:  ${branchConnectorsAdded}`);
    console.log(`[visual-network] Phase 3d groups skipped:    ${groupsSkipped}`);
  }
}
