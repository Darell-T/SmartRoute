import assert from "node:assert/strict";
import test from "node:test";

import {
  applyAgentEvent,
  persistSessionId,
  readPersistedSessionId,
} from "./use-agent-chat.ts";

function initialState(overrides = {}) {
  return { messages: [], sessionId: null, isStreaming: false, error: null, ...overrides };
}

test("turn_started appends a user turn and a streaming assistant placeholder", () => {
  const state = applyAgentEvent(initialState(), { type: "turn_started", text: "heading to Costco" });

  assert.equal(state.messages.length, 2);
  assert.deepEqual(state.messages[0], { role: "user", text: "heading to Costco" });
  assert.equal(state.messages[1].role, "assistant");
  assert.equal(state.messages[1].isStreaming, true);
  assert.equal(state.isStreaming, true);
  assert.equal(state.error, null);
});

test("turn_started clears a previous turn's error", () => {
  const state = applyAgentEvent(initialState({ error: "old error" }), {
    type: "turn_started",
    text: "hi",
  });
  assert.equal(state.error, null);
});

test("chat_reset clears the active conversation and session", () => {
  const state = applyAgentEvent(
    initialState({
      messages: [{ role: "user", text: "old trip" }],
      sessionId: "sess-old",
      isStreaming: true,
      error: "old error",
    }),
    { type: "chat_reset" },
  );

  assert.deepEqual(state, {
    messages: [],
    sessionId: null,
    isStreaming: false,
    error: null,
  });
});

test("a full happy-path event sequence assembles the expected final turn state", () => {
  let state = initialState();
  const events = [
    { type: "turn_started", text: "heading to Costco, no bus" },
    { type: "meta", session_id: "sess-1", turn_id: "turn-1" },
    { type: "token", text: "Here's " },
    { type: "token", text: "your route." },
    { type: "tool_start", tool_call_id: "c1", tool: "plan_trip", label: "Finding routes to Costco…" },
    { type: "tool_end", tool_call_id: "c1", tool: "plan_trip", ok: true, duration_ms: 812, summary: "3 candidates" },
    {
      type: "route_card",
      card_id: "rc_1",
      turn_id: "turn-1",
      role: "recommended",
      origin: { label: "Home", lat: 40.7, lng: -73.9 },
      destination: { label: "Costco", lat: 40.8, lng: -73.8 },
      summary: { eta_minutes: 22, transfers: 1, lines: ["A"], reason: "Fastest, avoids buses" },
      route: [],
      alerts: [],
    },
    { type: "done", session_id: "sess-1", turn_id: "turn-1", stop_reason: "end_turn", usage: { input_tokens: 10, output_tokens: 5 } },
  ];

  for (const event of events) state = applyAgentEvent(state, event);

  assert.equal(state.sessionId, "sess-1");
  assert.equal(state.isStreaming, false);

  const assistantTurn = state.messages[1];
  assert.equal(assistantTurn.role, "assistant");
  assert.equal(assistantTurn.turnId, "turn-1");
  assert.equal(assistantTurn.text, "Here's your route.");
  assert.equal(assistantTurn.isStreaming, false);
  assert.equal(assistantTurn.stopReason, "end_turn");

  assert.equal(assistantTurn.toolChips.length, 1);
  assert.deepEqual(assistantTurn.toolChips[0], {
    id: "c1",
    tool: "plan_trip",
    label: "Finding routes to Costco…",
    status: "ok",
    durationMs: 812,
    summary: "3 candidates",
  });

  assert.equal(assistantTurn.routeCards.length, 1);
  assert.equal(assistantTurn.routeCards[0].card_id, "rc_1");
  assert.equal(assistantTurn.routeCards[0].role, "recommended");
});

test("tool_end for an unknown tool_call_id leaves existing chips untouched", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "hi" });
  state = applyAgentEvent(state, { type: "tool_start", tool_call_id: "c1", tool: "plan_trip", label: "Finding routes…" });
  state = applyAgentEvent(state, { type: "tool_end", tool_call_id: "does-not-exist", tool: "plan_trip", ok: true, duration_ms: 5 });

  assert.equal(state.messages[1].toolChips[0].status, "running");
});

test("a mid-stream error event sets both the turn error and the top-level error, without a done", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "drive me to Boston" });
  state = applyAgentEvent(state, { type: "meta", session_id: "s1", turn_id: "t1" });
  state = applyAgentEvent(state, {
    type: "error",
    code: "internal",
    message: "Something went wrong.",
    retryable: true,
  });

  assert.equal(state.error, "Something went wrong.");
  assert.deepEqual(state.messages[1].error, {
    code: "internal",
    message: "Something went wrong.",
    retryable: true,
  });
  // error events are not required to finalize the turn by themselves —
  // the backend always follows with `done`.
  assert.equal(state.messages[1].isStreaming, true);

  state = applyAgentEvent(state, {
    type: "done",
    session_id: "s1",
    turn_id: "t1",
    stop_reason: "error",
    usage: { input_tokens: 1, output_tokens: 0 },
  });
  assert.equal(state.messages[1].isStreaming, false);
  assert.equal(state.messages[1].stopReason, "error");
});

test("stream_cancelled finalizes the turn as cancelled without setting a top-level error", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "hi" });
  state = applyAgentEvent(state, { type: "stream_cancelled" });

  assert.equal(state.isStreaming, false);
  assert.equal(state.error, null);
  assert.equal(state.messages[1].isStreaming, false);
  assert.equal(state.messages[1].stopReason, "cancelled");
  assert.equal(state.messages[1].error, undefined);
});

test("stream_error (dropped connection, no done) finalizes the turn with an error chip", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "hi" });
  state = applyAgentEvent(state, {
    type: "stream_error",
    message: "The connection to SmartRoute dropped before it finished responding.",
  });

  assert.equal(state.isStreaming, false);
  assert.equal(state.error, "The connection to SmartRoute dropped before it finished responding.");
  assert.equal(state.messages[1].isStreaming, false);
  assert.equal(state.messages[1].stopReason, "dropped");
  assert.equal(state.messages[1].error.message, "The connection to SmartRoute dropped before it finished responding.");
});

test("stream_error does not overwrite an error event's more specific message on the turn", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "hi" });
  state = applyAgentEvent(state, {
    type: "error",
    code: "budget_exceeded",
    message: "Daily budget reached.",
    retryable: false,
  });
  state = applyAgentEvent(state, { type: "stream_error", message: "connection dropped" });

  // The turn's own `error` field keeps the server's specific reason; only
  // the top-level banner reflects the fact that the stream also dropped.
  assert.equal(state.messages[1].error.message, "Daily budget reached.");
  assert.equal(state.error, "connection dropped");
});

test("events after turn_started with no assistant turn present are dropped, not crashing", () => {
  // Defensive case: a token event arriving before any turn_started (should
  // never happen given the backend always sends meta first into a turn we
  // started) must not throw.
  const state = applyAgentEvent(initialState(), { type: "token", text: "orphan" });
  assert.deepEqual(state.messages, []);
});

test("session id persistence: reads a stored id and treats missing storage as no session", () => {
  const storage = { store: { "sr-agent-session": "sess-42" }, getItem(key) { return this.store[key] ?? null; }, setItem(key, value) { this.store[key] = value; } };
  assert.equal(readPersistedSessionId(storage), "sess-42");
  assert.equal(readPersistedSessionId(undefined), null);

  const empty = { store: {}, getItem(key) { return this.store[key] ?? null; }, setItem(key, value) { this.store[key] = value; } };
  assert.equal(readPersistedSessionId(empty), null);
});

test("local_turn_appended appends a display-only assistant turn without touching isStreaming", () => {
  let state = initialState();
  state = applyAgentEvent(state, {
    type: "local_turn_appended",
    turnId: "local-1",
    text: "Next A trains near you:",
    arrivals: {
      routeId: "A",
      stationName: "125 St",
      groups: [
        { direction: "uptown", label: "Uptown", minutes: [2, 7, 12] },
        { direction: "downtown", label: "Downtown", minutes: [4, 9] },
      ],
    },
  });

  assert.equal(state.messages.length, 1);
  const turn = state.messages[0];
  assert.equal(turn.role, "assistant");
  assert.equal(turn.local, true);
  assert.equal(turn.isStreaming, false);
  assert.equal(turn.text, "Next A trains near you:");
  assert.deepEqual(turn.arrivals, {
    routeId: "A",
    stationName: "125 St",
    groups: [
      { direction: "uptown", label: "Uptown", minutes: [2, 7, 12] },
      { direction: "downtown", label: "Downtown", minutes: [4, 9] },
    ],
  });
  // A local turn must never flip the hook's top-level streaming/error state
  // — it has no network request behind it.
  assert.equal(state.isStreaming, false);
  assert.equal(state.error, null);
});

test("local_turn_appended after an in-progress streaming turn leaves that turn untouched", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "heading to Costco" });
  state = applyAgentEvent(state, {
    type: "local_turn_appended",
    turnId: "local-1",
    text: "Next A trains near you:",
    arrivals: { routeId: "A", stationName: "125 St", groups: [] },
  });

  assert.equal(state.messages.length, 3);
  assert.equal(state.messages[1].isStreaming, true); // the real streaming turn, untouched
  assert.equal(state.messages[1].local, undefined);
  assert.equal(state.messages[2].local, true);
  // The top-level isStreaming flag still reflects the real (non-local) turn.
  assert.equal(state.isStreaming, true);
});

test("session id persistence: writes only a non-null id, and tolerates a throwing storage", () => {
  const writes = [];
  const storage = {
    getItem() { return null; },
    setItem(key, value) { writes.push([key, value]); },
  };
  persistSessionId(storage, "sess-1");
  persistSessionId(storage, null);
  assert.deepEqual(writes, [["sr-agent-session", "sess-1"]]);

  const throwing = {
    getItem() { return null; },
    setItem() { throw new Error("quota exceeded"); },
  };
  assert.doesNotThrow(() => persistSessionId(throwing, "sess-1"));
});
