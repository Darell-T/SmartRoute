import { distanceMeters } from "./geometry-utils.ts";
import { colorRank, routeColorFor } from "./route-config.ts";
import type { LineFeature, PointFeat } from "./types.ts";

type CorridorMetadataStageInput = {
  corridorFeatures: LineFeature[];
  junctionSnapMaxM: number;
};

function endpointClusterKey(stopId: string, index: number) {
  return `${stopId}#${index}`;
}

function clusterEndpointEntries(entries: any[], junctionSnapMaxM: number) {
  const clusters: any[] = [];
  for (const entry of entries) {
    let target = null;
    for (const cluster of clusters) {
      if (
        cluster.entries.some(
          (existing: any) =>
            distanceMeters(existing.coordinate, entry.coordinate) <=
            junctionSnapMaxM,
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

function applyJunctionAnchorSnaps(features: LineFeature[], junctionSnapMaxM: number) {
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
    const clusters = clusterEndpointEntries(entries, junctionSnapMaxM);
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
        if (snapDistanceM > junctionSnapMaxM) continue;
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

export function buildCorridorMetadataStage({
  corridorFeatures,
  junctionSnapMaxM,
}: CorridorMetadataStageInput) {
  const junctionSnapDiagnostics = applyJunctionAnchorSnaps(corridorFeatures, junctionSnapMaxM);
  const laneChainDiagnostics = applyLaneChainMetadata(corridorFeatures);
  return { junctionSnapDiagnostics, laneChainDiagnostics };
}
