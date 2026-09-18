import assert from "node:assert/strict";
import test from "node:test";

import {
  buildEventsFromCanonicalItinerary,
  canonicalPlaceLabel,
  condensePreviewEvents,
  durationMinutesFromSeconds,
  formatDurationMinutes,
  intermediateStopNames,
} from "./itinerary-event-adapter.ts";
import {
  buildItineraryViewModel,
  buildMergedItineraryViewModel,
  formatClockTime,
  formatStructuredRecommendationReason,
  parseRationale,
  transferLabel,
  warnUnsupportedRouteId,
} from "./itinerary-view-model.ts";

const card = {
  card_id: "rc_1",
  turn_id: "t1",
  role: "recommended",
  origin: { label: "Your location", lat: 40.7, lng: -73.9 },
  destination: { label: "Costco", lat: 40.6, lng: -74 },
  summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "Server reason" },
  route: [],
  alerts: [],
  itinerary: {
    itinerary_id: "it_1",
    total_duration_seconds: 2040,
    transfer_count: 0,
    arrival_at: "2026-07-16T15:45:00-04:00",
    total_dwell_seconds: 0,
    legs: [
      {
        mode: "WALK",
        walk_seconds: 240,
        board: { label: "Your location" },
        alight: { label: "A station" },
      },
      {
        mode: "SUBWAY",
        ride_seconds: 1560,
        service_id: "A",
        board: { label: "A station" },
        alight: { label: "Costco" },
        stop_count: 8,
      },
    ],
  },
};

function eventsFor(itinerary) {
  return buildEventsFromCanonicalItinerary(itinerary, "Your location", "Costco", "rc_1");
}

function walkRow(extra = {}) {
  return { id: extra.id ?? "w", kind: "walk", routeIds: [], title: extra.title ?? "Walk", ...extra };
}

test("duration labels use em dash for invalid minutes and keep hour units", () => {
  assert.equal(formatDurationMinutes(Number.NaN), "—");
  assert.equal(formatDurationMinutes(-8), "—");
  assert.equal(formatDurationMinutes(12), "12 min");
  assert.equal(formatDurationMinutes(120), "2 hr");
  assert.equal(formatDurationMinutes(135), "2 hr 15 min");
  assert.equal(durationMinutesFromSeconds(undefined), null);
});

test("transfer without a destination route stays a generic transfer row", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "SUBWAY", ride_seconds: 180, service_id: "A", board: { label: "Jay" }, alight: { label: "MetroTech" } },
      {
        mode: "WALK",
        transfer_kind: "same_station",
        transfer_semantics: {
          kind: "same_station",
          total_seconds: 90,
          accessibility: "accessible",
        },
      },
      { mode: "SUBWAY", ride_seconds: 240, service_id: "Q", board: { label: "MetroTech" }, alight: { label: "Canal" } },
    ],
  });
  const transfer = events.find((event) => event.kind === "transfer");
  assert.equal(transfer?.title, "Transfer");
  assert.equal(transfer?.subtitle, "Same station · about 2 min");
  assert.deepEqual(transfer?.routeIds, []);
});

test("transfer to a named route keeps the passenger route in the title", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "SUBWAY", ride_seconds: 180, service_id: "N" },
      {
        mode: "WALK",
        transfer_kind: "station_complex",
        transfer_semantics: {
          kind: "station_complex",
          to_route_id: "Q",
          total_seconds: 180,
          accessibility: "unknown",
        },
      },
      { mode: "SUBWAY", ride_seconds: 240, service_id: "Q" },
    ],
  });
  const transfer = events.find((event) => event.kind === "transfer");
  assert.equal(transfer?.title, "Transfer to the Q");
  assert.equal(transfer?.subtitle, "Station complex · about 3 min · Accessibility unknown");
  assert.deepEqual(transfer?.routeIds, ["Q"]);
});

test("same-platform transfer with unknown duration still names the station and access", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "SUBWAY", ride_seconds: 120, service_id: "A" },
      {
        mode: "WALK",
        transfer_kind: "same_platform",
        transfer_semantics: {
          kind: "same_platform",
          to_route_id: "C",
          total_seconds: Number.NaN,
          accessibility: "inaccessible",
        },
      },
      { mode: "SUBWAY", ride_seconds: 120, service_id: "C" },
    ],
  });
  const transfer = events.find((event) => event.kind === "transfer");
  assert.equal(transfer?.title, "Transfer to the C");
  assert.equal(transfer?.subtitle, "Same station · Accessibility unavailable");
  assert.equal(transfer?.durationSeconds, undefined);
});

test("station-complex transfer with missing seconds omits the about-duration clause", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "SUBWAY", ride_seconds: 120, service_id: "A" },
      {
        mode: "WALK",
        transfer_kind: "station_complex",
        transfer_semantics: {
          kind: "station_complex",
          to_route_id: "R",
          accessibility: "accessible",
        },
      },
      { mode: "SUBWAY", ride_seconds: 120, service_id: "R" },
    ],
  });
  const transfer = events.find((event) => event.kind === "transfer");
  assert.equal(transfer?.subtitle, "Station complex");
  assert.equal(transfer?.durationLabel, undefined);
});

test("walk without walk seconds is omitted from the passenger preview", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "SUBWAY", ride_seconds: 200, service_id: "A", board: { label: "Jay" }, alight: { label: "Hoyt" } },
      { mode: "WALK", board: { label: "Hoyt" }, alight: { label: "Nevins" } },
      { mode: "SUBWAY", ride_seconds: 200, service_id: "2", board: { label: "Nevins" }, alight: { label: "Atlantic" } },
    ],
  });
  assert.deepEqual(events.map((event) => event.kind), ["subway", "subway"]);
});

test("ride without ride seconds still shows the transit row without a duration", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [{ mode: "SUBWAY", service_id: "A", board: { label: "Jay" }, alight: { label: "Canal" } }],
  });
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "subway");
  assert.equal(events[0].durationSeconds, undefined);
  assert.equal(events[0].durationLabel, undefined);
});

test("intermediate stop names drop matching endpoints and survive missing stop lists", () => {
  assert.deepEqual(
    intermediateStopNames({ stops: ["Jay", "DeKalb", "Canal"], fromLabel: "Jay", toLabel: "Canal" }),
    ["DeKalb"],
  );
  assert.deepEqual(intermediateStopNames({ fromLabel: "Jay", toLabel: "Canal" }), []);
  assert.deepEqual(intermediateStopNames({ stops: ["Jay", "DeKalb"], fromLabel: "Jay" }), ["DeKalb"]);
});

test("missing or non-finite wait seconds do not insert a wait row", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "SUBWAY", ride_seconds: 120, service_id: "A", board: { label: "S1" }, alight: { label: "S2" } },
      {
        mode: "SUBWAY",
        ride_seconds: 120,
        service_id: "B",
        wait_seconds: Number.NaN,
        board: { label: "S2" },
        alight: { label: "S3" },
      },
    ],
  });
  assert.deepEqual(events.map((event) => event.kind), ["subway", "subway"]);
});

test("wait without a service id is labeled wait to board", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      {
        mode: "SUBWAY",
        ride_seconds: 180,
        service_id: "   ",
        wait_seconds: 150,
        board: { label: "Jay" },
        alight: { label: "Canal" },
      },
    ],
  });
  assert.equal(events[0].kind, "wait");
  assert.equal(events[0].title, "Wait to board");
  assert.deepEqual(events[0].routeIds, []);
  assert.equal(events[1].kind, "subway");
});

test("unknown transit modes are omitted while train and rail stay visible", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "TRAIN", ride_seconds: 240, service_id: "LIRR", board: { label: "Atlantic" }, alight: { label: "Jamaica" } },
      { mode: "FERRY", ride_seconds: 400, service_id: "SI", board: { label: "Whitehall" }, alight: { label: "St George" } },
      { mode: "RAIL", ride_seconds: 180, service_id: "NJT", board: { label: "Penn" }, alight: { label: "Newark" } },
    ],
  });
  assert.deepEqual(events.map((event) => event.kind), ["rail", "rail"]);
  assert.deepEqual(events.map((event) => event.routeIds[0]), ["LIRR", "NJT"]);
});

test("preview drops a walk whose from and to labels match", () => {
  const events = condensePreviewEvents(
    [walkRow({ durationSeconds: 180, fromLabel: "Home", toLabel: " home " })],
    "Costco",
  );
  assert.deepEqual(events, []);
});

test("preview drops a zero-duration walk even when labels differ", () => {
  const events = condensePreviewEvents(
    [walkRow({ durationSeconds: 0, fromLabel: "Home", toLabel: "Station" })],
    "Costco",
  );
  assert.deepEqual(events, []);
});

test("preview hides a micro walk that has no own identity", () => {
  const events = condensePreviewEvents(
    [
      { id: "s", kind: "subway", routeIds: ["A"], title: "A" },
      walkRow({ id: "w", durationSeconds: 89 }),
      { id: "t", kind: "subway", routeIds: ["C"], title: "C" },
    ],
    "Costco",
  );
  assert.deepEqual(events.map((event) => event.kind), ["subway", "subway"]);
});

test("preview keeps a walk that only carries duration minutes", () => {
  const events = condensePreviewEvents(
    [walkRow({ durationMinutes: 4, fromLabel: "Home", toLabel: "Station" })],
    "Station",
    "Home",
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "walk");
  assert.equal(events[0].durationSeconds, 240);
  assert.equal(events[0].fromLabel, "Home");
  assert.equal(events[0].toLabel, "Station");
});

test("preview drops a long interior walk that still has no from or to", () => {
  const events = condensePreviewEvents(
    [
      { id: "s", kind: "subway", routeIds: ["A"], title: "A" },
      walkRow({ id: "w", durationSeconds: 180 }),
      { id: "t", kind: "subway", routeIds: ["C"], title: "C" },
    ],
    "Costco",
  );
  assert.deepEqual(events.map((event) => event.kind), ["subway", "subway"]);
});

test("consecutive walks merge into one passenger walk", () => {
  const events = condensePreviewEvents(
    [
      walkRow({ id: "w1", durationSeconds: 70, fromLabel: "Home" }),
      walkRow({ id: "w2", durationSeconds: 80, toLabel: "Station" }),
    ],
    "Station",
    "Home",
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "walk");
  assert.equal(events[0].durationSeconds, 150);
  assert.equal(events[0].fromLabel, "Home");
  assert.equal(events[0].toLabel, "Station");
});

test("preview walk borrows waypoint titles as boundary labels", () => {
  const events = condensePreviewEvents(
    [
      { id: "wp1", kind: "waypoint", routeIds: [], title: "Stop A" },
      walkRow({ id: "w", durationSeconds: 120 }),
      { id: "wp2", kind: "waypoint", routeIds: [], title: "Stop B" },
    ],
    "Costco",
  );
  assert.equal(events.length, 3);
  assert.equal(events[1].kind, "walk");
  assert.equal(events[1].fromLabel, "Stop A");
  assert.equal(events[1].toLabel, "Stop B");
});

test("a segment with non-array legs contributes no rows", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    segments: [
      { segment_index: 0, destination: { label: "Waypoint" }, legs: null },
      {
        segment_index: 1,
        destination: { label: "Costco" },
        legs: [{ mode: "SUBWAY", ride_seconds: 300, service_id: "A", board: { label: "Jay" }, alight: { label: "Costco" } }],
      },
    ],
  });
  assert.deepEqual(events.map((event) => event.kind), ["subway"]);
  assert.deepEqual(events[0].routeIds, ["A"]);
});

test("non-array dwell events do not invent waypoint rows", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    segments: [
      {
        segment_index: 0,
        destination: { label: "Costco" },
        legs: [{ mode: "SUBWAY", ride_seconds: 200, service_id: "A" }],
      },
    ],
    dwell_events: null,
  });
  assert.deepEqual(events.map((event) => event.kind), ["subway"]);
});

test("dwell rows keep requested versus planned copy and skip non-dwell events", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    segments: [
      {
        segment_index: 0,
        destination: { label: "Stop A" },
        legs: [{ mode: "SUBWAY", ride_seconds: 120, service_id: "A" }],
      },
      {
        segment_index: 1,
        destination: { label: "Costco" },
        legs: [{ mode: "BUS", ride_seconds: 180, service_id: "B63" }],
      },
    ],
    dwell_events: [
      {
        event_type: "board",
        after_segment_index: 0,
        waypoint: { label: "Ignored" },
        duration_seconds: 60,
        source: "user",
      },
      {
        event_type: "dwell",
        after_segment_index: 0,
        waypoint: { label: "Stop A" },
        duration_seconds: 300,
        source: "user",
      },
      {
        event_type: "dwell",
        after_segment_index: 1,
        waypoint: { label: "Costco" },
        duration_seconds: Number.NaN,
        source: "default",
      },
    ],
  });
  assert.deepEqual(events.map((event) => event.kind), ["subway", "waypoint", "bus", "waypoint"]);
  assert.equal(events[1].sourceLabel, "Requested stop");
  assert.equal(events[1].subtitle, "5 min stop");
  assert.equal(events[3].sourceLabel, "Planned stop");
  assert.equal(events[3].subtitle, "Planned stop");
});

test("itinerary without segments uses the top-level legs path", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [{ mode: "BUS", ride_seconds: 420, service_id: "B54", board: { label: "Court" }, alight: { label: "Costco" } }],
  });
  assert.deepEqual(events.map((event) => event.kind), ["bus"]);
  assert.deepEqual(events[0].routeIds, ["B54"]);
});

test("stop count is omitted unless the server sent a finite value", () => {
  const events = eventsFor({
    itinerary_id: "it_1",
    legs: [
      { mode: "SUBWAY", ride_seconds: 100, service_id: "A", stop_count: null, board: { label: "S1" }, alight: { label: "S2" } },
      { mode: "SUBWAY", ride_seconds: 100, service_id: "B", stop_count: Number.NaN, board: { label: "S2" }, alight: { label: "S3" } },
      { mode: "SUBWAY", ride_seconds: 100, service_id: "C", board: { label: "S3" }, alight: { label: "S4" } },
      { mode: "SUBWAY", ride_seconds: 100, service_id: "D", stop_count: 2.6, board: { label: "S4" }, alight: { label: "S5" } },
    ],
  });
  assert.equal(events[0].stopCount, undefined);
  assert.equal(events[1].stopCount, undefined);
  assert.equal(events[2].stopCount, undefined);
  assert.equal(events[3].stopCount, 3);
});

test("canonical place labels fall back when the place has no name", () => {
  assert.equal(canonicalPlaceLabel(null, "Waypoint"), "Waypoint");
  assert.equal(canonicalPlaceLabel({ label: "Costco" }, "Waypoint"), "Costco");
});

test("transfer labels use the plural form except for a single transfer", () => {
  assert.equal(transferLabel(0), "0 transfers");
  assert.equal(transferLabel(2), "2 transfers");
});

test("scheduled first-leg arrival remains a catchable passenger label", () => {
  const model = buildItineraryViewModel({
    ...card,
    summary: {
      ...card.summary,
      first_leg_arrival: { route_id: "A", catchable_arrival_minutes: 6, source_status: "scheduled" },
    },
  });
  assert.equal(model.firstLegArrivalLabel, "Next realistic A: 6 min");
});

test("structured fewer-transfers copy follows the difference count", () => {
  assert.equal(
    formatStructuredRecommendationReason({ code: "fewer_transfers", transfer_difference: 0 }),
    null,
  );
  assert.equal(
    formatStructuredRecommendationReason({ code: "fewer_transfers", transfer_difference: 2 }),
    "Uses 2 fewer transfers",
  );
  assert.equal(formatStructuredRecommendationReason({ code: "not_a_reason" }), null);
  assert.equal(formatStructuredRecommendationReason("   "), null);
});

test("unavailable recommended card without an id stays recommended and invalid", () => {
  const model = buildItineraryViewModel({
    ...card,
    card_id: undefined,
    itinerary: undefined,
  });
  assert.equal(model.invalid, true);
  assert.equal(model.recommended, true);
  assert.equal(model.id, "invalid");
  assert.deepEqual(model.sourceCardIds, []);
  assert.equal(model.invalidReason, "This itinerary is unavailable.");
});

test("valid card with a missing origin still starts at your location", () => {
  const model = buildItineraryViewModel({ ...card, origin: undefined });
  assert.equal(model.invalid, false);
  assert.equal(model.placeNames[0], "Your location");
});

test("merged view still returns the first card when every card is invalid", () => {
  const broken = { ...card, destination: { ...card.destination, label: "" } };
  const model = buildMergedItineraryViewModel([broken, { ...broken, card_id: "rc_2" }]);
  assert.equal(model?.invalid, true);
  assert.equal(model?.id, "rc_1");
  assert.equal(model?.invalidReason, "This itinerary is unavailable.");
});

test("merged view skips an invalid card and keeps the first valid itinerary", () => {
  const broken = { ...card, itinerary: undefined };
  const model = buildMergedItineraryViewModel([broken, card]);
  assert.equal(model?.invalid, false);
  assert.equal(model?.primaryCardId, "rc_1");
  assert.equal(model?.placeNames.at(-1), "Costco");
});

test("supported subway route ids do not emit an unsupported-route warning", () => {
  const warnings = [];
  const original = console.warn;
  console.warn = (...args) => {
    warnings.push(args.join(" "));
  };
  try {
    warnUnsupportedRouteId("Q");
  } finally {
    console.warn = original;
  }
  assert.deepEqual(warnings, []);
});

test("rationale parser treats empty and null copy as no phrases", () => {
  assert.deepEqual(parseRationale(""), []);
  assert.deepEqual(parseRationale(null), []);
  assert.deepEqual(parseRationale("   "), []);
});

test("consecutive duplicate origin and waypoint names collapse on the card", () => {
  const model = buildItineraryViewModel({
    ...card,
    origin: { ...card.origin, label: "Costco" },
    itinerary: {
      ...card.itinerary,
      waypoints: [{ label: "Costco" }, { label: "Costco" }],
    },
  });
  assert.deepEqual(model.placeNames, ["Costco"]);
});

test("positive total dwell seconds add a stop clause to card meta", () => {
  const model = buildItineraryViewModel({
    ...card,
    itinerary: { ...card.itinerary, transfer_count: 0, total_dwell_seconds: 600 },
  });
  assert.deepEqual(model.metaParts, ["0 transfers", "10 min stop"]);
});

test("clock formatter returns null for a missing timestamp", () => {
  assert.equal(formatClockTime(null), null);
  assert.equal(formatClockTime(undefined), null);
});
