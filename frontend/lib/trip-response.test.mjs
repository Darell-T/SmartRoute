import assert from "node:assert/strict";
import test from "node:test";

import { planTrip } from "./api.ts";
import { parseCanonicalItinerary } from "./canonical-itinerary-schema.ts";
import { parseSseStream } from "./agent-chat-stream.ts";
import { deriveTransitRouteIds, isAlertForRouteIds, normalizeTripCandidates } from "./route-planning.ts";
import { parseTripResponse, TRIP_PLAN_FAILED } from "./trip-response.ts";

const backendSelectionDecision = {
  selected_candidate_index: 0,
  selected_candidate_id: "candidate-0",
  base_score: 34,
  final_score: 38.5,
  hard_constraints_satisfied: ["at_least_one_transit_mode"],
  penalties: [{ source: "transfers", amount: 4, reason: "transfer cost" }],
  selection_reason: "lowest_final_score",
  reason_code: null,
  selection_source: "deterministic_fallback",
  evidence_ids: ["structured:evt-1"],
};

const publicSelectionDecision = {
  selection_reason: "lowest_final_score",
  reason_code: null,
  selection_source: "deterministic_fallback",
};

const itinerary = {
  itinerary_id: "it_1",
  total_duration_seconds: 2040,
  transfer_count: 1,
  departure_at: null,
  arrival_at: "2026-07-16T15:45:00-04:00",
  legs: [{ mode: "SUBWAY", service_id: "F", ride_seconds: 1800 }],
  selection_decision: backendSelectionDecision,
};

const validTrip = {
  recommendation: "Fastest route now",
  route: [{ type: "SUBWAY", route_id: "F" }],
  selected_route_index: 0,
  alerts: [],
  route_candidates: [
    {
      id: "candidate-0",
      index: 0,
      steps: [{ type: "SUBWAY", route_id: "F" }],
      is_recommended: true,
      total_minutes: 34,
      itinerary,
      score_breakdown: { duration_minutes: 34, transfers: 1, transit_lines: ["F"] },
    },
  ],
};

function readerFromChunks(chunks) {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    async read() {
      if (index >= chunks.length) return { done: true, value: undefined };
      const value = encoder.encode(chunks[index]);
      index += 1;
      return { done: false, value };
    },
  };
}

test("parses a valid REST trip with required canonical itinerary fields", () => {
  const parsed = parseTripResponse(validTrip);
  const selected = parsed.route_candidates[0];
  const decision = selected.itinerary.selection_decision;
  assert.equal(selected.itinerary.itinerary_id, "it_1");
  assert.equal(selected.total_minutes, 34);
  assert.equal(selected.itinerary.transfer_count, 1);
  assert.equal(selected.itinerary.total_duration_seconds, 2040);
  assert.equal(selected.itinerary.arrival_at, "2026-07-16T15:45:00-04:00");
  assert.equal(selected.itinerary.departure_at, null);
  assert.deepEqual(decision, publicSelectionDecision);
  assert.equal("selected_candidate_index" in decision, false);
  assert.equal("evidence_ids" in decision, false);
  assert.equal("penalties" in decision, false);
  assert.equal("base_score" in decision, false);
  assert.equal("final_score" in decision, false);
  assert.equal(normalizeTripCandidates(parsed)?.selectedIndex, 0);
});

test("rejects a REST trip whose selected candidate has no canonical itinerary", () => {
  const { itinerary: _ignored, ...candidate } = validTrip.route_candidates[0];
  assert.throws(
    () => parseTripResponse({ ...validTrip, route_candidates: [candidate] }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("rejects malformed duration totals instead of inventing minutes", () => {
  assert.throws(
    () =>
      parseTripResponse({
        ...validTrip,
        route_candidates: [
          {
            ...validTrip.route_candidates[0],
            itinerary: { ...itinerary, total_duration_seconds: "34 min" },
          },
        ],
      }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("rejects a malformed transfer count", () => {
  assert.throws(
    () =>
      parseTripResponse({
        ...validTrip,
        route_candidates: [
          {
            ...validTrip.route_candidates[0],
            itinerary: { ...itinerary, transfer_count: 1.5 },
          },
        ],
      }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("SSE route cards reuse the same itinerary schema as REST", async () => {
  const shared = parseCanonicalItinerary(itinerary);
  assert.equal(shared?.itinerary_id, "it_1");
  const events = [];
  for await (const event of parseSseStream(
    readerFromChunks([
      `event: route_card\ndata: ${JSON.stringify({
        card_id: "rc_1",
        turn_id: "t1",
        role: "recommended",
        origin: { label: "Origin", lat: 40.7484, lng: -73.9857 },
        destination: { label: "Destination", lat: 40.6413, lng: -73.7781 },
        summary: { eta_minutes: 34, transfers: 1, lines: ["F"], reason: "Fastest" },
        route: [{ type: "SUBWAY", route_id: "F" }],
        alerts: [],
        itinerary,
        selection_decision: backendSelectionDecision,
      })}\n\n`,
    ]),
  )) {
    events.push(event);
  }
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "route_card");
  assert.deepEqual(parseCanonicalItinerary(events[0].itinerary), shared);
  assert.deepEqual(events[0].itinerary.selection_decision, publicSelectionDecision);
  assert.deepEqual(events[0].selection_decision, publicSelectionDecision);
  assert.equal("evidence_ids" in events[0].selection_decision, false);
});

test("planTrip sends a malformed 200 through the existing API error path", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ recommendation: "ok", route: [], alerts: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  try {
    await assert.rejects(planTrip(40.75, -73.99, "JFK"), { message: TRIP_PLAN_FAILED });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("planTrip returns the validated trip for a well-formed 200", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify(validTrip), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  try {
    const trip = await planTrip(40.75, -73.99, "JFK");
    assert.equal(trip.route_candidates[0].itinerary.itinerary_id, "it_1");
    assert.equal(normalizeTripCandidates(trip)?.selectedIndex, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("parseTripResponse rejects non-object steps and alerts that omit header", () => {
  assert.throws(
    () => parseTripResponse({ ...validTrip, route: [null] }),
    { message: TRIP_PLAN_FAILED },
  );
  assert.throws(
    () => parseTripResponse({ ...validTrip, route: [{ type: "HOVERBOARD" }] }),
    { message: TRIP_PLAN_FAILED },
  );
  assert.throws(
    () => parseTripResponse({ ...validTrip, alerts: [{ description: "no header" }] }),
    { message: TRIP_PLAN_FAILED },
  );
  assert.throws(
    () => parseTripResponse({ ...validTrip, route: [[]] }),
    { message: TRIP_PLAN_FAILED },
  );
  assert.throws(
    () => parseTripResponse({ ...validTrip, alerts: [null] }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("parseTripResponse treats null optional candidate strings as absent", () => {
  const parsed = parseTripResponse({
    ...validTrip,
    route_candidates: [
      {
        ...validTrip.route_candidates[0],
        arrival_at: null,
        recommendation_reason: null,
      },
    ],
  });
  assert.equal(parsed.route_candidates[0].arrival_at, undefined);
  assert.equal(parsed.route_candidates[0].recommendation_reason, undefined);
});

test("parseTripResponse rejects a numeric route_id", () => {
  assert.throws(
    () => parseTripResponse({ ...validTrip, route: [{ type: "SUBWAY", route_id: 42 }] }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("parseTripResponse rejects numeric routeIds", () => {
  assert.throws(
    () => parseTripResponse({ ...validTrip, alerts: [{ header: "delay", routeIds: 42 }] }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("parseTripResponse rejects malformed step coordinates", () => {
  assert.throws(
    () =>
      parseTripResponse({
        ...validTrip,
        route: [
          {
            type: "SUBWAY",
            route_id: "F",
            start_point: { latitude: "north", longitude: -73.99 },
          },
        ],
      }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("parseTripResponse rejects malformed intermediate stops", () => {
  assert.throws(
    () =>
      parseTripResponse({
        ...validTrip,
        route: [{ type: "SUBWAY", route_id: "F", intermediate_stops: [42] }],
      }),
    { message: TRIP_PLAN_FAILED },
  );
  assert.throws(
    () =>
      parseTripResponse({
        ...validTrip,
        route: [
          {
            type: "SUBWAY",
            route_id: "F",
            intermediate_stop_locations: [{ name: 42, lat: 40.75, lng: -73.99 }],
          },
        ],
      }),
    { message: TRIP_PLAN_FAILED },
  );
});

test("parsed trip steps and alerts are safe for route-id consumers", () => {
  const parsed = parseTripResponse({
    ...validTrip,
    route: [
      {
        type: "SUBWAY",
        route_id: " f ",
        start_point: { latitude: 40.75, longitude: -73.99 },
        end_point: { lat: 40.64, lng: -73.78 },
        intermediate_stops: ["Herald Sq"],
        intermediate_stop_locations: [{ name: "Herald Sq", lat: 40.75, lng: -73.99 }],
      },
    ],
    alerts: [{ header: "delay", routeIds: [" f "], description: "slow" }],
  });
  assert.deepEqual(deriveTransitRouteIds(parsed.route), ["F"]);
  assert.equal(isAlertForRouteIds(parsed.alerts[0], ["F"]), true);
  assert.equal(parsed.route[0].start_point?.latitude, 40.75);
  assert.equal(parsed.route[0].end_point?.latitude, 40.64);
});

test("planTrip translates invalid JSON on HTTP 200 to the trip-plan error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("{not json", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  try {
    await assert.rejects(planTrip(40.75, -73.99, "JFK"), { message: TRIP_PLAN_FAILED });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("planTrip maps a non-503 HTTP error to the trip-plan error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("nope", { status: 500 });
  try {
    await assert.rejects(planTrip(40.75, -73.99, "JFK"), { message: TRIP_PLAN_FAILED });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("planTrip does not classify a non-JSON decode failure as a malformed trip", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => {
      throw new TypeError("truncated body");
    },
  });
  try {
    await assert.rejects(planTrip(40.75, -73.99, "JFK"), { name: "TypeError" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("selection_decision may omit reason_code and still strips private fields", () => {
  const parsed = parseCanonicalItinerary({
    ...itinerary,
    selection_decision: {
      selection_reason: "lowest_final_score",
      selection_source: "model",
      selected_candidate_index: 3,
    },
  });
  assert.deepEqual(parsed?.selection_decision, {
    selection_reason: "lowest_final_score",
    selection_source: "model",
  });
  assert.equal(parseCanonicalItinerary({ itinerary_id: "bad" }), null);
});

test("canonical legs may identify a stop by station_name alone", () => {
  const parsed = parseCanonicalItinerary({
    itinerary_id: "it_station",
    total_duration_seconds: 60,
    transfer_count: 0,
    legs: [{ mode: "SUBWAY", board: { station_name: "Canal St" } }],
  });
  assert.equal(parsed?.legs[0].board.station_name, "Canal St");
});
