import { distanceMeters } from "../shared/geometry-utils.ts";
import { colorRank, propertyKey, propertyString, routeColorFor, routeIdsOf, stringListOf } from "../shared/route-config.ts";
import type { LineFeature, PointFeat, Position } from "../shared/types.ts";

type CorridorMetadataStageInput = {
  corridorFeatures: LineFeature[];
  junctionSnapMaxM: number;
};

type EndpointKind = "from" | "to";

type EndpointEntry = {
  feature: LineFeature;
  kind: EndpointKind;
  stop_id: string;
  stop_name: string;
  coordinate: Position;
};

type EndpointCluster = {
  entries: EndpointEntry[];
  coordinate: Position;
};

type AnchorSnap = {
  anchorId: string;
  coordinate: Position;
};

const GEOMETRY_ENDPOINT_KEY = "__opendata_geometry_endpoints__";

function endpointClusterKey(stopId: string, index: number) {
  return `${stopId}#${index}`;
}

function clusterEndpointEntries(entries: EndpointEntry[], junctionSnapMaxM: number) {
  const clusters: EndpointCluster[] = [];
  for (const entry of entries) {
    const target = clusters.find((cluster) =>
      cluster.entries.some(
        (existing) => distanceMeters(existing.coordinate, entry.coordinate) <= junctionSnapMaxM,
      ),
    );
    if (!target) {
      clusters.push({ entries: [entry], coordinate: entry.coordinate });
      continue;
    }
    target.entries.push(entry);
    target.coordinate = [
      target.entries.reduce((sum, item) => sum + item.coordinate[0], 0) / target.entries.length,
      target.entries.reduce((sum, item) => sum + item.coordinate[1], 0) / target.entries.length,
    ];
  }
  return clusters;
}

function snapOneCluster(
  stopId: string,
  cluster: EndpointCluster,
  clusterIndex: number,
  junctionSnapMaxM: number,
  anchorFeatures: PointFeat[],
  snapFeatures: LineFeature[],
  anchorByFeatureEndpoint: Map<string, AnchorSnap>,
) {
  const anchorId =
    stopId === GEOMETRY_ENDPOINT_KEY
      ? `opendata-anchor#${clusterIndex}`
      : endpointClusterKey(stopId, clusterIndex);
  anchorFeatures.push({
    type: "Feature",
    geometry: { type: "Point", coordinates: cluster.coordinate },
    properties: {
      anchor_id: anchorId,
      stop_id: stopId === GEOMETRY_ENDPOINT_KEY ? null : stopId,
      stop_name: cluster.entries[0]?.stop_name ?? "",
      endpoint_count: cluster.entries.length,
      anchor_source: stopId === GEOMETRY_ENDPOINT_KEY ? "geometry_endpoint" : "gtfs_stop",
    },
  });
  for (const entry of cluster.entries) {
    const snapDistanceM = distanceMeters(entry.coordinate, cluster.coordinate);
    if (snapDistanceM > junctionSnapMaxM) continue;
    const key = `${entry.feature.properties.corridor_id}:${entry.kind}`;
    anchorByFeatureEndpoint.set(key, { anchorId, coordinate: cluster.coordinate });
    const coords = entry.feature.geometry.coordinates;
    const original = entry.kind === "from" ? coords[0] : coords[coords.length - 1];
    if (snapDistanceM <= 0.01) continue;
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

function stampJunctionAnchors(features: LineFeature[], anchorByFeatureEndpoint: Map<string, AnchorSnap>) {
  for (const feature of features) {
    const fromAnchor = anchorByFeatureEndpoint.get(`${feature.properties.corridor_id}:from`);
    const toAnchor = anchorByFeatureEndpoint.get(`${feature.properties.corridor_id}:to`);
    feature.properties.from_anchor_id = fromAnchor?.anchorId ?? null;
    feature.properties.to_anchor_id = toAnchor?.anchorId ?? null;
    const junctionAnchorIds: string[] = [];
    if (fromAnchor?.anchorId) junctionAnchorIds.push(fromAnchor.anchorId);
    if (toAnchor?.anchorId) junctionAnchorIds.push(toAnchor.anchorId);
    feature.properties.junction_anchor_ids = junctionAnchorIds;
  }
}

function applyJunctionAnchorSnaps(features: LineFeature[], junctionSnapMaxM: number) {
  const entriesByStop = new Map<string, EndpointEntry[]>();
  for (const feature of features) {
    const coords = feature.geometry.coordinates;
    const endpoints: EndpointEntry[] = [
      {
        feature,
        kind: "from",
        stop_id: propertyKey(feature.properties.from_stop_id),
        stop_name: propertyString(feature.properties.from_stop_name) ?? "",
        coordinate: coords[0],
      },
      {
        feature,
        kind: "to",
        stop_id: propertyKey(feature.properties.to_stop_id),
        stop_name: propertyString(feature.properties.to_stop_name) ?? "",
        coordinate: coords[coords.length - 1],
      },
    ];
    for (const endpoint of endpoints) {
      if (!endpoint.coordinate) continue;
      const key = endpoint.stop_id || GEOMETRY_ENDPOINT_KEY;
      const bucket = entriesByStop.get(key);
      const resolved = { ...endpoint, stop_id: endpoint.stop_id ?? key };
      if (bucket) bucket.push(resolved);
      else entriesByStop.set(key, [resolved]);
    }
  }
  const anchorFeatures: PointFeat[] = [];
  const snapFeatures: LineFeature[] = [];
  const anchorByFeatureEndpoint = new Map<string, AnchorSnap>();
  for (const [stopId, entries] of entriesByStop) {
    const clusters = clusterEndpointEntries(entries, junctionSnapMaxM);
    clusters.forEach((cluster, clusterIndex) => {
      snapOneCluster(
        stopId,
        cluster,
        clusterIndex,
        junctionSnapMaxM,
        anchorFeatures,
        snapFeatures,
        anchorByFeatureEndpoint,
      );
    });
  }
  stampJunctionAnchors(features, anchorByFeatureEndpoint);
  return { anchorFeatures, snapFeatures };
}

function colorGroupsForRoutes(routeIds: string[]) {
  return [...new Set(routeIds.map((routeId) => routeColorFor(routeId)))].sort(
    (a, b) => colorRank(a) - colorRank(b),
  );
}

function unionSharedColorNeighbors(features: LineFeature[], union: (a: number, b: number) => void) {
  const featureIndexByAnchor = new Map<string, number[]>();
  features.forEach((feature, index) => {
    for (const anchorId of stringListOf(feature.properties.junction_anchor_ids)) {
      const bucket = featureIndexByAnchor.get(anchorId);
      if (bucket) bucket.push(index);
      else featureIndexByAnchor.set(anchorId, [index]);
    }
  });
  for (const indices of featureIndexByAnchor.values()) {
    for (let leftIndex = 0; leftIndex < indices.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < indices.length; rightIndex += 1) {
        const left = features[indices[leftIndex]];
        const right = features[indices[rightIndex]];
        const leftColors = new Set(colorGroupsForRoutes(routeIdsOf(left.properties)));
        const rightColors = colorGroupsForRoutes(routeIdsOf(right.properties));
        if (rightColors.some((color) => leftColors.has(color))) {
          union(indices[leftIndex], indices[rightIndex]);
        }
      }
    }
  }
  return;
}

function applyLaneChainMetadata(features: LineFeature[]) {
  const parent = new Int32Array(features.length);
  for (let i = 0; i < parent.length; i += 1) parent[i] = i;
  const find = (x: number) => {
    let root = x;
    while (parent[root] !== root) root = parent[root];
    while (parent[x] !== root) {
      const next = parent[x];
      parent[x] = root;
      x = next;
    }
    return root;
  };
  unionSharedColorNeighbors(features, (left, right) => {
    const leftRoot = find(left);
    const rightRoot = find(right);
    if (leftRoot !== rightRoot) parent[leftRoot] = rightRoot;
  });

  const groups = new Map<number, number[]>();
  features.forEach((_feature, index) => {
    const root = find(index);
    const bucket = groups.get(root);
    if (bucket) bucket.push(index);
    else groups.set(root, [index]);
  });
  let groupId = 1;
  for (const indices of groups.values()) {
    const groupColors = [
      ...new Set(indices.flatMap((index) => colorGroupsForRoutes(routeIdsOf(features[index].properties)))),
    ].sort((a, b) => colorRank(a) - colorRank(b));
    const laneGroupId = `lane-group-${String(groupId++).padStart(4, "0")}`;
    for (const index of indices) {
      const feature = features[index];
      const localColors = colorGroupsForRoutes(routeIdsOf(feature.properties));
      const slots = Object.fromEntries(
        localColors.map((color, colorIndex) => [color, colorIndex - (localColors.length - 1) / 2]),
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
    chain_slot_feature_count: features.filter((feature) => feature.properties.lane_slot_source === "chain").length,
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
