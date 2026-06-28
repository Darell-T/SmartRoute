import type { Feature, LineStringGeometry, Position } from "../types.ts";
import type { StopsById } from "./gtfs-topology.ts";

type GeometryStats = {
  length_m: number;
  direct_distance_m: number;
  sinuosity: number;
  max_segment_length_m: number;
  coordinate_count: number;
  sharp_angle_count: number;
};

export type TopologyEdgeProperties = {
  edge_id: string;
  route_id: string;
  branch_id: string;
  direction_id: string;
  shape_id: null;
  shape_candidate_count: number;
  shape_selection_strategy: "gtfs_topology_only";
  from_stop_id: string;
  from_stop_name: string;
  to_stop_id: string;
  to_stop_name: string;
  length_m: number;
  direct_distance_m: number;
  sinuosity: number;
  max_segment_length_m: number;
  coordinate_count: number;
  sharp_angle_count: number;
  from_projection_dist_m: null;
  to_projection_dist_m: null;
  endpoint_snap_from: false;
  endpoint_snap_to: false;
};

export type TopologyEdgeFeature = Feature<LineStringGeometry, TopologyEdgeProperties>;

export type TopologyEdgeDiagnostics = {
  topology_edges_emitted: number;
  topology_edges_dropped_missing_stop: number;
};

type TopologyBranchForEdges = {
  branch_id: string;
  direction_id: string;
  stop_sequence: string[];
};

type TopologyRouteForEdges = {
  route_id: string;
  branches: TopologyBranchForEdges[];
};

export type TopologyEdgeBuildResult = {
  edgeFeatures: TopologyEdgeFeature[];
  topologyEdgeDiagnostics: TopologyEdgeDiagnostics;
};

export function buildTopologyEdges(
  perRoute: TopologyRouteForEdges[],
  stopsById: StopsById,
  geometryStats: (coords: Position[]) => GeometryStats,
): TopologyEdgeBuildResult {
  const edgeFeatures: TopologyEdgeFeature[] = [];
  const topologyEdgeDiagnostics: TopologyEdgeDiagnostics = {
    topology_edges_emitted: 0,
    topology_edges_dropped_missing_stop: 0,
  };

  for (const r of perRoute) {
    for (const branch of r.branches) {
      const stops = branch.stop_sequence;

      for (let i = 0; i < stops.length - 1; i += 1) {
        const p1 = stopsById.get(stops[i]);
        const p2 = stopsById.get(stops[i + 1]);
        if (!p1 || !p2) {
          topologyEdgeDiagnostics.topology_edges_dropped_missing_stop += 1;
          continue;
        }
        const topologyGeometry: Position[] = [[p1.lon, p1.lat], [p2.lon, p2.lat]];
        const stats = geometryStats(topologyGeometry);

        edgeFeatures.push({
          type: "Feature",
          geometry: { type: "LineString", coordinates: topologyGeometry },
          properties: {
            edge_id: `${branch.branch_id}__${p1.stop_id}__${p2.stop_id}`,
            route_id: r.route_id,
            branch_id: branch.branch_id,
            direction_id: branch.direction_id,
            shape_id: null,
            shape_candidate_count: 0,
            shape_selection_strategy: "gtfs_topology_only",
            from_stop_id: p1.stop_id,
            from_stop_name: p1.name,
            to_stop_id: p2.stop_id,
            to_stop_name: p2.name,
            length_m: stats.length_m,
            direct_distance_m: stats.direct_distance_m,
            sinuosity: stats.sinuosity,
            max_segment_length_m: stats.max_segment_length_m,
            coordinate_count: stats.coordinate_count,
            sharp_angle_count: stats.sharp_angle_count,
            from_projection_dist_m: null,
            to_projection_dist_m: null,
            endpoint_snap_from: false,
            endpoint_snap_to: false,
          },
        });
        topologyEdgeDiagnostics.topology_edges_emitted += 1;
      }
    }
  }

  return { edgeFeatures, topologyEdgeDiagnostics };
}
