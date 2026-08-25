import assert from "node:assert/strict";
import test from "node:test";

import { parseSseStream } from "./agent-chat-stream.ts";

/** Fakes a ReadableStreamDefaultReader<Uint8Array> over a fixed list of
 *  string chunks, so tests can control exactly where a frame gets split
 *  across `read()` calls without spinning up a real ReadableStream. */
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

async function collect(reader) {
  const events = [];
  for await (const event of parseSseStream(reader)) {
    events.push(event);
  }
  return events;
}

function silenceConsoleWarn(fn) {
  const original = console.warn;
  const calls = [];
  console.warn = (...args) => calls.push(args);
  return fn(calls).finally(() => {
    console.warn = original;
  });
}

test("accepts a JFK route card with an AirTrain tram leg", async () => {
  const payload = {
    card_id: "rc_jfk",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Your location", lat: 40.7484, lng: -73.9857 },
    destination: { label: "JFK Airport", lat: 40.6413, lng: -73.7781 },
    summary: { eta_minutes: 71, transfers: 1, lines: ["F"], reason: "Fewest transfers" },
    route: [
      { type: "SUBWAY", route_id: "F" },
      { type: "TRAM", route_id: "Jamaica AirTrain" },
    ],
    alerts: [],
    itinerary: {
      itinerary_id: "rc_jfk",
      total_duration_seconds: 4260,
      transfer_count: 1,
      legs: [
        { mode: "SUBWAY", service_id: "F", ride_seconds: 2220 },
        { mode: "TRAM", service_id: "Jamaica AirTrain", ride_seconds: 480 },
      ],
    },
  };

  const events = await collect(readerFromChunks([
    `event: route_card\ndata: ${JSON.stringify(payload)}\n\n`,
  ]));

  assert.equal(events.length, 1);
  assert.equal(events[0].type, "route_card");
  assert.equal(events[0].route[1].type, "TRAM");
  assert.equal(events[0].itinerary.legs[1].mode, "TRAM");
});

test("validates nested transfer semantics without dropping later terminal events", async () => {
  const basePayload = {
    card_id: "rc_transfer",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Downtown", lat: 40.69, lng: -73.99 },
    destination: { label: "Midtown", lat: 40.75, lng: -73.98 },
    summary: { eta_minutes: 24, transfers: 1, lines: ["A", "F"], reason: "Fastest" },
    route: [{ type: "SUBWAY", route_id: "A" }],
    alerts: [],
    itinerary: {
      itinerary_id: "it_transfer",
      total_duration_seconds: 1440,
      transfer_count: 1,
      legs: [{
        mode: "WALK",
        walk_seconds: 180,
        transfer_kind: "same_station",
        transfer_semantics: {
          kind: "same_station",
          from_route_id: "A",
          to_route_id: "F",
          from_station_label: "Jay St–MetroTech A/C",
          to_station_label: "Jay St–MetroTech F",
          street_walking_seconds: 0,
          in_station_transfer_seconds: 180,
          total_seconds: 180,
          fragment_count: 1,
          accessibility: "unknown",
        },
      }],
    },
  };
  const malformed = {
    ...basePayload,
    card_id: "rc_bad_transfer",
    itinerary: {
      ...basePayload.itinerary,
      legs: [{
        ...basePayload.itinerary.legs[0],
        transfer_semantics: {
          ...basePayload.itinerary.legs[0].transfer_semantics,
          fragment_count: 0,
        },
      }],
    },
  };
  const frames =
    `event: route_card\ndata: ${JSON.stringify(basePayload)}\n\n` +
    `event: route_card\ndata: ${JSON.stringify(malformed)}\n\n` +
    'event: done\ndata: {"session_id":"s1","turn_id":"t1","stop_reason":"end_turn","usage":{}}\n\n';

  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([frames]));
    assert.equal(events.length, 2);
    assert.equal(events[0].type, "route_card");
    assert.equal(events[0].itinerary.legs[0].transfer_semantics.kind, "same_station");
    assert.deepEqual(events[1], {
      type: "done",
      session_id: "s1",
      turn_id: "t1",
      stop_reason: "end_turn",
      usage: {},
    });
    assert.equal(calls.length, 1);
  });
});

test("accepts a complete route_card with deterministic fallback provenance", async () => {
  const payload = {
    card_id: "rc_outer",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Your location", lat: 40.7484, lng: -73.9857 },
    destination: { label: "Barclays Center", lat: 40.6826, lng: -73.9754 },
    summary: { eta_minutes: 23, transfers: 0, lines: ["Q"], reason: "Fastest route now" },
    route: [
      { type: "WALK", duration_minutes: 3 },
      { type: "SUBWAY", route_id: "Q", duration_minutes: 20 },
    ],
    alerts: [],
    selection_decision: {
      selection_reason: "deterministic_fallback",
      reason_code: "coverage_gap",
      selection_source: "deterministic_fallback",
    },
    itinerary: {
      itinerary_id: "rc_outer",
      total_duration_seconds: 1380,
      transfer_count: 0,
      legs: [
        { mode: "WALK", walk_seconds: 180 },
        { mode: "SUBWAY", service_id: "Q", ride_seconds: 1200 },
      ],
      selection_decision: {
        selection_reason: "deterministic_fallback",
        reason_code: "coverage_gap",
        selection_source: "deterministic_fallback",
      },
    },
  };
  const frames =
    `event: route_card\ndata: ${JSON.stringify(payload)}\n\n` +
    'event: done\ndata: {"session_id":"s1","turn_id":"t1","stop_reason":"end_turn","usage":{}}\n\n';

  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([frames]));
    assert.equal(events.length, 2);
    assert.equal(events[0].type, "route_card");
    assert.equal(events[0].selection_decision.selection_reason, "deterministic_fallback");
    assert.equal(events[0].selection_decision.reason_code, "coverage_gap");
    assert.equal(events[0].selection_decision.selection_source, "deterministic_fallback");
    assert.equal(events[0].itinerary.selection_decision.selection_reason, "deterministic_fallback");
    assert.deepEqual(events[1], {
      type: "done",
      session_id: "s1",
      turn_id: "t1",
      stop_reason: "end_turn",
      usage: {},
    });
    assert.equal(calls.length, 0, "valid fallback card must not be dropped");
  });
});

test("rejects private route selection fields at both passenger boundaries", async () => {
  const safeDecision = {
    selection_reason: "outer_agent_selection",
    reason_code: "fewer_transfers",
    selection_source: "model",
  };
  const base = {
    card_id: "rc_safe",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Your location", lat: 40.7484, lng: -73.9857 },
    destination: { label: "Barclays Center", lat: 40.6826, lng: -73.9754 },
    summary: { eta_minutes: 23, transfers: 0, lines: ["Q"], reason: "Direct route" },
    route: [{ type: "SUBWAY", route_id: "Q", duration_minutes: 20 }],
    alerts: [],
    selection_decision: safeDecision,
    itinerary: {
      itinerary_id: "itin_safe",
      total_duration_seconds: 1380,
      transfer_count: 0,
      legs: [{ mode: "SUBWAY", service_id: "Q", ride_seconds: 1200 }],
      selection_decision: safeDecision,
    },
  };
  const privateFields = {
    selected_candidate_index: 0,
    selected_candidate_id: "cd_private",
    base_score: 23,
    final_score: 23,
    hard_constraints_satisfied: ["at_least_one_transit_mode"],
    penalties: [],
    evidence_ids: ["private:evidence"],
  };
  const payloads = [];
  for (const [field, value] of Object.entries(privateFields)) {
    payloads.push({
      ...base,
      selection_decision: { ...safeDecision, [field]: value },
    });
    payloads.push({
      ...base,
      itinerary: {
        ...base.itinerary,
        selection_decision: { ...safeDecision, [field]: value },
      },
    });
  }
  const frames = payloads
    .map((payload) => `event: route_card\ndata: ${JSON.stringify(payload)}\n\n`)
    .join("")
    + 'event: done\ndata: {"session_id":"s1","turn_id":"t1","stop_reason":"end_turn","usage":{}}\n\n';

  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([frames]));
    assert.deepEqual(events, [{
      type: "done",
      session_id: "s1",
      turn_id: "t1",
      stop_reason: "end_turn",
      usage: {},
    }]);
    assert.equal(calls.length, payloads.length);
  });
});
