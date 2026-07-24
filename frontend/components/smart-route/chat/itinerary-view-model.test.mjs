import assert from "node:assert/strict";
import test from "node:test";

import {
  buildItineraryViewModel,
  buildMergedItineraryViewModel,
  condensePreviewEvents,
  formatDurationMinutes,
  isSupportedSubwayRoute,
  parseRationale,
  PREVIEW_EVENT_MAX,
  shouldCollapseEvents,
  transferLabel,
} from "./itinerary-view-model.ts";

const ORIGIN = { label: "Your location", lat: 40.7484, lng: -73.9857 };
const DESTINATION = { label: "Costco Sunset Park", lat: 40.6559, lng: -74.0089 };

function baseCard(overrides = {}) {
  return {
    card_id: "rc_1",
    turn_id: "t1",
    role: "recommended",
    origin: ORIGIN,
    destination: DESTINATION,
    summary: {
      eta_minutes: 34,
      transfers: 0,
      lines: ["A"],
      reason: "No bus · Elevator access for the cart",
    },
    route: [
      {
        type: "WALK",
        departure_stop: "Your location",
        arrival_stop: "34 St-Penn Station",
        minutes_until_arrival: 4,
      },
      {
        type: "SUBWAY",
        train_line: "A",
        departure_stop: "34 St-Penn Station",
        arrival_stop: "Jay St-MetroTech",
        minutes_until_arrival: 26,
        departure_time_iso: "2026-07-18T14:05:00-04:00",
        arrival_time_iso: "2026-07-18T14:31:00-04:00",
      },
      {
        type: "WALK",
        departure_stop: "Jay St-MetroTech",
        arrival_stop: "Costco Sunset Park",
        minutes_until_arrival: 4,
        arrival_time_iso: "2026-07-18T14:39:00-04:00",
      },
    ],
    alerts: [],
    ...overrides,
  };
}

test("formatDurationMinutes formats under and over an hour", () => {
  assert.equal(formatDurationMinutes(34), "34 min");
  assert.equal(formatDurationMinutes(89), "1 hr 29 min");
  assert.equal(formatDurationMinutes(60), "1 hr");
});

test("transferLabel is plural-aware", () => {
  assert.equal(transferLabel(0), "0 transfers");
  assert.equal(transferLabel(1), "1 transfer");
  assert.equal(transferLabel(2), "2 transfers");
});

test("parseRationale splits middle-dot phrases and omits empty", () => {
  assert.deepEqual(parseRationale("Fastest complete trip · Avoids current F maintenance"), [
    "Fastest complete trip",
    "Avoids current F maintenance",
  ]);
  assert.deepEqual(parseRationale(""), []);
  assert.deepEqual(parseRationale(null), []);
});

test("builds a compact preview: drops short lead walk, keeps transit + final walk", () => {
  const model = buildItineraryViewModel(baseCard());

  assert.equal(model.invalid, false);
  assert.equal(model.recommended, true);
  assert.deepEqual(model.placeNames, ["Your location", "Costco Sunset Park"]);
  assert.equal(model.durationLabel, "34 min");
  assert.equal(model.totalMinutes, 34);
  assert.equal(model.transferCount, 0);
  // Zero transfers are omitted from meta (not "0 transfers").
  assert.equal(model.metaParts.length, 0);
  assert.ok(model.arrivalLabel);
  assert.deepEqual(model.rationale, ["No bus", "Elevator access for the cart"]);
  assert.equal(model.primaryActionLabel, "Open on map");
  assert.equal(model.secondaryActionLabel, "View steps");

  // Curated: A transit + final walk only (no 4-min lead walk dump).
  assert.equal(model.events.length, 2);
  assert.equal(model.events[0].kind, "subway");
  assert.deepEqual(model.events[0].routeIds, ["A"]);
  assert.equal(model.events[0].title, "Jay St-MetroTech");
  assert.equal(model.events[1].kind, "walk");
  assert.equal(model.events[1].title, "Walk to Costco Sunset Park");
  assert.deepEqual(model.events[1].routeIds, []);
});

test("walk steps never use subway route ids even when summary lines exist", () => {
  const model = buildItineraryViewModel(
    baseCard({
      summary: { eta_minutes: 12, transfers: 0, lines: ["M"], reason: "" },
      route: [
        {
          type: "WALK",
          arrival_stop: "Prada",
          minutes_until_arrival: 6,
        },
      ],
    }),
  );

  assert.equal(model.events.length, 1);
  assert.equal(model.events[0].kind, "walk");
  assert.deepEqual(model.events[0].routeIds, []);
  assert.match(model.events[0].title, /Walk to/);
});

test("groups consecutive subway transfers into one multi-bullet event", () => {
  const model = buildItineraryViewModel(
    baseCard({
      summary: {
        eta_minutes: 29,
        transfers: 1,
        lines: ["N", "R"],
        reason: "Faster, but one transfer",
      },
      route: [
        {
          type: "SUBWAY",
          train_line: "N",
          departure_stop: "34 St-Herald Sq",
          arrival_stop: "Atlantic Av-Barclays Ctr",
          minutes_until_arrival: 18,
        },
        {
          type: "SUBWAY",
          train_line: "R",
          departure_stop: "Atlantic Av-Barclays Ctr",
          arrival_stop: "36 St",
          minutes_until_arrival: 8,
        },
        {
          type: "WALK",
          departure_stop: "36 St",
          arrival_stop: "Costco Sunset Park",
          minutes_until_arrival: 3,
        },
      ],
    }),
  );

  assert.equal(model.events.length, 2);
  assert.equal(model.events[0].kind, "subway");
  assert.deepEqual(model.events[0].routeIds, ["N", "R"]);
  assert.equal(model.events[0].durationLabel, "26 min");
  assert.equal(model.events[1].kind, "walk");
  assert.ok(model.metaParts.includes("1 transfer"));
});

test("bus segments are kind bus and keep their route id", () => {
  const model = buildItineraryViewModel(
    baseCard({
      summary: {
        eta_minutes: 22,
        transfers: 0,
        lines: ["B41"],
        reason: "Direct bus",
      },
      route: [
        {
          type: "BUS",
          train_line: "B41",
          departure_stop: "Livingston St",
          arrival_stop: "Flatbush Av",
          minutes_until_arrival: 18,
        },
      ],
    }),
  );

  assert.equal(model.events[0].kind, "bus");
  assert.deepEqual(model.events[0].routeIds, ["B41"]);
});

test("missing rationale omits rationale phrases", () => {
  const model = buildItineraryViewModel(
    baseCard({
      summary: { eta_minutes: 20, transfers: 0, lines: ["A"], reason: "" },
    }),
  );
  assert.deepEqual(model.rationale, []);
});

test("long place names are preserved in the journey title", () => {
  const long = "The Metropolitan Museum of Art Fifth Avenue Entrance";
  const model = buildItineraryViewModel(
    baseCard({
      destination: { label: long, lat: 40.7794, lng: -73.9632 },
    }),
  );
  assert.equal(model.placeNames[1], long);
});

test("invalid card fails safely without fabricated times", () => {
  const model = buildItineraryViewModel({
    card_id: "bad",
    turn_id: "t1",
    role: "recommended",
    origin: ORIGIN,
    destination: { label: "", lat: 0, lng: 0 },
    summary: { eta_minutes: Number.NaN, transfers: 0, lines: [], reason: "" },
    route: [],
    alerts: [],
  });

  assert.equal(model.invalid, true);
  assert.equal(model.durationLabel, "—");
  assert.equal(model.events.length, 0);
  assert.equal(model.arrivalLabel, null);
  assert.ok(model.invalidReason);
});

test("merged multi-stop nests pickup under transit and keeps final walk", () => {
  const leg1 = baseCard({
    card_id: "rc_leg1",
    origin: { label: "Home", lat: 40.72, lng: -73.98 },
    destination: { label: "Sunday Morning", lat: 40.72, lng: -74.0 },
    summary: {
      eta_minutes: 46,
      transfers: 1,
      lines: ["Q", "4"],
      reason: "Fastest complete trip",
    },
    route: [
      {
        type: "SUBWAY",
        train_line: "Q",
        departure_stop: "Home",
        arrival_stop: "Union Square",
        minutes_until_arrival: 20,
        departure_time_iso: "2026-07-18T04:20:00-04:00",
        arrival_time_iso: "2026-07-18T04:40:00-04:00",
      },
      {
        type: "SUBWAY",
        train_line: "4",
        departure_stop: "Union Square",
        arrival_stop: "Brooklyn Bridge",
        minutes_until_arrival: 26,
        arrival_time_iso: "2026-07-18T05:06:00-04:00",
      },
    ],
  });

  const leg2 = baseCard({
    card_id: "rc_leg2",
    origin: { label: "Sunday Morning", lat: 40.72, lng: -74.0 },
    destination: { label: "Prada", lat: 40.72, lng: -74.0 },
    depart_iso: "2026-07-18T05:31:00-04:00",
    summary: {
      eta_minutes: 18,
      transfers: 0,
      lines: ["M"],
      reason: "Avoids current F maintenance",
    },
    route: [
      {
        type: "SUBWAY",
        train_line: "M",
        departure_stop: "Sunday Morning",
        arrival_stop: "Broadway-Lafayette",
        minutes_until_arrival: 12,
        departure_time_iso: "2026-07-18T05:31:00-04:00",
        arrival_time_iso: "2026-07-18T05:43:00-04:00",
      },
      {
        type: "WALK",
        departure_stop: "Broadway-Lafayette",
        arrival_stop: "Prada",
        minutes_until_arrival: 6,
        arrival_time_iso: "2026-07-18T05:49:00-04:00",
      },
    ],
  });

  const model = buildMergedItineraryViewModel([leg1, leg2]);
  assert.ok(model);
  assert.equal(model.recommended, true);
  assert.deepEqual(model.placeNames, ["Home", "Sunday Morning", "Prada"]);
  assert.equal(model.sourceCardIds.length, 2);
  assert.equal(model.primaryCardId, "rc_leg2");
  assert.ok(model.metaParts.some((p) => p.includes("pickup")));
  assert.ok(model.metaParts.includes("1 transfer"));

  // Nested pickup: first transit row shows waypoint + pickup subtitle.
  const first = model.events[0];
  assert.equal(first.kind, "subway");
  assert.deepEqual(first.routeIds, ["Q", "4"]);
  assert.equal(first.title, "Sunday Morning");
  assert.match(first.subtitle ?? "", /pickup/i);

  // No separate pickup row; walks never carry subway bullets.
  assert.equal(
    model.events.some((e) => e.kind === "pickup"),
    false,
  );
  assert.ok(model.events.length <= PREVIEW_EVENT_MAX);

  for (const event of model.events) {
    if (event.kind === "walk") {
      assert.deepEqual(event.routeIds, []);
    }
  }

  const finalWalk = model.events.find((e) => e.kind === "walk");
  assert.ok(finalWalk);
  assert.equal(finalWalk.title, "Walk to Prada");
});

test("condensePreviewEvents drops short intermediate walks and caps rows", () => {
  const condensed = condensePreviewEvents(
    [
      { id: "1", kind: "walk", routeIds: [], title: "Walk", durationMinutes: 3, durationLabel: "3 min" },
      { id: "2", kind: "subway", routeIds: ["Q"], title: "Union Sq", durationMinutes: 20, durationLabel: "20 min" },
      { id: "3", kind: "walk", routeIds: [], title: "Walk", durationMinutes: 2, durationLabel: "2 min" },
      { id: "4", kind: "subway", routeIds: ["M"], title: "Lafayette", durationMinutes: 12, durationLabel: "12 min" },
      { id: "5", kind: "walk", routeIds: [], title: "Walk", durationMinutes: 6, durationLabel: "6 min" },
    ],
    "Prada",
  );

  assert.ok(condensed.length <= PREVIEW_EVENT_MAX);
  assert.equal(condensed.some((e) => e.durationMinutes === 3), false);
  assert.equal(condensed.some((e) => e.durationMinutes === 2), false);
  assert.equal(condensed.at(-1)?.kind, "walk");
  assert.equal(condensed.at(-1)?.title, "Walk to Prada");
});

test("official subway routes are recognized; unsupported ids are not", () => {
  assert.equal(isSupportedSubwayRoute("Q"), true);
  assert.equal(isSupportedSubwayRoute("6X"), true);
  assert.equal(isSupportedSubwayRoute("ZZ"), false);
});

test("long event lists flag collapse for the inline card", () => {
  assert.equal(shouldCollapseEvents(3), false);
  assert.equal(shouldCollapseEvents(6), true);
});

test("does not hardcode mockup destination names when card data differs", () => {
  const model = buildItineraryViewModel(baseCard());
  const serialized = JSON.stringify(model);
  assert.equal(serialized.includes("Prada"), false);
  assert.equal(serialized.includes("Sunday Morning"), false);
  assert.equal(serialized.includes("1 hr 29 min"), false);
});

test("prefers itinerary.total_duration_seconds for hero total over summary.eta_minutes", () => {
  // 5340s = 89 min; summary intentionally wrong so we prove preference.
  const model = buildItineraryViewModel(
    baseCard({
      summary: {
        eta_minutes: 34,
        transfers: 0,
        lines: ["A"],
        reason: "Legacy summary",
      },
      itinerary: {
        itinerary_id: "rc_1",
        total_duration_seconds: 5340,
        transfer_count: 0,
        arrival_at: "2026-07-18T15:34:00-04:00",
        departure_at: "2026-07-18T14:05:00-04:00",
        legs: [],
      },
    }),
  );

  assert.equal(model.invalid, false);
  assert.equal(model.totalMinutes, 89);
  assert.equal(model.durationLabel, "1 hr 29 min");
  // Must not invent hero total from summary's 34.
  assert.notEqual(model.totalMinutes, 34);
});

test("prefers itinerary.transfer_count over summary.transfers", () => {
  const model = buildItineraryViewModel(
    baseCard({
      summary: {
        eta_minutes: 40,
        transfers: 0,
        lines: ["N", "R"],
        reason: "One transfer",
      },
      itinerary: {
        total_duration_seconds: 2400,
        transfer_count: 1,
      },
    }),
  );

  assert.equal(model.transferCount, 1);
  assert.ok(model.metaParts.includes("1 transfer"));
});

test("prefers itinerary.arrival_at over inventing depart+eta", () => {
  const model = buildItineraryViewModel(
    baseCard({
      // Summary ETA and step ISO would invent a different clock if used first.
      summary: {
        eta_minutes: 10,
        transfers: 0,
        lines: ["A"],
        reason: "",
      },
      depart_iso: "2026-07-18T14:00:00-04:00",
      route: [
        {
          type: "SUBWAY",
          train_line: "A",
          departure_time_iso: "2026-07-18T14:00:00-04:00",
          // Last step arrival that would win without itinerary preference:
          arrival_time_iso: "2026-07-18T14:10:00-04:00",
        },
      ],
      itinerary: {
        total_duration_seconds: 5340,
        transfer_count: 0,
        arrival_at: "2026-07-18T15:34:00-04:00",
      },
    }),
  );

  const expected = new Date("2026-07-18T15:34:00-04:00").toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  assert.equal(model.arrivalLabel, expected);
  // Must not surface the step-derived 2:10 PM.
  const stepClock = new Date("2026-07-18T14:10:00-04:00").toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  assert.notEqual(model.arrivalLabel, stepClock);
});

test("uses canonical leg seconds for preview rows instead of relative arrival clocks", () => {
  const model = buildItineraryViewModel(
    baseCard({
      itinerary: {
        total_duration_seconds: 2100,
        transfer_count: 0,
        arrival_at: "2026-07-18T14:39:00-04:00",
        structured_recommendation_reasons: ["Avoids the delayed transfer"],
        legs: [
          {
            mode: "WALK",
            board: "Your location",
            alight: "34 St-Penn Station",
            walk_seconds: 240,
          },
          {
            mode: "SUBWAY",
            service_id: "A",
            board: "34 St-Penn Station",
            alight: "Jay St-MetroTech",
            // The legacy clock is deliberately incompatible with a 20-minute ride.
            ride_seconds: 1200,
          },
          {
            mode: "WALK",
            board: "Jay St-MetroTech",
            alight: "Costco Sunset Park",
            walk_seconds: 300,
          },
        ],
      },
      route: baseCard().route.map((step) => ({ ...step, minutes_until_arrival: 99 })),
    }),
  );

  assert.equal(model.durationLabel, "35 min");
  assert.equal(model.events[0].durationLabel, "20 min");
  assert.equal(model.events[1].durationLabel, "5 min");
  assert.deepEqual(model.rationale, ["Avoids the delayed transfer"]);
});

test("canonical multi-stop preview preserves ordered waypoint, dwell, and per-leg durations", () => {
  const model = buildItineraryViewModel(baseCard({
    summary: { eta_minutes: 999, transfers: 0, lines: ["B35", "B37"], reason: "Legacy" },
    itinerary: {
      itinerary_id: "pizza-chain",
      total_duration_seconds: 4380,
      total_dwell_seconds: 1500,
      transfer_count: 1,
      waypoints: [{ display_name: "Luigi's Pizza", address: "123 Fifth Ave", lat: 40.7, lng: -74 }],
      destination: { display_name: "Costco Sunset Park", lat: 40.65, lng: -74.01 },
      segments: [
        {
          segment_index: 0,
          destination: { display_name: "Luigi's Pizza" },
          legs: [{ mode: "BUS", service_id: "B35", alight: "Luigi's Pizza", ride_seconds: 900 }],
          duration_seconds: 900,
        },
        {
          segment_index: 1,
          destination: { display_name: "Costco Sunset Park" },
          legs: [{ mode: "BUS", service_id: "B37", alight: "Costco Sunset Park", ride_seconds: 780 }],
          duration_seconds: 780,
        },
      ],
      dwell_events: [{
        event_type: "dwell",
        after_segment_index: 0,
        waypoint: { display_name: "Luigi's Pizza", dwell_minutes: 25 },
        duration_seconds: 1500,
        source: "default",
      }],
    },
    route: [],
  }));

  assert.deepEqual(model.placeNames, ["Your location", "Luigi's Pizza", "Costco Sunset Park"]);
  assert.equal(model.transferCount, 1);
  assert.ok(model.metaParts.includes("1 transfer"));
  assert.ok(model.metaParts.includes("25 min stop"));
  const waypoint = model.events.find((event) => event.kind === "waypoint");
  assert.ok(waypoint);
  assert.equal(waypoint.title, "Luigi's Pizza");
  assert.equal(waypoint.subtitle, "25 min stop");
  const rides = model.events.filter((event) => event.kind === "bus");
  assert.deepEqual(rides.map((event) => event.routeIds), [["B35"], ["B37"]]);
  assert.deepEqual(rides.map((event) => event.durationLabel), ["15 min", "13 min"]);
});

test("without itinerary, falls back to summary totals (back-compat)", () => {
  const model = buildItineraryViewModel(baseCard());
  assert.equal(model.totalMinutes, 34);
  assert.equal(model.durationLabel, "34 min");
  assert.equal(model.transferCount, 0);
});

test("formats supported structured reason facts and ignores unknown facts", () => {
  const model = buildItineraryViewModel(
    baseCard({
      itinerary: {
        total_duration_seconds: 1800,
        transfer_count: 0,
        structured_recommendation_reasons: [
          { code: "fastest", difference_seconds: 300 },
          { code: "fewer_transfers", transfer_difference: 1 },
          { code: "not_a_supported_reason" },
        ],
      },
    }),
  );

  assert.deepEqual(model.rationale, [
    "About 5 min faster than the next option",
    "Uses 1 fewer transfer",
  ]);
});
