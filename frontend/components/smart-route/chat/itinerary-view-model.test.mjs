import assert from "node:assert/strict";
import test from "node:test";

import {
  buildItineraryViewModel,
  buildMergedItineraryViewModel,
  formatDurationMinutes,
  isSupportedSubwayRoute,
  parseRationale,
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

test("builds a standard single-destination itinerary from supplied route data", () => {
  const model = buildItineraryViewModel(baseCard());

  assert.equal(model.invalid, false);
  assert.equal(model.recommended, true);
  assert.deepEqual(model.placeNames, ["Your location", "Costco Sunset Park"]);
  assert.equal(model.durationLabel, "34 min");
  assert.equal(model.totalMinutes, 34);
  assert.equal(model.transferCount, 0);
  assert.ok(model.metaParts.includes("0 transfers"));
  assert.ok(model.arrivalLabel);
  assert.match(model.arrivalLabel, /\d{1,2}:\d{2}\s?(AM|PM)/i);
  assert.deepEqual(model.rationale, [
    "No bus",
    "Elevator access for the cart",
  ]);
  assert.equal(model.primaryActionLabel, "Open on map");
  assert.equal(model.events.length, 3);
  assert.equal(model.events[0].kind, "walk");
  assert.equal(model.events[1].kind, "subway");
  assert.deepEqual(model.events[1].routeIds, ["A"]);
  assert.equal(model.events[2].kind, "walk");
  // Walking never carries subway route ids.
  assert.deepEqual(model.events[0].routeIds, []);
  assert.deepEqual(model.events[2].routeIds, []);
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
  assert.match(model.events[0].title, /Prada/);
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

test("merged multi-stop itinerary is one card with pickup dwell event", () => {
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

  const pickup = model.events.find((e) => e.kind === "pickup");
  assert.ok(pickup, "expected a pickup/dwell event between legs");
  assert.equal(pickup.subtitle, "Sunday Morning");
  assert.equal(pickup.durationMinutes, 25);
  assert.ok(model.metaParts.some((p) => p.includes("pickup")));

  // Subway bullets only on subway events.
  for (const event of model.events) {
    if (event.kind === "walk" || event.kind === "pickup") {
      assert.deepEqual(event.routeIds, []);
    }
  }
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
