import assert from "node:assert/strict";
import test from "node:test";

import { buildItineraryViewModel, condensePreviewEvents, formatClockTime, formatDurationMinutes, formatStructuredRecommendationReason, isSupportedSubwayRoute, parseRationale, PREVIEW_EVENT_MAX, shouldCollapseEvents, transferLabel } from "./itinerary-view-model.ts";

const card = {
  card_id: "rc_1", turn_id: "t1", role: "recommended",
  origin: { label: "Your location", lat: 40.7, lng: -73.9 },
  destination: { label: "Costco", lat: 40.6, lng: -74 },
  summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "Server reason" },
  route: [], alerts: [],
  itinerary: {
    itinerary_id: "it_1", total_duration_seconds: 2040, transfer_count: 0,
    arrival_at: "2026-07-16T15:45:00-04:00", total_dwell_seconds: 0,
    legs: [{ mode: "WALK", walk_seconds: 240, board: { label: "Your location" }, alight: { label: "A station" } }, { mode: "SUBWAY", ride_seconds: 1560, service_id: "A", board: { label: "A station" }, alight: { label: "Costco" }, stop_count: 8 }],
  },
};

test("formats canonical itinerary facts across the chat card", () => {
  const model = buildItineraryViewModel(card);
  assert.equal(model.invalid, false);
  assert.equal(model.totalMinutes, 34);
  assert.equal(model.transferCount, 0);
  assert.equal(model.arrivalLabel, "3:45 PM");
  assert.deepEqual(model.events.map((event) => event.kind), ["walk", "subway"]);
});

test("missing canonical itinerary is explicit unavailable state", () => {
  const model = buildItineraryViewModel({ ...card, itinerary: undefined });
  assert.equal(model.invalid, true);
  assert.equal(model.invalidReason, "This itinerary is unavailable.");
});

test("canonical transfer count wins over a disagreeing summary", () => {
  const model = buildItineraryViewModel({ ...card, summary: { ...card.summary, transfers: 9 }, itinerary: { ...card.itinerary, transfer_count: 1 } });
  assert.equal(model.transferCount, 1);
  assert.deepEqual(model.metaParts, ["1 transfer"]);
});

test("canonical structured reasons remain rider-facing", () => {
  const model = buildItineraryViewModel({ ...card, itinerary: { ...card.itinerary, structured_recommendation_reasons: [{ code: "fewer_transfers", transfer_difference: 1 }] } });
  assert.deepEqual(model.rationale, ["Uses 1 fewer transfer"]);
});

test("canonical stop count and route identity stay attached to transit legs", () => {
  const model = buildItineraryViewModel(card);
  assert.deepEqual(model.events[1].routeIds, ["A"]);
  assert.equal(model.events[1].stopCount, 8);
});

test("canonical walk duration is displayed without route identifiers", () => {
  const model = buildItineraryViewModel(card);
  assert.equal(model.events[0].durationLabel, "4 min");
  assert.deepEqual(model.events[0].routeIds, []);
});

test("formatters retain customer-facing units", () => {
  assert.equal(formatDurationMinutes(89), "1 hr 29 min");
  assert.equal(transferLabel(1), "1 transfer");
});

test("clock formatter rejects malformed times", () => assert.equal(formatClockTime("bad"), null));
test("rationale parser keeps independent phrases", () => assert.deepEqual(parseRationale("Fastest · Fewer transfers"), ["Fastest", "Fewer transfers"]));
test("supported subway route guard recognizes official routes", () => { assert.equal(isSupportedSubwayRoute("A"), true); assert.equal(isSupportedSubwayRoute("ZZ"), false); });
test("collapse threshold is preserved", () => { assert.equal(shouldCollapseEvents(PREVIEW_EVENT_MAX), false); assert.equal(shouldCollapseEvents(PREVIEW_EVENT_MAX + 1), true); });
test("adjacent canonical walks condense without crossing transit", () => {
  const events = condensePreviewEvents([{ id: "w1", kind: "walk", routeIds: [], title: "x", durationSeconds: 60, fromLabel: "Home" }, { id: "w2", kind: "walk", routeIds: [], title: "x", durationSeconds: 120, toLabel: "Station" }, { id: "a", kind: "subway", routeIds: ["A"], title: "A", fromLabel: "Station" }], "Destination");
  assert.deepEqual(events.map((event) => event.kind), ["walk", "subway"]);
  assert.equal(events[0].durationSeconds, 180);
});
test("geometry-only canonical walks are not passenger rows", () => assert.deepEqual(condensePreviewEvents([{ id: "w", kind: "walk", routeIds: [], title: "x", durationSeconds: 0 }], "Destination"), []));
test("micro-walk without identity stays hidden", () => assert.deepEqual(condensePreviewEvents([{ id: "w", kind: "walk", routeIds: [], title: "x", durationSeconds: 60 }], "Destination"), []));
test("destination identity comes from canonical destination", () => assert.equal(buildItineraryViewModel({ ...card, destination: { ...card.destination, label: "A long destination" } }).placeNames.at(-1), "A long destination"));
test("catchable arrival uses grounded live evidence", () => assert.equal(buildItineraryViewModel({ ...card, summary: { ...card.summary, first_leg_arrival: { route_id: "A", catchable_arrival_minutes: 4, source_status: "live" } } }).firstLegArrivalLabel, "Next realistic A: 4 min"));
test("stale catchable arrival is withheld", () => assert.equal(buildItineraryViewModel({ ...card, summary: { ...card.summary, first_leg_arrival: { route_id: "A", catchable_arrival_minutes: 4, source_status: "stale" } } }).firstLegArrivalLabel, null));
test("canonical multi-stop dwell remains ordered", () => {
  const model = buildItineraryViewModel({ ...card, itinerary: { ...card.itinerary, waypoints: [{ label: "Stop" }], segments: [{ segment_index: 0, destination: { label: "Stop" }, legs: [{ mode: "WALK", walk_seconds: 120 }] }, { segment_index: 1, destination: { label: "Costco" }, legs: [{ mode: "SUBWAY", ride_seconds: 300, service_id: "A" }] }], dwell_events: [{ event_type: "dwell", after_segment_index: 0, waypoint: { label: "Stop" }, duration_seconds: 600, source: "user" }] } });
  assert.deepEqual(model.events.map((event) => event.kind), ["walk", "waypoint", "subway"]);
});

test("canonical bus leg renders as bus with its server route id", () => {
  const model = buildItineraryViewModel({
    ...card,
    itinerary: { ...card.itinerary, legs: [{ mode: "BUS", ride_seconds: 600, service_id: "B63", stop_count: 4 }] },
  });
  assert.equal(model.events[0].kind, "bus");
  assert.deepEqual(model.events[0].routeIds, ["B63"]);
});

test("canonical AirTrain tram leg remains a visible rail transfer", () => {
  const model = buildItineraryViewModel({
    ...card,
    itinerary: {
      ...card.itinerary,
      transfer_count: 1,
      legs: [
        { mode: "SUBWAY", ride_seconds: 600, service_id: "F" },
        { mode: "TRAM", ride_seconds: 480, service_id: "Jamaica AirTrain" },
      ],
    },
  });
  assert.deepEqual(model.events.map((event) => event.kind), ["subway", "rail"]);
  assert.deepEqual(model.events[1].routeIds, ["JAMAICA AIRTRAIN"]);
  assert.equal(model.metaParts[0], "1 transfer");
});

test("canonical boarding wait is an explicit itinerary row before transit", () => {
  const model = buildItineraryViewModel({
    ...card,
    itinerary: {
      ...card.itinerary,
      total_wait_seconds: 1260,
      legs: [
        card.itinerary.legs[0],
        { ...card.itinerary.legs[1], wait_seconds: 1260 },
      ],
    },
  });
  assert.deepEqual(model.events.map((event) => event.kind), ["walk", "wait", "subway"]);
  assert.equal(model.events[1].title, "Wait for A");
  assert.equal(model.events[1].durationLabel, "21 min");
});

test("consecutive canonical transit legs retain transfer presentation", () => {
  const model = buildItineraryViewModel({
    ...card,
    itinerary: { ...card.itinerary, transfer_count: 1, legs: [{ mode: "SUBWAY", ride_seconds: 300, service_id: "A" }, { mode: "SUBWAY", ride_seconds: 600, service_id: "C" }] },
  });
  assert.deepEqual(model.events.map((event) => event.kind), ["subway", "subway"]);
  assert.equal(model.metaParts[0], "1 transfer");
});

test("empty canonical and summary rationale stays empty", () => {
  const model = buildItineraryViewModel({ ...card, summary: { ...card.summary, reason: "" } });
  assert.deepEqual(model.rationale, []);
});

test("fully malformed canonical card is unavailable without fabricated rows", () => {
  const model = buildItineraryViewModel({ ...card, destination: { label: "", lat: 0, lng: 0 }, itinerary: { total_duration_seconds: "bad" } });
  assert.equal(model.invalid, true);
  assert.equal(model.durationLabel, "—");
  assert.deepEqual(model.events, []);
});

test("canonical total and arrival beat disagreeing summary and route clocks", () => {
  const model = buildItineraryViewModel({
    ...card,
    summary: { ...card.summary, eta_minutes: 5 },
    route: [{ arrival_time_iso: "2026-07-16T10:00:00-04:00" }],
    itinerary: { ...card.itinerary, total_duration_seconds: 2040, arrival_at: "2026-07-16T15:45:00-04:00" },
  });
  assert.equal(model.totalMinutes, 34);
  assert.equal(model.arrivalLabel, "3:45 PM");
});

test("canonical leg seconds and provider stop order are retained", () => {
  const model = buildItineraryViewModel({
    ...card,
    itinerary: { ...card.itinerary, legs: [{ mode: "SUBWAY", ride_seconds: 605, service_id: "A", stop_count: 3, stops: [{ name: "First" }, { name: "Second" }, { name: "Third" }] }] },
  });
  assert.equal(model.events[0].durationSeconds, 605);
  assert.deepEqual(model.events[0].stops, ["First", "Second", "Third"]);
});

test("multi-stop canonical itinerary preserves places, segments, dwell source, and durations", () => {
  const model = buildItineraryViewModel({
    ...card,
    itinerary: { ...card.itinerary, waypoints: [{ label: "Waypoint" }], segments: [{ segment_index: 0, destination: { label: "Waypoint" }, legs: [{ mode: "SUBWAY", ride_seconds: 300, service_id: "A" }] }, { segment_index: 1, destination: { label: "Costco" }, legs: [{ mode: "BUS", ride_seconds: 480, service_id: "B63" }] }], dwell_events: [{ event_type: "dwell", after_segment_index: 0, waypoint: { label: "Waypoint" }, duration_seconds: 600, source: "user" }] },
  });
  assert.deepEqual(model.placeNames, ["Your location", "Waypoint", "Costco"]);
  assert.deepEqual(model.events.map((event) => event.routeIds[0] || event.kind), ["A", "waypoint", "B63"]);
  assert.equal(model.events[1].sourceLabel, "Requested stop");
  assert.equal(model.events[0].durationSeconds, 300);
  assert.equal(model.events[2].durationSeconds, 480);
});

test("unknown structured recommendation reason is ignored", () => {
  const model = buildItineraryViewModel({ ...card, itinerary: { ...card.itinerary, structured_recommendation_reasons: [{ code: "unknown" }] } });
  assert.deepEqual(model.rationale, []);
});

test("event crowd exposure reason stays concise and rider-facing", () => {
  assert.equal(
    formatStructuredRecommendationReason({
      code: "lower_event_crowd_exposure",
      event_count: 2,
      provider_status: "available",
    }),
    "Lower exposure to nearby event crowds",
  );
});
