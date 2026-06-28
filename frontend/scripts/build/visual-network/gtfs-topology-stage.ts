import { parseCsv } from "./gtfs-ingest.ts";
import {
  buildRoutesByRawId,
  buildStopsById,
  buildTripsById,
  buildTripStations,
  type StopsById,
} from "./gtfs-topology.ts";
import {
  buildBranchesByRoute,
  type Branch,
  type BranchesByRoute,
} from "./branch-selection.ts";
import {
  buildTopologyEdges,
  type TopologyEdgeDiagnostics,
  type TopologyEdgeFeature,
} from "./topology-edges.ts";

type GeometryStats = Parameters<typeof buildTopologyEdges>[2];

export type TopologyBranchSummary = {
  branch_id: string;
  direction_id: string;
  terminal_start: string;
  terminal_start_name: string;
  terminal_end: string;
  terminal_end_name: string;
  stop_count: number;
  total_trips_in_branch: number;
  canonical_pattern_trips: number;
  canonical_pattern_share: number;
  distinct_patterns: number;
  sample_shape_ids: string[];
  sample_headsigns: string[];
  stop_sequence: string[];
};

export type TopologyRouteSummary = {
  route_id: string;
  branch_count: number;
  distinct_stations: number;
  branches: TopologyBranchSummary[];
};

export type TopologyDoc = {
  generated_at: string;
  source: "build-subway-visual-network.mjs Gate 2A";
  parameters: {
    min_trips_per_branch: number;
  };
  gtfs_input: {
    stops: number;
    trips: number;
    stop_times: number;
    routes: number;
  };
  topology: {
    distinct_routes: number;
    total_branches: number;
    dropped_low_freq_branches: number;
  };
  per_route: TopologyRouteSummary[];
};

export type GtfsTopologyStageInput = {
  gtfs: Map<string, string>;
  minTripsPerBranch: number;
  normalizeRouteId: (value: string) => string;
  geometryStats: GeometryStats;
  log?: (message: string) => void;
};

export type GtfsTopologyStageResult = {
  stopsById: StopsById;
  branchesByRoute: BranchesByRoute;
  droppedLowFreqBranches: number;
  topologyDoc: TopologyDoc;
  edgeFeatures: TopologyEdgeFeature[];
  topologyEdgeDiagnostics: TopologyEdgeDiagnostics;
  expectedOpenDataRouteIds: string[];
  expectedEdges: number;
};

export function buildGtfsTopologyStage({
  gtfs,
  minTripsPerBranch,
  normalizeRouteId,
  geometryStats,
  log,
}: GtfsTopologyStageInput): GtfsTopologyStageResult {
  log?.("[visual-network] parsing stops.txt");
  const stopRows = parseCsv(gtfs.get("stops.txt")!);
  log?.("[visual-network] parsing trips.txt");
  const tripRows = parseCsv(gtfs.get("trips.txt")!);
  log?.("[visual-network] parsing stop_times.txt");
  const stopTimeRows = parseCsv(gtfs.get("stop_times.txt")!);
  log?.("[visual-network] parsing routes.txt");
  const routeRows = parseCsv(gtfs.get("routes.txt")!);
  log?.(
    `[visual-network] gtfs sizes: stops=${stopRows.length}, ` +
    `trips=${tripRows.length}, stop_times=${stopTimeRows.length}, ` +
    `routes=${routeRows.length}`,
  );

  const stopsById = buildStopsById(stopRows);

  const routesByRawId = buildRoutesByRawId(routeRows, normalizeRouteId);

  const tripsById = buildTripsById(tripRows, routesByRawId);

  log?.("[visual-network] building per-trip station sequences");

  const tripStations = buildTripStations(stopTimeRows, stopsById);

  log?.("[visual-network] grouping trips into branches");
  const { branchesByRoute, droppedLowFreqBranches } = buildBranchesByRoute(
    tripsById,
    tripStations,
    minTripsPerBranch,
  );

  const topologyDoc: TopologyDoc = {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2A",
    parameters: {
      min_trips_per_branch: minTripsPerBranch,
    },
    gtfs_input: {
      stops: stopRows.length,
      trips: tripRows.length,
      stop_times: stopTimeRows.length,
      routes: routeRows.length,
    },
    topology: {
      distinct_routes: branchesByRoute.size,
      total_branches: [...branchesByRoute.values()].reduce((a, b) => a + b.length, 0),
      dropped_low_freq_branches: droppedLowFreqBranches,
    },
    per_route: [...branchesByRoute.entries()]
      .sort((a, b) => a[0].localeCompare(b[0], "en", { numeric: true }))
      .map(([routeId, branches]) => {
        const allStations = new Set<string>();
        for (const b of branches) for (const s of b.stop_sequence) allStations.add(s);
        return {
          route_id: routeId,
          branch_count: branches.length,
          distinct_stations: allStations.size,
          branches: branches.map((b: Branch) => ({
            branch_id: b.branch_id,
            direction_id: b.direction_id,
            terminal_start: b.terminal_start,
            terminal_start_name: stopsById.get(b.terminal_start)?.name ?? b.terminal_start,
            terminal_end: b.terminal_end,
            terminal_end_name: stopsById.get(b.terminal_end)?.name ?? b.terminal_end,
            stop_count: b.stop_sequence.length,
            total_trips_in_branch: b.total_trips_in_branch,
            canonical_pattern_trips: b.canonical_pattern_trips,
            canonical_pattern_share: b.canonical_pattern_share,
            distinct_patterns: b.distinct_patterns,
            sample_shape_ids: b.sample_shape_ids,
            sample_headsigns: b.sample_headsigns,
            stop_sequence: b.stop_sequence,
          })),
        };
      }),
  };

  const { edgeFeatures, topologyEdgeDiagnostics } = buildTopologyEdges(
    topologyDoc.per_route,
    stopsById,
    geometryStats,
  );

  const expectedOpenDataRouteIds = topologyDoc.per_route.map((route) => route.route_id);
  const expectedEdges = topologyDoc.per_route.reduce(
    (acc, r) =>
      acc + r.branches.reduce((br, b) => br + Math.max(0, b.stop_count - 1), 0),
    0,
  );

  return {
    stopsById,
    branchesByRoute,
    droppedLowFreqBranches,
    topologyDoc,
    edgeFeatures,
    topologyEdgeDiagnostics,
    expectedOpenDataRouteIds,
    expectedEdges,
  };
}
