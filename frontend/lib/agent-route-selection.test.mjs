import assert from "node:assert/strict";
import test from "node:test";

import { agentRouteFromCard } from "./agent-route-selection.ts";

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
