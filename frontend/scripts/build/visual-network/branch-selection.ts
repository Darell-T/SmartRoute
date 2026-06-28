import type { TripsById, TripStations } from "./gtfs-topology.ts";

type BranchPattern = {
  sequence: string[];
  count: number;
  sample_trip_ids: string[];
  sample_shape_ids: Set<string>;
};

type BranchAccumulator = {
  route_id: string;
  direction_id: string;
  terminal_start: string;
  terminal_end: string;
  patterns: Map<string, BranchPattern>;
  total_trips: number;
  sample_headsigns: Set<string>;
};

export type Branch = {
  branch_id: string;
  route_id: string;
  direction_id: string;
  terminal_start: string;
  terminal_end: string;
  total_trips_in_branch: number;
  canonical_pattern_trips: number;
  canonical_pattern_share: number;
  distinct_patterns: number;
  stop_sequence: string[];
  sample_trip_ids: string[];
  sample_shape_ids: string[];
  sample_headsigns: string[];
};

export type BranchesByRoute = Map<string, Branch[]>;

export type BranchSelection = {
  branchesByRoute: BranchesByRoute;
  droppedLowFreqBranches: number;
};

export function buildBranchesByRoute(
  tripsById: TripsById,
  tripStations: TripStations,
  minTripsPerBranch: number,
): BranchSelection {
  const branchAccum = new Map<string, BranchAccumulator>(); // key -> { route_id, direction_id, terminals, patterns: Map<sigHash, {pattern, count}>, totalTrips }

  for (const trip of tripsById.values()) {
    const sequence = tripStations.get(trip.trip_id);
    if (!sequence) continue;
    const terminalStart = sequence[0];
    const terminalEnd = sequence[sequence.length - 1];
    const key = `${trip.route_id}|${trip.direction_id}|${terminalStart}→${terminalEnd}`;
    if (!branchAccum.has(key)) {
      branchAccum.set(key, {
        route_id: trip.route_id,
        direction_id: trip.direction_id,
        terminal_start: terminalStart,
        terminal_end: terminalEnd,
        patterns: new Map(),
        total_trips: 0,
        sample_headsigns: new Set(),
      });
    }
    const branch = branchAccum.get(key)!;
    const sig = sequence.join(",");
    if (!branch.patterns.has(sig)) {
      branch.patterns.set(sig, { sequence, count: 0, sample_trip_ids: [], sample_shape_ids: new Set() });
    }
    const p = branch.patterns.get(sig)!;
    p.count += 1;
    if (p.sample_trip_ids.length < 3) p.sample_trip_ids.push(trip.trip_id);
    if (trip.shape_id) p.sample_shape_ids.add(trip.shape_id);
    branch.total_trips += 1;
    if (trip.headsign) branch.sample_headsigns.add(trip.headsign);
  }

  const branchesByRoute: BranchesByRoute = new Map();
  let droppedLowFreqBranches = 0;
  for (const [, branch] of branchAccum) {
    if (branch.total_trips < minTripsPerBranch) {
      droppedLowFreqBranches += 1;
      continue;
    }
    let bestSig: string | null = null;
    let bestCount = -1;
    for (const [sig, p] of branch.patterns) {
      if (p.count > bestCount) { bestCount = p.count; bestSig = sig; }
    }
    const canonical = branch.patterns.get(bestSig!)!;
    if (!branchesByRoute.has(branch.route_id)) {
      branchesByRoute.set(branch.route_id, []);
    }
    branchesByRoute.get(branch.route_id)!.push({
      branch_id: `${branch.route_id}-${branch.direction_id}-${branch.terminal_start}-${branch.terminal_end}`,
      route_id: branch.route_id,
      direction_id: branch.direction_id,
      terminal_start: branch.terminal_start,
      terminal_end: branch.terminal_end,
      total_trips_in_branch: branch.total_trips,
      canonical_pattern_trips: canonical.count,
      canonical_pattern_share: Number((canonical.count / branch.total_trips).toFixed(3)),
      distinct_patterns: branch.patterns.size,
      stop_sequence: canonical.sequence,
      sample_trip_ids: canonical.sample_trip_ids,
      sample_shape_ids: [...canonical.sample_shape_ids],
      sample_headsigns: [...branch.sample_headsigns].slice(0, 4),
    });
  }

  // Sort branches per route by total_trips desc (most common service first)
  for (const arr of branchesByRoute.values()) {
    arr.sort((a: { total_trips_in_branch: number }, b: { total_trips_in_branch: number }) => b.total_trips_in_branch - a.total_trips_in_branch);
  }

  return { branchesByRoute, droppedLowFreqBranches };
}
