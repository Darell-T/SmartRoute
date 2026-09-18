import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  ItineraryCardSkeleton,
  RecommendedItineraryCard,
  RecommendedItineraryFromCards,
} from "./recommended-itinerary-card.tsx";

function card(overrides = {}) {
  return {
    card_id: "rc_1",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Your location", lat: 40.7, lng: -73.9 },
    destination: { label: "Costco", lat: 40.6, lng: -74 },
    summary: {
      eta_minutes: 34,
      transfers: 0,
      lines: ["A"],
      reason: "Server reason",
    },
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
          stops: [{ name: "A station" }, { name: "Hoyt St" }, { name: "Costco" }],
        },
      ],
    },
    ...overrides,
  };
}

function renderCard(overrides = {}, props = {}) {
  return renderToStaticMarkup(
    createElement(RecommendedItineraryCard, {
      card: card(overrides),
      ...props,
    }),
  );
}

function renderFromCards(cards, props = {}) {
  return renderToStaticMarkup(
    createElement(RecommendedItineraryFromCards, { cards, ...props }),
  );
}

test("an invalid card says the itinerary is unavailable", () => {
  const html = renderCard({ itinerary: undefined });
  assert.match(html, /This itinerary is unavailable\./);
  assert.match(html, /sr-itinerary-card--invalid/);
});

test("arrival and first-leg labels join with a dot", () => {
  const html = renderCard({
    summary: {
      eta_minutes: 34,
      transfers: 0,
      lines: ["A"],
      reason: "Server reason",
      first_leg_arrival: {
        route_id: "A",
        catchable_arrival_minutes: 4,
        source_status: "live",
      },
    },
  });
  assert.match(html, /Arrive around 3:45 PM/);
  assert.match(html, /Next realistic A: 4 min/);
  assert.match(html, / · /);
});

test("an arrival clock without a first-leg label stands alone", () => {
  const html = renderCard();
  assert.match(html, /Arrive around 3:45 PM/);
  assert.doesNotMatch(html, /Next realistic/);
});

test("a first-leg label without an arrival clock stands alone", () => {
  const html = renderCard({
    itinerary: {
      itinerary_id: "it_1",
      total_duration_seconds: 2040,
      transfer_count: 0,
      total_dwell_seconds: 0,
      legs: [],
    },
    summary: {
      eta_minutes: 34,
      transfers: 0,
      lines: ["A"],
      reason: "Server reason",
      first_leg_arrival: {
        route_id: "A",
        catchable_arrival_minutes: 4,
        source_status: "scheduled",
      },
    },
  });
  assert.match(html, /Next realistic A: 4 min/);
  assert.doesNotMatch(html, /Arrive around/);
});

test("missing arrival facts omit the arrival line", () => {
  const html = renderCard({
    itinerary: {
      itinerary_id: "it_1",
      total_duration_seconds: 2040,
      transfer_count: 0,
      total_dwell_seconds: 0,
      legs: [],
    },
  });
  assert.doesNotMatch(html, /sr-itinerary-card__arrive/);
});

test("one meta part does not insert a second separator", () => {
  const html = renderCard();
  assert.match(html, /0 transfers/);
  assert.doesNotMatch(html, /min stop/);
});

test("dwell adds a second meta part after transfers", () => {
  const html = renderCard({
    itinerary: {
      itinerary_id: "it_1",
      total_duration_seconds: 2040,
      transfer_count: 1,
      arrival_at: "2026-07-16T15:45:00-04:00",
      total_dwell_seconds: 120,
      legs: [],
    },
  });
  assert.match(html, /1 transfer/);
  assert.match(html, /2 min stop/);
});

test("transit legs stay collapsed so intermediate stops stay hidden", () => {
  const html = renderCard();
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /data-expanded="false"/);
  assert.doesNotMatch(html, /Hoyt St/);
});

test("the default primary action is Open on map", () => {
  const html = renderCard({}, { onSelect() {} });
  assert.match(html, /Open on map/);
});

test("a custom primary action label replaces the default", () => {
  const html = renderCard(
    {},
    { primaryActionLabel: "See this trip", onSelect() {} },
  );
  assert.match(html, /See this trip/);
  assert.doesNotMatch(html, /Open on map/);
});

test("an itinerary with no legs still shows duration", () => {
  const html = renderCard({
    itinerary: {
      itinerary_id: "it_1",
      total_duration_seconds: 2040,
      transfer_count: 0,
      arrival_at: "2026-07-16T15:45:00-04:00",
      total_dwell_seconds: 0,
      legs: [],
    },
  });
  assert.match(html, /34 min/);
  assert.doesNotMatch(html, /subway leg/);
  assert.doesNotMatch(html, /Walking directions/);
});

test("a final walk marks the card as ending on foot", () => {
  const html = renderCard({
    itinerary: {
      itinerary_id: "it_1",
      total_duration_seconds: 300,
      transfer_count: 0,
      total_dwell_seconds: 0,
      legs: [
        {
          mode: "WALK",
          walk_seconds: 300,
          board: { label: "Your location" },
          alight: { label: "Costco" },
        },
      ],
    },
  });
  assert.match(html, /data-has-final-walk="true"/);
});

test("a transit last leg is not a final walk", () => {
  const html = renderCard();
  assert.match(html, /data-has-final-walk="false"/);
});

test("an empty card list renders nothing", () => {
  const html = renderFromCards([]);
  assert.equal(html, "");
});

test("a card with a canonical itinerary wins the group", () => {
  const html = renderFromCards([
    {
      card_id: "legacy",
      turn_id: "t1",
      role: "alternate",
      destination: { label: "Elsewhere", lat: 40.6, lng: -74 },
      summary: { eta_minutes: 99, transfers: 3, lines: [], reason: "" },
      route: [],
      alerts: [],
    },
    card(),
  ]);
  assert.match(html, /34 min/);
  assert.match(html, /Costco/);
  assert.doesNotMatch(html, /This itinerary is unavailable/);
});

test("cards without itineraries render unavailable", () => {
  const html = renderFromCards([
    {
      card_id: "legacy",
      turn_id: "t1",
      role: "recommended",
      destination: { label: "Costco", lat: 40.6, lng: -74 },
      summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "" },
      route: [],
      alerts: [],
    },
  ]);
  assert.match(html, /This itinerary is unavailable\./);
});

test("a selected source card id marks the group selected", () => {
  const html = renderFromCards([card()], { selectedCardId: "rc_1" });
  assert.match(html, /data-selected="true"/);
});

test("a foreign selected id leaves the group unselected", () => {
  const html = renderFromCards([card()], { selectedCardId: "other" });
  assert.match(html, /data-selected="false"/);
});

test("a missing primary card id still renders the last card", () => {
  const html = renderFromCards([
    {
      turn_id: "t1",
      role: "recommended",
      destination: { label: "", lat: 0, lng: 0 },
      summary: { eta_minutes: 0, transfers: 0, lines: [], reason: "" },
      route: [],
      alerts: [],
    },
    {
      card_id: "tail",
      turn_id: "t1",
      role: "alternate",
      destination: { label: "Tail", lat: 40.6, lng: -74 },
      summary: { eta_minutes: 0, transfers: 0, lines: [], reason: "" },
      route: [],
      alerts: [],
    },
  ]);
  assert.match(html, /This itinerary is unavailable\./);
});

test("the skeleton is hidden from assistive tech", () => {
  const html = renderToStaticMarkup(createElement(ItineraryCardSkeleton));
  assert.match(html, /sr-itinerary-card--skeleton/);
  assert.match(html, /aria-hidden="true"/);
});
