import assert from "node:assert/strict";
import { test } from "node:test";
import { geometryStats } from "../shared/geometry-utils.ts";
import { buildStopsById, buildRoutesByRawId, buildTripsById, buildTripStations } from "./gtfs-topology.ts";
import { buildGtfsTopologyStage } from "./gtfs-topology-stage.ts";
import { buildTopologyEdges } from "./topology-edges.ts";
import { normalizeRouteId } from "../shared/route-config.ts";

test("topology maps stops, routes, trips, and station sequences", () => {
  const stops = buildStopsById([
    { stop_id: "A40", stop_name: "High", stop_lat: "40.75", stop_lon: "-73.99", parent_station: "", location_type: "1" },
    { stop_id: "A40N", stop_name: "High N", stop_lat: "40.7501", stop_lon: "-73.99", parent_station: "A40", location_type: "0" },
    { stop_id: "A41", stop_name: "Low", stop_lat: "40.74", stop_lon: "-73.99", parent_station: "", location_type: "1" },
  ]);
  assert.equal(stops.get("A40")?.name, "High");
  const routes = buildRoutesByRawId([{ route_id: "A", route_short_name: "A", route_long_name: "8 Av", route_color: "0039A6" }], normalizeRouteId);
  assert.equal(routes.get("A")?.route_id, "A");
  const trips = buildTripsById(
    [{ trip_id: "t1", route_id: "A", direction_id: "0", "shape_id": "poly-a", service_id: "1", trip_headsign: "Downtown" }],
    routes,
  );
  assert.equal(trips.get("t1")?.["shape_id"], "poly-a");
  const stations = buildTripStations(
    [
      { trip_id: "t1", stop_id: "A40N", stop_sequence: "1" },
      { trip_id: "t1", stop_id: "A41", stop_sequence: "2" },
    ],
    stops,
  );
  assert.deepEqual(stations.get("t1"), ["A40", "A41"]);
});

test("topology edges emit one LineString per adjacent stop pair", () => {
  const stops = buildStopsById([
    { stop_id: "A40", stop_name: "High", stop_lat: "40.75", stop_lon: "-73.99", parent_station: "", location_type: "1" },
    { stop_id: "A41", stop_name: "Low", stop_lat: "40.74", stop_lon: "-73.99", parent_station: "", location_type: "1" },
  ]);
  const { edgeFeatures, topologyEdgeDiagnostics } = buildTopologyEdges(
    [{
      route_id: "A",
      branches: [{
        branch_id: "A-0-A40-A41",
        direction_id: "0",
        stop_sequence: ["A40", "A41"],
      }],
    }],
    stops,
    geometryStats,
  );
  assert.equal(edgeFeatures.length, 1);
  assert.equal(edgeFeatures[0].properties["shape_selection_strategy"], "gtfs_topology_only");
  assert.equal(topologyEdgeDiagnostics.topology_edges_emitted, 1);
  const empty = buildTopologyEdges([{ route_id: "A", branches: [{ branch_id: "x", direction_id: "0", stop_sequence: ["missing", "A41"] }] }], stops, geometryStats);
  assert.equal(empty.topologyEdgeDiagnostics.topology_edges_dropped_missing_stop, 1);
});

test("gtfs topology stage builds branches and edges from csv tables", () => {
  const gtfs = new Map([
    ["stops.txt", "stop_id,stop_name,stop_lat,stop_lon,parent_station,location_type\nA40,High,40.75,-73.99,,1\nA41,Low,40.74,-73.99,,1\n"],
    ["routes.txt", "route_id,route_short_name,route_long_name,route_color\nA,A,8 Av,0039A6\n"],
    ["trips.txt", "trip_id,route_id,direction_id,shape_id,service_id,trip_headsign\nt1,A,0,poly-a,1,Downtown\n"],
    ["stop_times.txt", "trip_id,stop_id,stop_sequence\nt1,A40,1\nt1,A41,2\n"],
  ]);
  const result = buildGtfsTopologyStage({
    gtfs,
    minTripsPerBranch: 1,
    normalizeRouteId,
    geometryStats,
  });
  assert.equal(result.expectedOpenDataRouteIds[0], "A");
  assert.equal(result.expectedEdges, 1);
  assert.equal(result.edgeFeatures.length, 1);
  assert.equal(result.topologyDoc.per_route[0].branches[0]["sample_shape_ids"][0], "poly-a");
  const again = buildGtfsTopologyStage({ gtfs, minTripsPerBranch: 1, normalizeRouteId, geometryStats });
  assert.deepEqual(again.edgeFeatures[0].geometry.coordinates, result.edgeFeatures[0].geometry.coordinates);
});

test("gtfs topology stage logs parse steps when a logger is provided", () => {
  const gtfs = new Map([
    ["stops.txt", "stop_id,stop_name,stop_lat,stop_lon,parent_station,location_type\nA40,High,40.75,-73.99,,1\nA41,Low,40.74,-73.99,,1\n"],
    ["routes.txt", "route_id,route_short_name,route_long_name,route_color\nA,A,8 Av,0039A6\n"],
    ["trips.txt", "trip_id,route_id,direction_id,shape_id,service_id,trip_headsign\nt1,A,0,poly-a,1,Downtown\n"],
    ["stop_times.txt", "trip_id,stop_id,stop_sequence\nt1,A40,1\nt1,A41,2\n"],
  ]);
  const logs: string[] = [];
  const result = buildGtfsTopologyStage({
    gtfs,
    minTripsPerBranch: 1,
    normalizeRouteId,
    geometryStats,
    log: (message) => logs.push(message),
  });
  assert.ok(logs.some((line) => line.includes("parsing stops.txt")));
  assert.ok(logs.some((line) => line.includes("gtfs sizes: stops=2")));
  assert.equal(result.expectedEdges, 1);
});

test("topology skips empty ids, unresolved parents, unknown trips, and duplicate stations", () => {
  const stops = buildStopsById([
    { stop_id: "", stop_name: "Blank" },
    { stop_id: "A40", stop_name: "High", stop_lat: "40.75", stop_lon: "-73.99", parent_station: "MISSING", location_type: "0" },
    { stop_id: "A40N", stop_name: "High N", stop_lat: "40.7501", stop_lon: "-73.99", parent_station: "A40", location_type: "0" },
    { stop_id: "A41", stop_name: "Low", stop_lat: "40.74", stop_lon: "-73.99", parent_station: "", location_type: "1" },
  ]);
  assert.equal(stops.has(""), false);
  assert.equal(stops.get("A40")?.parent_station, "MISSING");
  const routes = buildRoutesByRawId(
    [
      { route_id: "", route_short_name: "x" },
      { route_id: "A", route_short_name: "", route_long_name: "8 Av", route_color: "" },
    ],
    normalizeRouteId,
  );
  assert.equal(routes.has(""), false);
  assert.equal(routes.get("A")?.short_name, "A");
  assert.equal(routes.get("A")?.color, null);
  const trips = buildTripsById(
    [
      { trip_id: "", route_id: "A" },
      { trip_id: "orphan", route_id: "Z" },
      { trip_id: "t1", route_id: "A", direction_id: "1", "shape_id": "", service_id: "WKD", trip_headsign: "Uptown" },
    ],
    routes,
  );
  assert.equal(trips.has(""), false);
  assert.equal(trips.has("orphan"), false);
  assert.equal(trips.get("t1")?.["shape_id"], null);
  assert.equal(trips.get("t1")?.direction_id, "1");
  const stations = buildTripStations(
    [
      { trip_id: "", stop_id: "A40", stop_sequence: "1" },
      { trip_id: "t1", stop_id: "", stop_sequence: "1" },
      { trip_id: "t1", stop_id: "A40N", stop_sequence: "not-a-number" },
      { trip_id: "t1", stop_id: "A40N", stop_sequence: "1" },
      { trip_id: "t1", stop_id: "A40", stop_sequence: "2" },
      { trip_id: "t1", stop_id: "A41", stop_sequence: "3" },
      { trip_id: "solo", stop_id: "A40", stop_sequence: "1" },
    ],
    stops,
  );
  assert.deepEqual(stations.get("t1"), ["A40", "A41"]);
  assert.equal(stations.has("solo"), false);
});

test("topology maps a platform with no parent onto its own stop id", () => {
  const stops = buildStopsById([
    { stop_id: "G22", stop_name: "Court Sq", stop_lat: "40.75", stop_lon: "-73.94", parent_station: "", location_type: "0" },
    { stop_id: "G26", stop_name: "Greenpoint", stop_lat: "40.73", stop_lon: "-73.95", parent_station: "", location_type: "0" },
  ]);
  const stations = buildTripStations(
    [
      { trip_id: "g1", stop_id: "G22", stop_sequence: "2" },
      { trip_id: "g1", stop_id: "G26", stop_sequence: "1" },
      { trip_id: "missing-stop", stop_id: "ZZZ", stop_sequence: "1" },
      { trip_id: "missing-stop", stop_id: "G26", stop_sequence: "2" },
    ],
    stops,
  );
  assert.deepEqual(stations.get("g1"), ["G26", "G22"]);
  assert.deepEqual(stations.get("missing-stop"), ["ZZZ", "G26"]);
});
