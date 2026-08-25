import assert from "node:assert/strict";
import test from "node:test";

import { agentRouteFromCard, agentRoutePlanFromCards } from "./agent-route-selection.ts";

const card = {
  card_id: "rc_1", turn_id: "t1", role: "recommended",
  origin: { label: "Origin", lat: 40.7, lng: -73.9 },
  destination: { label: "Destination", lat: 40.6, lng: -74 },
  summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "Server reason" }, alerts: [],
  route: [{ type: "WALK", end_point: { latitude: 40.6, longitude: -74 } }],
  itinerary: { itinerary_id: "it_1", total_duration_seconds: 2040, transfer_count: 0, arrival_at: "2026-07-16T15:45:00-04:00" },
};

test("uses route geometry while preserving server canonical facts", () => {
  assert.deepEqual(agentRouteFromCard(card)?.destCoords, { lat: 40.6, lng: -74 });
  const plan = agentRoutePlanFromCards([card], "rc_1");
  assert.equal(plan?.candidates[0].itinerary_id, "it_1");
  assert.equal(plan?.candidates[0].total_minutes, 34);
  assert.equal(plan?.entryContext, "chat");
});

test("canonical duration wins when the legacy card summary disagrees", () => {
  const plan = agentRoutePlanFromCards([
    { ...card, summary: { ...card.summary, eta_minutes: 5 }, itinerary: { ...card.itinerary, total_duration_seconds: 2040 } },
  ], "rc_1");
  assert.equal(plan?.candidates[0].total_minutes, 34);
  assert.equal(plan?.candidates[0].score_breakdown.duration_minutes, 34);
});

test("refuses a card without canonical itinerary", () => {
  assert.equal(agentRoutePlanFromCards([{ ...card, itinerary: undefined }], "rc_1"), null);
});

test("refuses incomplete canonical timing rather than using the summary", () => {
  assert.equal(
    agentRoutePlanFromCards([{ ...card, itinerary: { ...card.itinerary, total_duration_seconds: undefined } }], "rc_1"),
    null,
  );
});

test("keeps server recommendation role and itinerary identity", () => {
  const plan = agentRoutePlanFromCards([{ ...card, role: "alternative" }], "rc_1");
  assert.equal(plan?.candidates[0].is_recommended, false);
  assert.equal(plan?.candidates[0].itinerary?.itinerary_id, "it_1");
});

test("uses trailing transit geometry when no final walk exists", () => {
  const plan = agentRoutePlanFromCards([{ ...card, route: [{ type: "SUBWAY", arrival_coords: { latitude: 40.61, longitude: -74.01 } }] }], "rc_1");
  assert.deepEqual(plan?.destination.coordinates, { lat: 40.61, lng: -74.01 });
});

test("returns null for empty route geometry", () => assert.equal(agentRouteFromCard({ ...card, route: [] }), null));
test("returns null for missing route geometry", () => assert.equal(agentRouteFromCard({ ...card, route: undefined }), null));
test("falls back to card destination coordinates", () => assert.deepEqual(agentRouteFromCard({ ...card, route: [{ type: "SUBWAY" }] })?.destCoords, { lat: 40.6, lng: -74 }));
test("rejects invalid card destination coordinates", () => assert.equal(agentRouteFromCard({ ...card, destination: { label: "bad", lat: "bad", lng: -74 }, route: [{ type: "SUBWAY" }] }), null));
test("keeps all canonical cards in a multi-card route plan", () => {
  const second = { ...card, card_id: "rc_2", role: "alternative", itinerary: { ...card.itinerary, itinerary_id: "it_2" } };
  const plan = agentRoutePlanFromCards([card, second], "rc_2");
  assert.equal(plan?.candidates.length, 2);
  assert.equal(plan?.activeCandidateId, "rc_2");
});
test("preserves chained itinerary identity", () => {
  const plan = agentRoutePlanFromCards([{ ...card, itinerary: { ...card.itinerary, itinerary_id: "chain_1", segments: [{ segment_index: 0, legs: [] }] } }], "rc_1");
  assert.equal(plan?.candidates[0].itinerary_id, "chain_1");
});

test("accepts compact latitude and longitude route geometry", () => {
  const selection = agentRouteFromCard({ ...card, route: [{ type: "WALK", end_point: { lat: 40.61, lng: -74.01 } }] });
  assert.deepEqual(selection?.destCoords, { lat: 40.61, lng: -74.01 });
});

test("trailing walk without an end point safely uses card destination", () => {
  const selection = agentRouteFromCard({ ...card, route: [{ type: "WALK", start_point: { latitude: 40.61, longitude: -74.01 } }] });
  assert.deepEqual(selection?.destCoords, { lat: 40.6, lng: -74 });
});

test("selected alternative remains the active canonical candidate", () => {
  const alternative = { ...card, card_id: "rc_2", role: "alternative", itinerary: { ...card.itinerary, itinerary_id: "it_2" } };
  const plan = agentRoutePlanFromCards([card, alternative], "rc_2");
  assert.equal(plan?.activeCandidateId, "rc_2");
  assert.equal(plan?.candidates.find((candidate) => candidate.id === "rc_2")?.is_recommended, false);
});

test("chained segment indexes are passed through unchanged", () => {
  const itinerary = { ...card.itinerary, segments: [{ segment_index: 4, legs: [] }, { segment_index: 9, legs: [] }] };
  const plan = agentRoutePlanFromCards([{ ...card, itinerary }], "rc_1");
  assert.deepEqual(plan?.candidates[0].itinerary?.segments.map((segment) => segment.segment_index), [4, 9]);
});

test("chat entry context and passenger-safe selection decision remain intact", () => {
  const decision = {
    selection_reason: "outer_agent_selection",
    reason_code: "fastest",
    selection_source: "model",
  };
  const plan = agentRoutePlanFromCards([{ ...card, itinerary: { ...card.itinerary, selection_decision: decision } }], "rc_1");
  assert.equal(plan?.entryContext, "chat");
  assert.deepEqual(plan?.candidates[0].itinerary?.selection_decision, decision);
});
