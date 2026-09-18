import assert from "node:assert/strict";
import test from "node:test";
import { buildBranchesByRoute } from "./branch-selection.ts";
import type { GtfsTrip } from "./gtfs-topology.ts";

function trip(partial: Partial<GtfsTrip> & Pick<GtfsTrip, "trip_id" | "route_id">): GtfsTrip {
  return {
    raw_route_id: partial.route_id,
    direction_id: "0",
    "shape_id": "poly-1",
    service_id: "WKD",
    headsign: "Coney Island",
    ...partial,
  };
}

test("buildBranchesByRoute groups trips by terminals and keeps the majority pattern", () => {
  const tripsById = new Map<string, GtfsTrip>([
    ["t1", trip({ trip_id: "t1", route_id: "Q" })],
    ["t2", trip({ trip_id: "t2", route_id: "Q" })],
    ["t3", trip({ trip_id: "t3", route_id: "Q", "shape_id": "poly-2" })],
  ]);
  const tripStations = new Map([
    ["t1", ["Q01", "Q02", "Q03"]],
    ["t2", ["Q01", "Q02", "Q03"]],
    ["t3", ["Q01", "Q02", "Q04"]],
  ]);
  const { branchesByRoute, droppedLowFreqBranches } = buildBranchesByRoute(tripsById, tripStations, 1);
  const branches = branchesByRoute.get("Q") ?? [];
  assert.equal(droppedLowFreqBranches, 0);
  assert.equal(branches.length, 2);
  assert.deepEqual(branches[0].stop_sequence, ["Q01", "Q02", "Q03"]);
  assert.equal(branches[0].canonical_pattern_trips, 2);
  assert.deepEqual(branches[0]["sample_shape_ids"], ["poly-1"]);
  assert.deepEqual(branches[1].stop_sequence, ["Q01", "Q02", "Q04"]);
  assert.deepEqual(branches[1]["sample_shape_ids"], ["poly-2"]);
  assert.deepEqual(
    buildBranchesByRoute(tripsById, tripStations, 1).branchesByRoute.get("Q")?.[0].stop_sequence,
    branches[0].stop_sequence,
  );
});

test("buildBranchesByRoute drops branches below minTripsPerBranch", () => {
  const tripsById = new Map<string, GtfsTrip>([["t1", trip({ trip_id: "t1", route_id: "G" })]]);
  const tripStations = new Map([["t1", ["G01", "G02"]]]);
  const { branchesByRoute, droppedLowFreqBranches } = buildBranchesByRoute(tripsById, tripStations, 5);
  assert.equal(droppedLowFreqBranches, 1);
  assert.equal(branchesByRoute.size, 0);
});

test("buildBranchesByRoute skips trips with no station sequence", () => {
  const tripsById = new Map<string, GtfsTrip>([["t1", trip({ trip_id: "t1", route_id: "G" })]]);
  const { branchesByRoute, droppedLowFreqBranches } = buildBranchesByRoute(tripsById, new Map(), 1);
  assert.equal(droppedLowFreqBranches, 0);
  assert.equal(branchesByRoute.size, 0);
});

test("buildBranchesByRoute caps sample trips, skips empty shape ids, and sorts by trip count", () => {
  const tripsById = new Map<string, GtfsTrip>([
    ["t1", trip({ trip_id: "t1", route_id: "G", "shape_id": null, headsign: "" })],
    ["t2", trip({ trip_id: "t2", route_id: "G" })],
    ["t3", trip({ trip_id: "t3", route_id: "G" })],
    ["t4", trip({ trip_id: "t4", route_id: "G" })],
    ["t5", trip({ trip_id: "t5", route_id: "F", direction_id: "1" })],
  ]);
  const tripStations = new Map([
    ["t1", ["G01", "G02"]],
    ["t2", ["G01", "G02"]],
    ["t3", ["G01", "G02"]],
    ["t4", ["G01", "G02"]],
    ["t5", ["F01", "F02"]],
  ]);
  const { branchesByRoute } = buildBranchesByRoute(tripsById, tripStations, 1);
  const g = branchesByRoute.get("G") ?? [];
  assert.equal(g[0].sample_trip_ids.length, 3);
  assert.deepEqual(g[0]["sample_shape_ids"], ["poly-1"]);
  assert.deepEqual(g[0].sample_headsigns, ["Coney Island"]);
  assert.equal(g[0].canonical_pattern_share, 1);
  const f = branchesByRoute.get("F") ?? [];
  assert.equal(f[0].direction_id, "1");
  assert.equal(f[0].total_trips_in_branch, 1);
});
