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

test("parses optional itinerary on route_card and ignores non-object itinerary", async () => {
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
  const withoutItin = {
    card_id: "rc_legacy",
    turn_id: "t1",
    role: "alternative",
    origin: { label: "Home", lat: 40.7, lng: -73.9 },
    destination: { label: "Costco", lat: 40.8, lng: -73.8 },
    summary: { eta_minutes: 40, transfers: 0, lines: ["D"], reason: "legacy" },
    route: [{ type: "WALK" }],
    alerts: [],
    // Non-object must not be copied onto the card.
    itinerary: "not-an-object",
  };

  const chunk =
    `event: route_card\ndata: ${JSON.stringify(withItin)}\n\n` +
    `event: route_card\ndata: ${JSON.stringify(withoutItin)}\n\n`;

  const events = await collect(readerFromChunks([chunk]));

  assert.equal(events.length, 2);
  assert.deepEqual(events[0].itinerary, itinerary);
  assert.equal("itinerary" in events[1], false);
});

test("flushes a trailing frame with no terminating blank line once the stream closes", async () => {
  const chunk = 'event: token\ndata: {"text":"trailing"}\n\n' + 'event: token\ndata: {"text":"no trailer"}';

  const events = await collect(readerFromChunks([chunk]));

  assert.deepEqual(events, [
    { type: "token", text: "trailing" },
    { type: "token", text: "no trailer" },
  ]);
});
