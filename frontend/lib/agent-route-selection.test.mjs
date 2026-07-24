import assert from "node:assert/strict";
import test from "node:test";

import {
  agentRouteFromCard,
  agentRoutePlanFromCards,
} from "./agent-route-selection.ts";

const ORIGIN = { label: "Your location", lat: 40.7484, lng: -73.9857 };
const DESTINATION = { label: "Costco Sunset Park", lat: 40.6559, lng: -74.0089 };

function baseCard(overrides = {}) {
  return {
    card_id: "rc_1",
    turn_id: "t1",
    role: "recommended",
    origin: ORIGIN,
    destination: DESTINATION,
    summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "Fewest transfers." },
    route: [],
    alerts: [],
    ...overrides,
  };
}

test("builds a selection from a card's steps, taking destCoords from the trailing WALK step", () => {
  const card = baseCard({
    route: [
      {
        type: "SUBWAY",
        train_line: "A",
        departure_coords: { latitude: 40.7527, longitude: -73.9862 },
        arrival_coords: { latitude: 40.6627, longitude: -73.9958 },
      },
      {
        type: "WALK",
        start_point: { latitude: 40.6627, longitude: -73.9958 },
        end_point: { latitude: 40.6559, longitude: -74.0089 },
      },
    ],
  });

  const selection = agentRouteFromCard(card);

  assert.deepEqual(selection, {
    cardId: "rc_1",
    steps: card.route,
    destCoords: { lat: 40.6559, lng: -74.0089 },
  });
});

test("returns null when the card has an empty route array", () => {
  assert.equal(agentRouteFromCard(baseCard({ route: [] })), null);
});

test("returns null when the card's route field is missing entirely", () => {
  const card = baseCard();
  delete card.route;
  assert.equal(agentRouteFromCard(card), null);
});

test("destCoords falls back to arrival_coords when the last step is not a WALK", () => {
  const card = baseCard({
    route: [
      {
        type: "SUBWAY",
        train_line: "D",
        departure_coords: { latitude: 40.7484, longitude: -73.9857 },
        arrival_coords: { latitude: 40.6459, longitude: -74.0067 },
      },
    ],
  });

  const selection = agentRouteFromCard(card);

  assert.deepEqual(selection?.destCoords, { lat: 40.6459, lng: -74.0067 });
});

test("destCoords falls back to the card's destination label when the last step carries no geometry", () => {
  const card = baseCard({
    route: [{ type: "SUBWAY", train_line: "D" }],
  });

  const selection = agentRouteFromCard(card);

  assert.deepEqual(selection?.destCoords, { lat: DESTINATION.lat, lng: DESTINATION.lng });
});

test("destCoords falls back to the card's destination when the trailing WALK step has no end_point", () => {
  const card = baseCard({
    route: [{ type: "WALK", start_point: { latitude: 40.6627, longitude: -73.9958 } }],
  });

  const selection = agentRouteFromCard(card);

  assert.deepEqual(selection?.destCoords, { lat: DESTINATION.lat, lng: DESTINATION.lng });
});

test("destCoords accepts the compact lat/lng coordinate shape from agent route steps", () => {
  const card = baseCard({
    route: [{ type: "WALK", end_point: { lat: 40.6559, lng: -74.0089 } }],
  });

  assert.deepEqual(agentRouteFromCard(card)?.destCoords, {
    lat: 40.6559,
    lng: -74.0089,
  });
});

test("returns null instead of handing invalid destination coordinates to the map", () => {
  const card = baseCard({
    destination: { label: "Unknown destination" },
    route: [{ type: "WALK", end_point: {} }],
  });

  assert.equal(agentRouteFromCard(card), null);
});

test("converts a turn's route cards into the shared rail candidate model", () => {
  const recommended = baseCard({
    route: [{ type: "WALK", end_point: { latitude: 40.6559, longitude: -74.0089 } }],
  });
  const alternative = baseCard({
    card_id: "rc_2",
    role: "alternative",
    summary: { eta_minutes: 39, transfers: 1, lines: ["Q", "D"], reason: "More walking." },
    route: [{ type: "WALK", end_point: { latitude: 40.6559, longitude: -74.0089 } }],
  });

  const plan = agentRoutePlanFromCards([recommended, alternative], "rc_2");

  assert.equal(plan?.activeCandidateId, "rc_2");
  assert.equal(plan?.candidates.length, 2);
  assert.equal(plan?.candidates[0].is_recommended, true);
  assert.equal(plan?.candidates[1].is_recommended, false);
  assert.deepEqual(plan?.destination.coordinates, { lat: 40.6559, lng: -74.0089 });
});

test("agentRoutePlanFromCards prefers itinerary totals over summary", () => {
  const card = baseCard({
    summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "Fewest transfers." },
    itinerary: {
      total_duration_seconds: 5340,
      transfer_count: 2,
      arrival_at: "2026-07-16T15:45:00-04:00",
    },
    route: [{ type: "WALK", end_point: { latitude: 40.6559, longitude: -74.0089 } }],
  });

  const plan = agentRoutePlanFromCards([card], "rc_1");
  assert.ok(plan);
  assert.equal(plan.candidates[0].total_minutes, 89);
  assert.equal(plan.candidates[0].score_breakdown.duration_minutes, 89);
  assert.equal(plan.candidates[0].score_breakdown.transfers, 2);
  assert.equal(plan.candidates[0].arrival_at, "2026-07-16T15:45:00-04:00");
});

test("agentRoutePlanFromCards uses summary when itinerary is absent", () => {
  const card = baseCard({
    summary: { eta_minutes: 34, transfers: 1, lines: ["A"], reason: "ok" },
    route: [{ type: "WALK", end_point: { latitude: 40.6559, longitude: -74.0089 } }],
  });

  const plan = agentRoutePlanFromCards([card], "rc_1");
  assert.equal(plan?.candidates[0].total_minutes, 34);
  assert.equal(plan?.candidates[0].score_breakdown.transfers, 1);
  assert.equal(plan?.candidates[0].arrival_at, undefined);
});
