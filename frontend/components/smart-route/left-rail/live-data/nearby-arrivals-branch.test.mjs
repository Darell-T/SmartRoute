import assert from "node:assert/strict";
import test from "node:test";

import { buildAlerts, buildFeed, buildHealth, buildLineState, buildStation } from "./alerts-feed.ts";
import {
  cleanDestinationLabel,
  formatClockAt,
  formatDistance,
  formatWalk,
  labelForArrivalMinutes,
  labelForMinutes,
  minutesAgo,
  minutesUntilArrival,
  secondsSince,
  splitBusHeadsign,
} from "./formatters.ts";
import { buildArrivalRows, buildNearbyBusArrivals, buildNearbySubwayGroups } from "./nearby-arrivals.ts";

const nowMs = 1_700_000_000_000;
const at = (mins) => nowMs / 1000 + mins * 60;
const blankDest = { terminal_stop_name: "", trip_headsign: "", headsign: "", destination: "", destination_name: "" };

function live(overrides = {}) {
  return { route_id: "Q", stop_id: "Q01N", station_name: "Canal St", distance_m: 90, arrival_time: at(4), ...overrides };
}
function row(overrides = {}) {
  return buildArrivalRows({ arrivals: [live(overrides)] }, nowMs).serviceRows[0];
}
function services(arrivals) {
  return buildArrivalRows({ arrivals }, nowMs).serviceRows;
}
function subway(overrides = {}) {
  return {
    id: "q", mode: "subway", routeIds: ["Q"], destination: "96 St", stopName: "Canal St", walkMinutes: 3,
    arrivalMinutes: [4], direction: "uptown", predictionType: "live", predictionFreshness: "fresh",
    alertSeverity: "none", line: "Q", way: "uptown", dest: "96 St", label: "4 min", mins: 4,
    status: "On Time", stale: false, stationName: "Canal St", ...overrides,
  };
}
function bus(overrides = {}) {
  return subway({ mode: "bus", routeIds: ["B41"], destination: "Downtown Brooklyn", dest: "Downtown Brooklyn", line: "B41", ...overrides });
}
function stop(name, extra = {}) {
  return subway({ stationName: name, stopName: name, ...extra });
}
function names(groups) {
  return groups.map((group) => group.name);
}
function rail(sev, line) {
  return { sev, kind: "train", lines: line ? [line] : [], title: "Notice", sub: "Active", startedAgo: "1m", lastUpdate: "1m" };
}
function qn(secondQ) {
  return services([
    live({ arrival_time: at(3), terminal_stop_name: "96 St" }),
    live({ arrival_time: at(secondQ), terminal_stop_name: "96 St" }),
    live({ route_id: "N", stop_id: "N01N", arrival_time: at(3), terminal_stop_name: "Astoria-Ditmars Blvd" }),
    live({ route_id: "N", stop_id: "N01N", arrival_time: at(8), terminal_stop_name: "Astoria-Ditmars Blvd" }),
  ]);
}

test("unknown subway dest is Terminal and bus dest is Route terminal", () => {
  assert.equal(row({ ...blankDest, direction: "", stop_id: "Q01" }).destination, "Terminal");
  assert.equal(row({ ...blankDest, mode: "bus", route_id: "B41", stop_compass: "E", direction: "" }).destination, "Route terminal");
});

test("direction-only Uptown and Downtown use Q fallbacks", () => {
  assert.equal(row({ direction: "UPTOWN", terminal_stop_name: "Uptown" }).destination, "96 St");
  assert.equal(row({ direction: "DOWNTOWN", stop_id: "Q01S", terminal_stop_name: "Downtown" }).destination, "Coney Island-Stillwell Av");
  assert.equal(row({ route_id: "T", direction: "DOWNTOWN", stop_id: "T01S", terminal_stop_name: "Downtown" }).destination, "Terminal");
});

test("outbound south and stop suffix S are downtown", () => {
  assert.equal(row({ direction: "outbound", stop_id: "Q01" }).direction, "downtown");
  assert.equal(row({ direction: "south", stop_id: "Q01" }).direction, "downtown");
  assert.equal(row({ direction: "", stop_id: "Q01S" }).direction, "downtown");
});

test("inbound and suffix N are uptown; empty direction stays unknown", () => {
  assert.equal(row({ direction: "inbound", stop_id: "Q01" }).direction, "uptown");
  assert.equal(row({ direction: "", stop_id: "Q01N" }).direction, "uptown");
  assert.equal(row({ direction: "", stop_id: "Q01" }).direction, "unknown");
});

test("bus compass E falls back to direction and compass S is downtown", () => {
  assert.equal(row({ mode: "bus", route_id: "B41", stop_compass: "E", direction: "outbound" }).direction, "downtown");
  assert.equal(row({ mode: "bus", route_id: "B41", stop_compass: "S", direction: "" }).direction, "downtown");
  assert.equal(row({ mode: "bus", route_id: "B41", stop_compass: "E", direction: "" }).direction, "unknown");
});

test("delay 250 is on time, 400 is minor, 650 is major", () => {
  assert.equal(row({ delay: 250 }).status, "On Time");
  assert.equal(row({ delay: 250 }).alertSeverity, "none");
  assert.equal(row({ delay: 400 }).alertSeverity, "minor");
  assert.equal(row({ delay: 400 }).status, "Delayed");
  assert.equal(row({ delay: 650 }).alertSeverity, "major");
});

test("scheduled flags, far stops, and missing route ids drop or mark scheduled", () => {
  assert.equal(row({ prediction_type: "scheduled" }).predictionType, "scheduled");
  assert.equal(row({ realtime: false }).predictionType, "scheduled");
  assert.equal(services([live({ distance_m: 900 })]).length, 0);
  assert.equal(services([live({ route_id: null })]).length, 0);
});

test("a Limited-only bus headsign keeps Limited as the destination", () => {
  assert.equal(row({ mode: "bus", route_id: "B41", stop_compass: "E", headsign: "Limited ", terminal_stop_name: "" }).destination, "Limited");
});

test("a 20 minute gap ranks behind a tighter same-walk subway", () => {
  const result = qn(23);
  assert.deepEqual(result.map((item) => item.line), ["N", "Q"]);
  assert.deepEqual(result.find((item) => item.line === "Q").arrivalMinutes, [3, 23]);
});

test("a 15 minute gap ranks behind a tighter same-walk subway", () => {
  const result = qn(18);
  assert.deepEqual(result.map((item) => item.line), ["N", "Q"]);
  assert.deepEqual(result.find((item) => item.line === "Q").arrivalMinutes, [3, 18]);
});

test("closer station wins a shared service; equal distance keeps the sooner stop", () => {
  assert.equal(services([
    live({ station_name: "Far Av", distance_m: 400, arrival_time: at(2), terminal_stop_name: "96 St" }),
    live({ station_name: "Near Av", stop_id: "Q02N", distance_m: 80, arrival_time: at(9), terminal_stop_name: "96 St" }),
  ])[0].stopName, "Near Av");
  assert.equal(services([
    live({ station_name: "Later Av", distance_m: 200, arrival_time: at(9), terminal_stop_name: "96 St" }),
    live({ station_name: "Soon Av", stop_id: "Q02N", distance_m: 200, arrival_time: at(3), terminal_stop_name: "96 St" }),
  ])[0].stopName, "Soon Av");
});

test("buildArrivalRows without a clock still returns empty rows", () => {
  assert.deepEqual(buildArrivalRows(null).serviceRows, []);
  assert.deepEqual(buildArrivalRows(null).stationRows, []);
});

test("crafted planned major and minor alerts sort groups by severity", () => {
  assert.deepEqual(names(buildNearbySubwayGroups([
    stop("Major St", { id: "maj", alertSeverity: "major" }),
    stop("Minor St", { id: "min", alertSeverity: "minor" }),
    stop("Plan St", { id: "pl", alertSeverity: "planned" }),
  ])), ["Plan St", "Minor St", "Major St"]);
});

test("groups rank by walk, eta, coverage, planned alert, then name", () => {
  assert.deepEqual(names(buildNearbySubwayGroups([
    stop("Later", { arrivalMinutes: [12], mins: 12 }),
    stop("Soon", { arrivalMinutes: [4], mins: 4 }),
  ])), ["Soon", "Later"]);
  const covered = buildNearbySubwayGroups([
    stop("Thin"),
    stop("Wide", { id: "n", routeIds: ["N"], line: "N" }),
    stop("Wide", { id: "r", routeIds: ["R"], line: "R" }),
  ]);
  assert.equal(covered[0].name, "Wide");
  assert.deepEqual(covered[0].routeIds, ["N", "R"]);
  assert.deepEqual(names(buildNearbySubwayGroups([
    stop("Alerted", { alertSeverity: "planned" }),
    stop("Clear", { alertSeverity: "none" }),
  ])), ["Clear", "Alerted"]);
  assert.deepEqual(names(buildNearbySubwayGroups([stop("Zoo"), stop("Ark")])), ["Ark", "Zoo"]);
});

test("empty arrival minutes sort last in a group and across groups", () => {
  const [group] = buildNearbySubwayGroups([
    subway({ id: "empty", destination: "Zebra", dest: "Zebra", arrivalMinutes: [], mins: 99 }),
    subway({ id: "soon", destination: "Apple", dest: "Apple", arrivalMinutes: [5], mins: 5 }),
  ]);
  assert.deepEqual(group.arrivals.map((item) => item.destination), ["Apple", "Zebra"]);
  assert.deepEqual(names(buildNearbySubwayGroups([
    stop("Empty", { arrivalMinutes: [], mins: 99 }),
    stop("Live", { arrivalMinutes: [6], mins: 6 }),
  ])), ["Live", "Empty"]);
});

test("same-eta grouped arrivals prefer a real route id then destination", () => {
  const [routes] = buildNearbySubwayGroups([
    subway({ id: "blank", routeIds: [], line: "", destination: "96 St", dest: "96 St" }),
    subway({ id: "q", destination: "96 St", dest: "96 St" }),
  ]);
  assert.equal(routes.arrivals[0].routeIds[0], "Q");
  const [labels] = buildNearbySubwayGroups([
    subway({ id: "z", destination: "Zebra", dest: "Zebra" }),
    subway({ id: "a", destination: "Apple", dest: "Apple" }),
  ]);
  assert.deepEqual(labels.arrivals.map((item) => item.destination), ["Apple", "Zebra"]);
});

test("the nearest walk ignores arrivals that omit walk minutes", () => {
  const [group] = buildNearbySubwayGroups([subway({ id: "omit", walkMinutes: undefined }), subway({ id: "near", walkMinutes: 2 })]);
  assert.equal(group.walkMinutes, 2);
});

test("tied usefulness prefers shorter walk, sooner bus, measured walk, and no planned alert", () => {
  assert.equal(buildNearbyBusArrivals([
    bus({ id: "long", walkMinutes: 8, arrivalMinutes: [4], mins: 4 }),
    bus({ id: "short", line: "B42", routeIds: ["B42"], walkMinutes: 5, arrivalMinutes: [10], mins: 10 }),
  ])[0].line, "B42");
  assert.equal(buildNearbyBusArrivals([
    bus({ id: "late", line: "B43", routeIds: ["B43"], walkMinutes: 4, arrivalMinutes: [28], mins: 28 }),
    bus({ id: "soon", line: "B44", routeIds: ["B44"], walkMinutes: 4, arrivalMinutes: [10], mins: 10, alertSeverity: "major" }),
  ])[0].line, "B44");
  assert.equal(buildNearbyBusArrivals([
    bus({ id: "unknown", walkMinutes: undefined }),
    bus({ id: "measured", line: "B42", routeIds: ["B42"], walkMinutes: 10 }),
  ])[0].line, "B42");
  assert.equal(buildNearbyBusArrivals([
    bus({ id: "p", alertSeverity: "planned" }),
    bus({ id: "n", line: "B42", routeIds: ["B42"], alertSeverity: "none" }),
  ])[0].line, "B42");
});

test("cleanDestinationLabel title-cases transit tokens without shouting", () => {
  assert.equal(cleanDestinationLabel(""), "");
  assert.equal(cleanDestinationLabel("A//B"), "A//B");
  assert.equal(cleanDestinationLabel("McDONALD"), "McDonald");
  assert.equal(cleanDestinationLabel("VIA"), "via");
  assert.equal(cleanDestinationLabel("E 18 ST"), "E 18 St");
  assert.equal(cleanDestinationLabel("14TH"), "14th");
  assert.equal(cleanDestinationLabel("JFK"), "JFK");
  assert.equal(cleanDestinationLabel("FLATBUSH AV"), "Flatbush Av");
});

test("splitBusHeadsign pulls Limited and via off the destination", () => {
  assert.deepEqual(splitBusHeadsign("Limited Sunset Park via Church"), {
    destination: "Sunset Park",
    qualifiers: ["Limited", "via Church"],
  });
  assert.deepEqual(splitBusHeadsign("Limited "), { destination: "Limited", qualifiers: [] });
});

test("arrival labels, minutesAgo, and secondsSince cover empty and clock edges", () => {
  assert.equal(minutesUntilArrival(at(0), nowMs), 0);
  assert.equal(labelForMinutes(0), "Now");
  assert.equal(labelForMinutes(1), "1 min");
  assert.equal(labelForArrivalMinutes([]), "Now");
  assert.equal(labelForArrivalMinutes([5]), "5 min");
  assert.equal(labelForArrivalMinutes([0, 4, 12]), "Now, 4, 12 min");
  assert.equal(minutesAgo(null, nowMs), "live");
  assert.equal(minutesAgo(nowMs / 1000 - 20, nowMs), "now");
  assert.equal(minutesAgo(nowMs / 1000 - 60, nowMs), "1m");
  assert.equal(minutesAgo(nowMs / 1000 - 30 * 60, nowMs), "30m");
  assert.equal(minutesAgo(nowMs / 1000 - 2 * 3600, nowMs), "2h");
  assert.equal(secondsSince(null, nowMs), 0);
  assert.equal(secondsSince(0, nowMs), 0);
});

test("formatDistance and formatWalk use nearby, meters, and miles", () => {
  assert.equal(formatDistance(null), "nearby");
  assert.equal(formatWalk(null), "nearby");
  assert.equal(formatDistance(90), "90 m");
  assert.equal(formatWalk(90), "1 min walk");
  assert.equal(formatDistance(1609.344), "1.0 mi");
  assert.equal(formatWalk(168), "2 min walk");
  assert.equal(formatClockAt(nowMs), "5:13 PM");
  assert.equal(formatClockAt(Number.NaN), "");
});

test("null live feed station and health stay nearby and clear", () => {
  const station = buildStation(null, nowMs);
  assert.equal(station.walk, "nearby");
  assert.equal(station.dist, "nearby");
  assert.equal(station.updatedSec, 0);
  assert.equal(buildHealth(null).status, "clear");
  assert.equal(buildHealth(null).summary, "Nearby subway routes are being monitored inside a half-mile radius.");
  assert.deepEqual(buildHealth({ stops: [{ stop_id: "X", stop_name: "X", distance_m: 10 }] }).affected, []);
});

test("network status maps disrupted, caution, unknown, and degraded without signals", () => {
  assert.equal(buildHealth({ signals: { network_status: "disrupted" } }).status, "disrupted");
  assert.equal(buildHealth({ signals: { network_status: "caution" } }).status, "minor");
  assert.equal(buildHealth({ signals: { network_status: "fog" } }).status, "clear");
  assert.equal(buildHealth({ degraded: true }).status, "minor");
});

test("alerts classify severity, drop duplicates, and fill missing copy", () => {
  const alerts = buildAlerts([
    { header: "Service suspended on the A", description: "No trains", route_ids: ["A"], alert_id: "dup", start: nowMs / 1000 - 7200, end: nowMs / 1000 - 1800, stop_names: ["Canal St"] },
    { header: "Service suspended on the A", description: "No trains", route_ids: ["A"], alert_id: "dup" },
    { header: "Weekend work", description: "Planned work on the Q", routeIds: ["Q"] },
    { header: "Weekend work", description: "Planned work on the Q" },
    { header: "", description: "Slow speeds", route_ids: ["N"] },
    { header: "Trains bypass DeKalb", description: "Skip stop", route_ids: ["D"] },
  ], undefined, nowMs);
  assert.equal(alerts.length, 4);
  assert.equal(alerts[0].sev, "major");
  assert.equal(alerts[0].lastUpdate, "30m");
  assert.equal(alerts[0].startedAgo, "2h");
  assert.deepEqual(alerts[0].affectedStops, ["Canal St"]);
  assert.equal(alerts[1].sev, "planned");
  assert.equal(alerts[2].title, "MTA service alert");
  assert.equal(alerts[3].sev, "major");
  const [startOnly] = buildAlerts([{ header: "Delays", description: "Signals", start: nowMs / 1000 - 7200, route_ids: ["A"] }], [], nowMs);
  assert.equal(startOnly.lastUpdate, "2h");
});

test("line state keeps major, skips watch, and records planned versus minor", () => {
  assert.deepEqual(
    buildLineState([rail("major", "A"), rail("minor", "A"), rail("watch", "B"), rail("planned", "C"), rail("minor", "D")]),
    { A: "major", C: "planned", D: "minor" },
  );
});

test("feed maps critical and high incidents to major and fills missing fields", () => {
  const feed = buildFeed(
    [rail("minor", "")],
    [
      { id: "1", type: "fire", lat: 0, lng: 0, title: "Fire", severity: "critical" },
      { id: "2", type: "police", lat: 0, lng: 0, title: "Police", severity: "high", routeIds: ["N"], detail: "Police activity", updated_at: nowMs / 1000 - 60 },
      { id: "3", type: "medical", lat: 0, lng: 0, title: "Medical", severity: "low" },
    ],
    nowMs,
  );
  assert.equal(feed[0].line, null);
  assert.equal(feed[1].sev, "major");
  assert.equal(feed[1].detail, "Nearby incident");
  assert.equal(feed[2].sev, "major");
  assert.equal(feed[2].line, "N");
  assert.equal(feed[3].sev, "minor");
});
