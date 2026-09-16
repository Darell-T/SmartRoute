import type { GtfsTrip, TripsById, TripStations } from "./gtfs-topology.ts";

type BranchPattern = {
  sequence: string[];
  count: number;
  sample_trip_ids: string[];
  sampleGtfsPolylineIds: Set<string>;
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
  "sample_shape_ids": string[];
  sample_headsigns: string[];
};

export type BranchesByRoute = Map<string, Branch[]>;

export type BranchSelection = {
  branchesByRoute: BranchesByRoute;
  droppedLowFreqBranches: number;
};

function patternForSequence(branch: BranchAccumulator, sequence: string[]): BranchPattern {
  const sig = sequence.join(",");
  const existing = branch.patterns.get(sig);
  if (existing) return existing;
  const created: BranchPattern = {
    sequence,
    count: 0,
    sample_trip_ids: [],
    sampleGtfsPolylineIds: new Set(),
  };
  branch.patterns.set(sig, created);
  return created;
}

function accumulateTrip(
  branchAccum: Map<string, BranchAccumulator>,
  trip: GtfsTrip,
  sequence: string[],
): void {
  const terminalStart = sequence[0];
  const terminalEnd = sequence[sequence.length - 1];
  const key = `${trip.route_id}|${trip.direction_id}|${terminalStart}→${terminalEnd}`;
  let branch = branchAccum.get(key);
  if (!branch) {
    branch = ({
    route_id: (trip.route_id),
    direction_id: (trip.direction_id),
    terminal_start: (terminalStart),
    terminal_end: (terminalEnd),
    patterns: new Map(),
    total_trips: 0,
    sample_headsigns: new Set(),
});
    branchAccum.set(key, branch);
  }
  const pattern = patternForSequence(branch, sequence);
  pattern.count += 1;
  if (pattern.sample_trip_ids.length < 3) pattern.sample_trip_ids.push(trip.trip_id);
  const polylineId = trip["shape_id"];
  if (polylineId) pattern.sampleGtfsPolylineIds.add(polylineId);
  branch.total_trips += 1;
  if (trip.headsign) branch.sample_headsigns.add(trip.headsign);
}

function canonicalPattern(branch: BranchAccumulator): BranchPattern | null {
  let best: BranchPattern | null = null;
  for (const pattern of branch.patterns.values()) {
    if (!best || pattern.count > best.count) best = pattern;
  }
  return best;
}

function emitBranch(branch: BranchAccumulator, canonical: BranchPattern): Branch {
  return {
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
    "sample_shape_ids": [...canonical.sampleGtfsPolylineIds],
    sample_headsigns: [...branch.sample_headsigns].slice(0, 4),
  };
}

export function buildBranchesByRoute(
  tripsById: TripsById,
  tripStations: TripStations,
  minTripsPerBranch: number,
): BranchSelection {
  const branchAccum = new Map<string, BranchAccumulator>();

  for (const trip of tripsById.values()) {
    const sequence = tripStations.get(trip.trip_id);
    if (!sequence) continue;
    accumulateTrip(branchAccum, trip, sequence);
  }

  const branchesByRoute: BranchesByRoute = new Map();
  let droppedLowFreqBranches = 0;
  for (const branch of branchAccum.values()) {
    if (branch.total_trips < minTripsPerBranch) {
      droppedLowFreqBranches += 1;
      continue;
    }
    const canonical = canonicalPattern(branch);
    if (!canonical) continue;
    const routeBranches = branchesByRoute.get(branch.route_id);
    if (routeBranches) routeBranches.push(emitBranch(branch, canonical));
    else branchesByRoute.set(branch.route_id, [emitBranch(branch, canonical)]);
  }

  for (const arr of branchesByRoute.values()) {
    arr.sort((left, right) => right.total_trips_in_branch - left.total_trips_in_branch);
  }

  return { branchesByRoute, droppedLowFreqBranches };
}
