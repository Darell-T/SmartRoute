import type { LineFeature } from "./types.ts";
import { geometryStats } from "./geometry-utils.ts";

const ROUTE_FAMILY_GROUPS = [
  ["1", "2", "3"],
  ["4", "5", "6", "6X"],
  ["A", "C", "E"],
  ["B", "D", "F", "FX", "M"],
  ["N", "Q", "R", "W"],
  ["J", "Z", "M"],
  ["7", "7X"],
  ["S"],
  ["FS"],
  ["GS"],
  ["H"],
  ["SI"],
  ["L"],
  ["G"],
];

function routeFamilyKey(routeId: string) {
  for (const group of ROUTE_FAMILY_GROUPS) {
    if (group.includes(routeId)) return group.join("/");
  }
  return routeId;
}

export function buildRouteIncidentCounts(features: LineFeature[], useSourceEdges = false) {
  const counts = new Map();
  const add = (stopId: any, stopName: any, routeId: any, corridorId: any) => {
    const key = `${stopId}|${routeId}`;
    if (!counts.has(key)) {
      counts.set(key, {
        stop_id: stopId,
        stop_name: stopName,
        route_id: routeId,
        corridor_ids: new Set(),
        count: 0,
      });
    }
    const row = counts.get(key);
    row.count += 1;
    if (corridorId) row.corridor_ids.add(corridorId);
  };

  for (const feature of features) {
    const props = feature.properties;
    const routeIds = useSourceEdges
      ? [props.route_id]
      : props.route_ids ?? [];
    for (const routeId of routeIds) {
      add(props.from_stop_id, props.from_stop_name, routeId, props.corridor_id);
      add(props.to_stop_id, props.to_stop_name, routeId, props.corridor_id);
    }
  }

  return counts;
}

export function buildVisualRouteIncidentCounts(
  features: LineFeature[],
  edgeById: Map<any, any>,
) {
  const counts = new Map();
  const add = (stopId: any, stopName: any, routeId: any, corridorId: any) => {
    const key = `${stopId}|${routeId}`;
    if (!counts.has(key)) {
      counts.set(key, {
        stop_id: stopId,
        stop_name: stopName,
        route_id: routeId,
        corridor_ids: new Set(),
        count: 0,
      });
    }
    const row = counts.get(key);
    row.count += 1;
    if (corridorId) row.corridor_ids.add(corridorId);
  };

  for (const feature of features) {
    const props = feature.properties;
    const routeIds = new Set(props.route_ids ?? []);
    const sourceEdges = (props.source_edge_ids ?? [])
      .map((edgeId: any) => edgeById.get(edgeId))
      .filter(Boolean);

    if (sourceEdges.length > 0) {
      for (const edge of sourceEdges) {
        const routeId = edge.properties.route_id;
        if (!routeIds.has(routeId)) continue;
        add(
          edge.properties.from_stop_id,
          edge.properties.from_stop_name,
          routeId,
          props.corridor_id,
        );
        add(
          edge.properties.to_stop_id,
          edge.properties.to_stop_name,
          routeId,
          props.corridor_id,
        );
      }
      continue;
    }

    for (const routeId of routeIds) {
      add(props.from_stop_id, props.from_stop_name, routeId, props.corridor_id);
      add(props.to_stop_id, props.to_stop_name, routeId, props.corridor_id);
    }
  }

  return counts;
}

function hasUnrelatedRouteFamilyMix(routeIds: string[]) {
  if (routeIds.length <= 1) return false;
  return new Set(routeIds.map(routeFamilyKey)).size > 2;
}

function anomalyReasonsForFeature(
  feature: LineFeature,
  edgeById: Map<any, any>,
  thresholds: {
    maxSegmentAnomalyM: number;
    sparseLongSliceM: number;
    projectionAnomalyM: number;
  },
) {
  const props = feature.properties;
  const stats = geometryStats(feature.geometry.coordinates);
  const sourceEdges = (props.source_edge_ids ?? [])
    .map((edgeId: any) => edgeById.get(edgeId))
    .filter(Boolean);
  const maxProjectionDistanceM = sourceEdges.reduce((max: number, edge: any) => {
    return Math.max(
      max,
      Number(edge.properties.from_projection_dist_m ?? 0),
      Number(edge.properties.to_projection_dist_m ?? 0),
    );
  }, 0);
  const reasons = [];

  if (stats.max_segment_length_m > thresholds.maxSegmentAnomalyM) {
    reasons.push("max_segment_gt_250m");
  }
  if (stats.coordinate_count <= 2 && stats.length_m > thresholds.sparseLongSliceM) {
    reasons.push("sparse_long_slice");
  }
  if (maxProjectionDistanceM > thresholds.projectionAnomalyM) {
    reasons.push("projection_gt_125m");
  }
  if (stats.sharp_angle_count > 0) {
    reasons.push("sharp_angle_gt_120deg");
  }
  if (
    stats.coordinate_count <= 3 &&
    stats.length_m > 600 &&
    stats.sinuosity < 1.03
  ) {
    reasons.push("low_detail_straight_long_slice");
  }
  if (hasUnrelatedRouteFamilyMix(props.route_ids ?? [])) {
    reasons.push("unrelated_route_family_mix");
  }

  const severity =
    Math.max(0, stats.max_segment_length_m - thresholds.maxSegmentAnomalyM) / 25 +
    Math.max(0, maxProjectionDistanceM - thresholds.projectionAnomalyM) / 10 +
    stats.sharp_angle_count * 3 +
    (stats.coordinate_count <= 2 && stats.length_m > thresholds.sparseLongSliceM
      ? 20
      : 0) +
    (hasUnrelatedRouteFamilyMix(props.route_ids ?? []) ? 5 : 0);

  return {
    reasons,
    severity: Number(severity.toFixed(2)),
    stats,
    max_projection_distance_m: Number(maxProjectionDistanceM.toFixed(2)),
    source_edges: sourceEdges,
  };
}

export function buildVisualAnomalyRecords(
  features: LineFeature[],
  edgeById: Map<any, any>,
  thresholds: {
    maxSegmentAnomalyM: number;
    sparseLongSliceM: number;
    projectionAnomalyM: number;
  },
): any[] {
  return features.map((feature) => {
    const result = anomalyReasonsForFeature(feature, edgeById, thresholds);
    if (result.reasons.length === 0) return null;
    const props = feature.properties;
    return {
      feature,
      reasons: result.reasons,
      severity: result.severity,
      stats: result.stats,
      max_projection_distance_m: result.max_projection_distance_m,
      shape_ids: [
        ...new Set<string>(result.source_edges.map((edge: any) => edge.properties.shape_id)),
      ].sort((a, b) => a.localeCompare(b, "en", { numeric: true })),
      stop_pairs: result.source_edges
        .slice(0, 12)
        .map(
          (edge: any) =>
            `${edge.properties.from_stop_name} → ${edge.properties.to_stop_name}`,
        ),
      source_edge_ids: props.source_edge_ids ?? [],
    };
  })
  .filter(Boolean)
  .sort((a: any, b: any) => b.severity - a.severity);
}
