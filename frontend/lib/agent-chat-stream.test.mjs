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

test("parses a full meta/token/done sequence delivered in one chunk", async () => {
  const chunk =
    'event: meta\ndata: {"session_id":"s1","turn_id":"t1"}\n\n' +
    'event: token\ndata: {"text":"Hello"}\n\n' +
    'event: done\ndata: {"session_id":"s1","turn_id":"t1","stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":2}}\n\n';

  const events = await collect(readerFromChunks([chunk]));

  assert.deepEqual(events, [
    { type: "meta", session_id: "s1", turn_id: "t1" },
    { type: "token", text: "Hello" },
    {
      type: "done",
      session_id: "s1",
      turn_id: "t1",
      stop_reason: "end_turn",
      usage: { input_tokens: 1, output_tokens: 2 },
    },
  ]);
});

test("reassembles a frame split across chunk boundaries, including mid-line splits", async () => {
  const full = 'event: token\ndata: {"text":"partial chunk boundary"}\n\n';
  // Split in the middle of the "data:" line itself, not just between lines.
  const splitPoint = full.indexOf('"text"') + 3;
  const chunks = [full.slice(0, splitPoint), full.slice(splitPoint)];

  const events = await collect(readerFromChunks(chunks));

  assert.deepEqual(events, [{ type: "token", text: "partial chunk boundary" }]);
});

test("reassembles a frame whose bytes split a multi-byte UTF-8 character", async () => {
  // "café" — the é is a 2-byte UTF-8 sequence; split the encoded bytes so
  // the second byte of that sequence starts the next chunk.
  const encoder = new TextEncoder();
  const full = 'event: token\ndata: {"text":"café"}\n\n';
  const bytes = encoder.encode(full);
  const splitIndex = full.indexOf("é");
  // Encode up through the char just before é, then the é+rest separately at
  // the byte level so the split lands inside the 2-byte sequence.
  const prefixBytes = encoder.encode(full.slice(0, splitIndex));
  const chunk1 = bytes.slice(0, prefixBytes.length + 1);
  const chunk2 = bytes.slice(prefixBytes.length + 1);

  let index = 0;
  const rawChunks = [chunk1, chunk2];
  const reader = {
    async read() {
      if (index >= rawChunks.length) return { done: true, value: undefined };
      const value = rawChunks[index];
      index += 1;
      return { done: false, value };
    },
  };

  const events = await collect(reader);
  assert.deepEqual(events, [{ type: "token", text: "café" }]);
});

test("skips heartbeat comment frames without emitting an event", async () => {
  const chunk =
    'event: meta\ndata: {"session_id":"s1","turn_id":"t1"}\n\n' +
    ": ping\n\n" +
    'event: token\ndata: {"text":"after heartbeat"}\n\n';

  const events = await collect(readerFromChunks([chunk]));

  assert.deepEqual(events, [
    { type: "meta", session_id: "s1", turn_id: "t1" },
    { type: "token", text: "after heartbeat" },
  ]);
});

test("skips a malformed frame (invalid JSON) and warns, without dropping later events", async () => {
  const chunk =
    "event: token\ndata: {not json}\n\n" +
    'event: token\ndata: {"text":"still works"}\n\n';

  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([chunk]));
    assert.deepEqual(events, [{ type: "token", text: "still works" }]);
    assert.ok(calls.length >= 1, "expected a console.warn for the malformed frame");
  });
});

test("skips a frame with an unknown event type and warns", async () => {
  const chunk =
    'event: mystery\ndata: {"foo":"bar"}\n\n' +
    'event: token\ndata: {"text":"ok"}\n\n';

  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([chunk]));
    assert.deepEqual(events, [{ type: "token", text: "ok" }]);
    assert.ok(calls.length >= 1, "expected a console.warn for the unknown event type");
  });
});

test("skips a frame missing required fields and warns", async () => {
  const chunk =
    'event: tool_start\ndata: {"tool_call_id":"c1"}\n\n' + // missing tool/label
    'event: token\ndata: {"text":"ok"}\n\n';

  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([chunk]));
    assert.deepEqual(events, [{ type: "token", text: "ok" }]);
    assert.ok(calls.length >= 1, "expected a console.warn for the missing-fields frame");
  });
});

test("parses a route_card event with the full nested payload", async () => {
  const payload = {
    card_id: "rc_abc123",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Home", lat: 40.7, lng: -73.9 },
    destination: { label: "Costco", lat: 40.8, lng: -73.8 },
    summary: { eta_minutes: 22, transfers: 1, lines: ["A", "C"], reason: "Fastest route" },
    route: [{ type: "WALK" }],
    alerts: [],
    leg_label: "Leg 1",
    depart_iso: "2026-07-16T10:00:00-04:00",
  };
  const chunk = `event: route_card\ndata: ${JSON.stringify(payload)}\n\n`;

  const events = await collect(readerFromChunks([chunk]));

  assert.deepEqual(events, [{ type: "route_card", ...payload }]);
});

test("round-trips every production route and canonical itinerary field without dropping facts", async () => {
  const firstLeg = {
    mode: "SUBWAY", service_id: "Q", board: { name: "Canal St" }, alight: { label: "Pickup" }, stop_count: 3,
    stops: [{ name: "Canal St", lat: 40.72, lng: -74 }, { name: "Pickup", lat: 40.75, lng: -73.95 }],
    departure_at: "2026-07-25T14:00:00Z", arrival_at: "2026-07-25T14:10:00Z", walk_seconds: 0, wait_seconds: 60,
    ride_seconds: 540, transfer_seconds: 0, geometry: { encodedPolyline: "abc" }, service_data_basis: "mixed", segment_index: 0,
  };
  const secondLeg = {
    mode: "WALK", service_id: null, board: { name: "Pickup" }, alight: { label: "Work" }, stop_count: 0,
    stops: [{ name: "Pickup", lat: 40.75, lng: -73.95 }, { name: "Work", lat: 40.8, lng: -73.8 }],
    departure_at: "2026-07-25T14:15:00Z", arrival_at: "2026-07-25T14:20:00Z", walk_seconds: 300, wait_seconds: 0,
    ride_seconds: 0, transfer_seconds: 0, geometry: { encodedPolyline: "def" }, service_data_basis: "mixed", segment_index: 1,
  };
  const payload = {
    card_id: "full", turn_id: "turn", role: "recommended",
    origin: { label: "Home", lat: 40.7, lng: -73.9 }, destination: { label: "Work", lat: 40.8, lng: -73.8 },
    summary: { eta_minutes: 20, transfers: 0, lines: ["Q"], reason: "fast", first_leg_arrival: { route_id: "Q", stop_name: "Canal", source_status: "live", walking_minutes: 2, catchable_arrival_minutes: 5, arrival_minutes: [5] } },
    route: [{ type: "SUBWAY", start_point: { latitude: 40.7, longitude: -73.9 }, end_point: { latitude: 40.8, longitude: -73.8 }, polyline: { encodedPolyline: "abc" }, train_line: "Q", line_color: "FCCC0A", direction: "North", departure_stop: "Canal", arrival_stop: "Atlantic", departure_coords: { latitude: 40.7, longitude: -73.9 }, arrival_coords: { latitude: 40.8, longitude: -73.8 }, minutes_until_train_arrives: 3, minutes_until_arrival: 20, route_total_minutes: 20, route_total_seconds: 1200, duration_minutes: 18, distance_meters: 4200, stop_count: 3, route_id: "Q", intermediate_stops: ["Canal", "Atlantic"], intermediate_stop_locations: [{ name: "Canal", lat: 40.72, lng: -74 }], segment_index: 0, departure_time_iso: "2026-07-25T14:00:00Z", arrival_time_iso: "2026-07-25T14:20:00Z" }],
    alerts: [{ header: "Service change", description: "Use the next train", routeIds: ["Q"], route_ids: ["Q"] }],
    itinerary: { itinerary_id: "itin", origin: { display_name: "Home", lat: 40.7, lng: -73.9 }, waypoints: [{ display_name: "Pickup", lat: 40.75, lng: -73.95, dwell_minutes: 5, dwell_source: "user" }], destination: { display_name: "Work", lat: 40.8, lng: -73.8 }, timezone: "America/New_York", planning_mode: "leave_now", requested_departure: null, requested_arrival: null, generated_at: "2026-07-25T14:00:00Z", data_basis: "mixed", data_freshness: "2026-07-25T14:00:00Z", departure_at: "2026-07-25T14:00:00Z", arrival_at: "2026-07-25T14:20:00Z", total_duration_seconds: 1500, total_walk_seconds: 300, total_wait_seconds: 60, total_in_vehicle_seconds: 540, total_dwell_seconds: 300, transfer_count: 0, legs: [firstLeg, secondLeg], segments: [{ segment_index: 0, origin: { display_name: "Home", lat: 40.7, lng: -73.9 }, destination: { display_name: "Pickup", lat: 40.75, lng: -73.95 }, legs: [firstLeg], duration_seconds: 600 }, { segment_index: 1, origin: { display_name: "Pickup", lat: 40.75, lng: -73.95 }, destination: { display_name: "Work", lat: 40.8, lng: -73.8 }, legs: [secondLeg], duration_seconds: 600 }], dwell_events: [{ event_type: "dwell", after_segment_index: 0, waypoint: { display_name: "Pickup", lat: 40.75, lng: -73.95, dwell_minutes: 5, dwell_source: "user" }, duration_seconds: 300, source: "user" }], structured_recommendation_reasons: [{ code: "fastest", difference_seconds: 120 }], selection_decision: { selected_candidate_index: 0, selected_candidate_id: "candidate", base_score: 20, final_score: 20, hard_constraints_satisfied: ["transit"], penalties: [{ source: "transfers", amount: 0, reason: "none" }], selection_reason: "lowest_final_score", evidence_ids: ["mta:1"] } },
    selection_decision: { selected_candidate_index: 0, selected_candidate_id: "candidate", base_score: 20, final_score: 20, hard_constraints_satisfied: ["transit"], penalties: [{ source: "transfers", amount: 0, reason: "none" }], selection_reason: "lowest_final_score", evidence_ids: ["mta:1"] },
  };
  const events = await collect(readerFromChunks([`event: route_card\ndata: ${JSON.stringify(payload)}\n\n`]));
  assert.deepEqual(events, [{ type: "route_card", ...payload }]);
});

test("keeps known cross-version fields while omitting unknown future data and later done", async () => {
  const payload = {
    card_id: "future", turn_id: "turn", role: "recommended",
    origin: { label: "Home", lat: 40.7, lng: -73.9 }, destination: { label: "Work", lat: 40.8, lng: -73.8 },
    summary: { eta_minutes: 10, transfers: 0, lines: ["Q"], reason: "fast" }, route: [{ type: "WALK", future_step_data: { version: 2 } }], alerts: [], future_card_data: { version: 2 },
  };
  const chunk = `event: route_card\ndata: ${JSON.stringify(payload)}\n\n`
    + 'event: done\ndata: {"session_id":"session","turn_id":"turn","stop_reason":"end_turn","usage":{}}\n\n';
  const events = await collect(readerFromChunks([chunk]));
  assert.deepEqual(events, [
    { type: "route_card", card_id: "future", turn_id: "turn", role: "recommended", origin: payload.origin, destination: payload.destination, summary: payload.summary, route: [{ type: "WALK" }], alerts: [] },
    { type: "done", session_id: "session", turn_id: "turn", stop_reason: "end_turn", usage: {} },
  ]);
});

test("rejects malformed nested cards and continues to the terminal event", async () => {
  const malformed = {
    card_id: "bad",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Home", lat: Number.NaN, lng: -73.9 },
    destination: { label: "Work", lat: 40.7, lng: -73.9 },
    summary: { eta_minutes: 12, transfers: 0, lines: [], reason: "fast" },
    route: [{ type: "WALK" }],
    alerts: [],
  };
  const chunk = `event: route_card\ndata: ${JSON.stringify(malformed)}\n\n`
    + 'event: done\ndata: {"session_id":"s1","turn_id":"t1","stop_reason":"end_turn","usage":{}}\n\n';
  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([chunk]));
    assert.equal(events.length, 1);
    assert.equal(events[0].type, "done");
    assert.ok(calls.length >= 1);
  });
});

test("parses the explicit terminal state on a clarification completion", async () => {
  const chunk =
    'event: done\ndata: {"session_id":"s1","turn_id":"t2","stop_reason":"clarification_required","terminal_state":"clarification_required","usage":{}}\n\n';

  const events = await collect(readerFromChunks([chunk]));

  assert.deepEqual(events, [
    {
      type: "done",
      session_id: "s1",
      turn_id: "t2",
      stop_reason: "clarification_required",
      terminal_state: "clarification_required",
      usage: {},
    },
  ]);
});

test("parses a grounded arrival_card event", async () => {
  const payload = {
    turn_id: "t2",
    route_id: "Q",
    stop: {
      id: "D28",
      name: "Newkirk Plaza",
      distance_meters: 320,
      latitude: 40.635,
      longitude: -73.962,
    },
    directions: [
      {
        id: "downtown",
        label: "Downtown / Brooklyn-bound",
        arrivals: [
          {
            expected_at: "2026-07-25T14:04:00Z",
            minutes: 4,
            realtime: true,
          },
        ],
      },
    ],
    updated_at: "2026-07-25T14:00:00Z",
    source_status: "live",
    resolution_status: "resolved",
    evidence: {
      source: "mta_gtfs_rt",
      observedAt: "2026-07-25T14:00:00Z",
      validUntil: "2026-07-25T14:02:00Z",
      status: "current",
      payload: { directions: [] },
    },
    catchability: {
      walking_minutes: 1,
      boarding_buffer_minutes: 2,
      arrival_minutes: [4],
      catchable_arrival_minutes: 4,
      confidence: 0.9,
    },
  };
  const chunk = `event: arrival_card\ndata: ${JSON.stringify(payload)}\n\n`;

  const events = await collect(readerFromChunks([chunk]));

  assert.deepEqual(events, [{ type: "arrival_card", ...payload }]);
});

test("rejects a malformed present optional itinerary and continues to done", async () => {
  const itinerary = {
    itinerary_id: "rc_abc123",
    total_duration_seconds: 5340,
    transfer_count: 1,
    arrival_at: "2026-07-16T10:49:00-04:00",
    departure_at: "2026-07-16T10:00:00-04:00",
    legs: [{ mode: "SUBWAY", ride_seconds: 1500 }],
  };
  const withItin = {
    card_id: "rc_abc123",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "Home", lat: 40.7, lng: -73.9 },
    destination: { label: "Costco", lat: 40.8, lng: -73.8 },
    summary: { eta_minutes: 89, transfers: 1, lines: ["A"], reason: "ok" },
    route: [{ type: "WALK" }],
    alerts: [],
    itinerary,
  };
  const malformedItinerary = {
    card_id: "rc_legacy",
    turn_id: "t1",
    role: "alternative",
    origin: { label: "Home", lat: 40.7, lng: -73.9 },
    destination: { label: "Costco", lat: 40.8, lng: -73.8 },
    summary: { eta_minutes: 40, transfers: 0, lines: ["D"], reason: "legacy" },
    route: [{ type: "WALK" }],
    alerts: [],
    itinerary: "not-an-object",
  };

  const chunk =
    `event: route_card\ndata: ${JSON.stringify(withItin)}\n\n` +
    `event: route_card\ndata: ${JSON.stringify(malformedItinerary)}\n\n` +
    'event: done\ndata: {"session_id":"s1","turn_id":"t1","stop_reason":"end_turn","usage":{}}\n\n';

  await silenceConsoleWarn(async () => {
    const events = await collect(readerFromChunks([chunk]));
    assert.equal(events.length, 2);
    assert.deepEqual(events[0].itinerary, itinerary);
    assert.equal(events[1].type, "done");
  });
});

test("mutation corpus rejects each malformed nested family without losing a later terminal event", async () => {
  const routeCard = {
    card_id: "card", turn_id: "turn", role: "recommended",
    origin: { label: "Home", lat: 40.7, lng: -73.9 },
    destination: { label: "Work", lat: 40.8, lng: -73.8 },
    summary: { eta_minutes: 20, transfers: 1, lines: ["Q"], reason: "fast" },
    route: [{ type: "SUBWAY", departure_coords: { latitude: 40.7, longitude: -73.9 }, arrival_coords: { lat: 40.8, lng: -73.8 }, intermediate_stop_locations: [{ name: "Canal", lat: 40.72, lng: -74 }] }],
    alerts: [{ header: "Service change", route_ids: ["Q"] }],
    itinerary: { itinerary_id: "itin", total_duration_seconds: 1200, transfer_count: 0, legs: [{ mode: "SUBWAY", ride_seconds: 1000, stops: [{ name: "Canal", lat: 40.72, lng: -74 }] }] },
    selection_decision: { selected_candidate_index: 0, selected_candidate_id: "candidate", base_score: 20, final_score: 20, hard_constraints_satisfied: ["transit"], penalties: [], selection_reason: "lowest_final_score", evidence_ids: [] },
  };
  const arrival = {
    turn_id: "turn", route_id: "Q", stop: { id: "Q01", latitude: 40.7, longitude: -73.9 }, directions: [{ id: "north", label: "Northbound", arrivals: [{ expected_at: "2026-07-25T14:00:00Z", minutes: 3, realtime: true }] }], updated_at: "2026-07-25T14:00:00Z", source_status: "live", resolution_status: "resolved",
  };
  const mutations = [
    { ...routeCard, origin: { ...routeCard.origin, lat: 99 } },
    { ...routeCard, summary: { ...routeCard.summary, eta_minutes: -1 } },
    { ...routeCard, route: [{ ...routeCard.route[0], departure_coords: { latitude: 40.7 } }] },
    { ...routeCard, route: [{ ...routeCard.route[0], departure_coords: { latitude: 40.7, lng: -73.9 } }] },
    { ...routeCard, route: [{ ...routeCard.route[0], departure_coords: { latitude: 40.7, longitude: -73.9, lat: 40.7, lng: -73.9 } }] },
    { ...routeCard, route: [{ ...routeCard.route[0], duration_minutes: -1 }] },
    { ...routeCard, route: [{ ...routeCard.route[0], distance_meters: 1_000_001 }] },
    { ...routeCard, route: [{ ...routeCard.route[0], intermediate_stop_locations: [{ name: "", lat: 40.7, lng: -73.9 }] }] },
    { ...routeCard, alerts: [{ header: "" }] },
    { ...routeCard, itinerary: "malformed" },
    { ...routeCard, itinerary: { ...routeCard.itinerary, legs: [{ mode: "", ride_seconds: 2 }] } },
    { ...routeCard, selection_decision: null },
    { ...routeCard, selection_decision: { ...routeCard.selection_decision, selected_candidate_index: -1 } },
    { ...routeCard, selection_decision: { ...routeCard.selection_decision, penalties: [{ source: "x", amount: "bad", reason: "x" }] } },
    { ...arrival, source_status: "invented" },
    { ...arrival, stop: { latitude: 99, longitude: -73.9 } },
    { ...arrival, directions: [{ id: "north", label: "Northbound", arrivals: [{ expected_at: "", minutes: 3, realtime: true }] }] },
    { ...arrival, ambiguity: [{}] },
    { ...arrival, catchability: { walking_minutes: 1, boarding_buffer_minutes: 1, confidence: 2, arrival_minutes: [] } },
    { ...arrival, evidence: { source: "mta", observedAt: "2026-07-25T14:00:00Z", status: "current", payload: { directions: Array.from({ length: 33 }, () => ({ id: "north", label: "Northbound", arrivals: [] })) } } },
  ];
  const frames = mutations.map((payload) => `event: ${"card_id" in payload ? "route_card" : "arrival_card"}\ndata: ${JSON.stringify(payload)}\n\n`).join("")
    + 'event: done\ndata: {"session_id":"s1","turn_id":"turn","stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":2}}\n\n';
  await silenceConsoleWarn(async (calls) => {
    const events = await collect(readerFromChunks([frames]));
    assert.deepEqual(events, [{ type: "done", session_id: "s1", turn_id: "turn", stop_reason: "end_turn", usage: { input_tokens: 1, output_tokens: 2 } }]);
    assert.equal(calls.length, mutations.length);
  });
});

test("flushes a trailing frame with no terminating blank line once the stream closes", async () => {
  const chunk = 'event: token\ndata: {"text":"trailing"}\n\n' + 'event: token\ndata: {"text":"no trailer"}';

  const events = await collect(readerFromChunks([chunk]));

  assert.deepEqual(events, [
    { type: "token", text: "trailing" },
    { type: "token", text: "no trailer" },
  ]);
});
