import type { FeatureProps, LineFeature } from "./types.ts";
import { geometryStats } from "./geometry-utils.ts";
import { isJsonString, propertyString, routeIdsOf } from "./route-config.ts";

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

type TopologyEdge = LineFeature;

type EdgeLookup = {
  get(edgeId: string): TopologyEdge | undefined;
};

type IncidentRow = {
  stop_id: string;
  stop_name: string;
  route_id: string;
  corridor_ids: Set<string>;
  count: number;
};

type AnomalyThresholds = {
  maxSegmentAnomalyM: number;
  sparseLongSliceM: number;
  projectionAnomalyM: number;
};

type GeometryStats = ReturnType<typeof geometryStats>;

type VisualAnomalyRecord = {
  feature: LineFeature;
  reasons: string[];
  severity: number;
  stats: GeometryStats;
  max_projection_distance_m: number;
  gtfsPolylineIds: string[];
  stop_pairs: string[];
  source_edge_ids: string[];
};

function addIncident(
  counts: Map<string, IncidentRow>,
  stopId: string,
  stopName: string,
  routeId: string,
  corridorId: string | undefined,
) {
  const key = `${stopId}|${routeId}`;
  let row = counts.get(key);
  if (!row) {
    row = {
      stop_id: stopId,
      stop_name: stopName,
      route_id: routeId,
      corridor_ids: new Set(),
      count: 0,
    };
    counts.set(key, row);
  }
  row.count += 1;
  if (corridorId) row.corridor_ids.add(corridorId);
}

export function buildRouteIncidentCounts(features: LineFeature[], useSourceEdges = false) {
  const counts = new Map<string, IncidentRow>();

  for (const feature of features) {
    const props = feature.properties;
    const routeIds = useSourceEdges
      ? [props.route_id]
      : routeIdsOf(props);
    for (const routeId of routeIds) {
      addIncident(
        counts,
        incidentStopText(props.from_stop_id),
        incidentStopText(props.from_stop_name),
        incidentStopText(routeId),
        propertyString(props.corridor_id),
      );
      addIncident(
        counts,
        incidentStopText(props.to_stop_id),
        incidentStopText(props.to_stop_name),
        incidentStopText(routeId),
        propertyString(props.corridor_id),
      );
    }
  }

  return counts;
}

function sourceEdgeIdsOf(properties: FeatureProps): string[] {
  return Array.isArray(properties.source_edge_ids)
    ? properties.source_edge_ids.map(String)
    : [];
}

function incidentStopText(value: FeatureProps[string] | undefined): string {
  return String(value ?? "");
}

function addStopPairIncidents(
  counts: Map<string, IncidentRow>,
  fromId: string,
  fromName: string,
  toId: string,
  toName: string,
  routeId: string,
  corridorId: string | undefined,
): void {
  addIncident(counts, fromId, fromName, routeId, corridorId);
  addIncident(counts, toId, toName, routeId, corridorId);
}

export function buildVisualRouteIncidentCounts(
  features: LineFeature[],
  edgeById: EdgeLookup,
) {
  const counts = new Map<string, IncidentRow>();

  for (const feature of features) {
    const props = feature.properties;
    const routeIds = new Set(routeIdsOf(props));
    const sourceEdges = sourceEdgesForFeature(feature, edgeById);

    if (sourceEdges.length > 0) {
      for (const edge of sourceEdges) {
        const routeId = edge.properties.route_id;
        if (!isJsonString(routeId) || !routeIds.has(routeId)) continue;
        addStopPairIncidents(
          counts,
          incidentStopText(edge.properties.from_stop_id),
          incidentStopText(edge.properties.from_stop_name),
          incidentStopText(edge.properties.to_stop_id),
          incidentStopText(edge.properties.to_stop_name),
          routeId,
          propertyString(props.corridor_id),
        );
      }
      continue;
    }

    for (const routeId of routeIds) {
      addStopPairIncidents(
        counts,
        incidentStopText(props.from_stop_id),
        incidentStopText(props.from_stop_name),
        incidentStopText(props.to_stop_id),
        incidentStopText(props.to_stop_name),
        routeId,
        propertyString(props.corridor_id),
      );
    }
  }

  return counts;
}

function hasUnrelatedRouteFamilyMix(routeIds: string[]) {
  if (routeIds.length <= 1) return false;
  return new Set(routeIds.map(routeFamilyKey)).size > 2;
}

function sourceEdgesForFeature(feature: LineFeature, edgeById: EdgeLookup): TopologyEdge[] {
  return sourceEdgeIdsOf(feature.properties)
    .map((edgeId) => edgeById.get(edgeId))
    .filter((edge: TopologyEdge | undefined): edge is TopologyEdge => Boolean(edge));
}

function maxProjectionDistanceM(sourceEdges: TopologyEdge[]): number {
  return sourceEdges.reduce((max, edge) => {
    return Math.max(
      max,
      Number(edge.properties.from_projection_dist_m ?? 0),
      Number(edge.properties.to_projection_dist_m ?? 0),
    );
  }, 0);
}

function collectAnomalyReasons(
  stats: GeometryStats,
  maxProjection: number,
  routeIds: string[],
  thresholds: AnomalyThresholds,
): string[] {
  const reasons = [];
  if (stats.max_segment_length_m > thresholds.maxSegmentAnomalyM) {
    reasons.push("max_segment_gt_250m");
  }
  if (stats.coordinate_count <= 2 && stats.length_m > thresholds.sparseLongSliceM) {
    reasons.push("sparse_long_slice");
  }
  if (maxProjection > thresholds.projectionAnomalyM) {
    reasons.push("projection_gt_125m");
  }
  if (stats.sharp_angle_count > 0) {
    reasons.push("sharp_angle_gt_120deg");
  }
  if (stats.coordinate_count <= 3 && stats.length_m > 600 && stats.sinuosity < 1.03) {
    reasons.push("low_detail_straight_long_slice");
  }
  if (hasUnrelatedRouteFamilyMix(routeIds)) {
    reasons.push("unrelated_route_family_mix");
  }
  return reasons;
}

function anomalyReasonsForFeature(
  feature: LineFeature,
  edgeById: EdgeLookup,
  thresholds: AnomalyThresholds,
) {
  const props = feature.properties;
  const stats = geometryStats(feature.geometry.coordinates);
  const sourceEdges = sourceEdgesForFeature(feature, edgeById);
  const maxProjection = maxProjectionDistanceM(sourceEdges);
  const routeIds = routeIdsOf(props);
  const severity =
    Math.max(0, stats.max_segment_length_m - thresholds.maxSegmentAnomalyM) / 25 +
    Math.max(0, maxProjection - thresholds.projectionAnomalyM) / 10 +
    stats.sharp_angle_count * 3 +
    (stats.coordinate_count <= 2 && stats.length_m > thresholds.sparseLongSliceM ? 20 : 0) +
    (hasUnrelatedRouteFamilyMix(routeIds) ? 5 : 0);
  return {
    reasons: collectAnomalyReasons(stats, maxProjection, routeIds, thresholds),
    severity: Number(severity.toFixed(2)),
    stats,
    max_projection_distance_m: Number(maxProjection.toFixed(2)),
    source_edges: sourceEdges,
  };
}

export function buildVisualAnomalyRecords(
  features: LineFeature[],
  edgeById: EdgeLookup,
  thresholds: AnomalyThresholds,
): VisualAnomalyRecord[] {
  return features.map((feature) => {
    const result = anomalyReasonsForFeature(feature, edgeById, thresholds);
    if (result.reasons.length === 0) return null;
    const props = feature.properties;
    const record: VisualAnomalyRecord = {
      feature,
      reasons: result.reasons,
      severity: result.severity,
      stats: result.stats,
      max_projection_distance_m: result.max_projection_distance_m,
      gtfsPolylineIds: [
        ...new Set<string>(result.source_edges.map((edge) => String(edge.properties["shape_id"] ?? ""))),
      ].sort((a, b) => a.localeCompare(b, "en", { numeric: true })),
      stop_pairs: result.source_edges
        .slice(0, 12)
        .map(
          (edge) =>
            `${edge.properties.from_stop_name} → ${edge.properties.to_stop_name}`,
        ),
      source_edge_ids: sourceEdgeIdsOf(props),
    };
    return record;
  })
  .filter((row): row is VisualAnomalyRecord => row !== null)
  .sort((a, b) => b.severity - a.severity);
}
