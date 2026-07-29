import assert from "node:assert/strict";
import test from "node:test";

import {
  applyAgentEvent,
  buildAgentChatRequest,
  persistSessionId,
  readPersistedSessionId,
  runTurn,
  sessionStorageKey,
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

test("replayed tool and route-card events are deduplicated by stable id", () => {
  let state = applyAgentEvent(initialState(), {
    type: "turn_started",
    text: "Plan a trip",
  });
  const toolStart = {
    type: "tool_start",
    tool_call_id: "tool-1",
    tool: "plan_trip",
    label: "Comparing routes",
  };
  const routeCard = {
    type: "route_card",
    card_id: "route-1",
    turn_id: "turn-1",
    role: "recommended",
    origin: { label: "Your location", lat: 40.7, lng: -73.9 },
    destination: { label: "Coney Island", lat: 40.57, lng: -73.98 },
    summary: {
      eta_minutes: 30,
      transfers: 0,
      lines: ["Q"],
      reason: "Direct",
    },
    route: [],
    alerts: [],
  };

  state = applyAgentEvent(state, toolStart);
  state = applyAgentEvent(state, toolStart);
  state = applyAgentEvent(state, routeCard);
  state = applyAgentEvent(state, routeCard);

  assert.equal(state.messages[1].toolChips.length, 1);
  assert.equal(state.messages[1].routeCards.length, 1);
});

test("a recovered route attempt replaces the prior failed chip", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to Costco" });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "address-attempt",
    tool: "plan_trip",
    label: "Planning a route without busesâ€¦",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "address-attempt",
    tool: "plan_trip",
    ok: false,
    duration_ms: 120,
  });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "coordinate-recovery",
    tool: "plan_trip",
    label: "Planning a route without busesâ€¦",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "coordinate-recovery",
    tool: "plan_trip",
    ok: true,
    duration_ms: 280,
  });

  const chips = state.messages[1].toolChips;
  assert.equal(chips.length, 1);
  assert.equal(chips[0].id, "coordinate-recovery");
  assert.equal(chips[0].status, "ok");
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

test("session id persistence is versioned and namespaced by backend environment", () => {
  const namespace = "http://localhost:3000|development";
  const storage = {
    store: {},
    getItem(key) { return this.store[key] ?? null; },
    setItem(key, value) { this.store[key] = value; },
    removeItem(key) { delete this.store[key]; },
  };

  persistSessionId(storage, "sess-42", namespace);

  assert.equal(readPersistedSessionId(storage, namespace), "sess-42");
  assert.equal(readPersistedSessionId(storage, "https://smartroute.app|production"), null);
  assert.equal(readPersistedSessionId(undefined), null);
  assert.match(sessionStorageKey(namespace), /development/);
});

test("legacy and incompatible session records are discarded", () => {
  const namespace = "http://localhost:3000|development";
  const key = sessionStorageKey(namespace);
  const removed = [];
  const storage = {
    store: {
      "sr-agent-session": "legacy-bare-session",
      [key]: JSON.stringify({ version: 999, namespace, sessionId: "stale" }),
    },
    getItem(name) { return this.store[name] ?? null; },
    setItem(name, value) { this.store[name] = value; },
    removeItem(name) { removed.push(name); delete this.store[name]; },
  };

  assert.equal(readPersistedSessionId(storage, namespace), null);
  assert.deepEqual(removed, ["sr-agent-session", key]);
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

test("session id persistence writes a record, clears null, and tolerates throwing storage", () => {
  const namespace = "http://localhost:3000|development";
  const writes = [];
  const removals = [];
  const storage = {
    getItem() { return null; },
    setItem(key, value) { writes.push([key, value]); },
    removeItem(key) { removals.push(key); },
  };
  persistSessionId(storage, "sess-1", namespace);
  persistSessionId(storage, null, namespace);
  assert.equal(writes.length, 1);
  assert.deepEqual(JSON.parse(writes[0][1]), {
    version: 2,
    namespace,
    sessionId: "sess-1",
  });
  assert.deepEqual(removals, [
    "sr-agent-session",
    sessionStorageKey(namespace),
    "sr-agent-session",
  ]);

  const throwing = {
    getItem() { return null; },
    setItem() { throw new Error("quota exceeded"); },
    removeItem() { throw new Error("blocked"); },
  };
  assert.doesNotThrow(() => persistSessionId(throwing, "sess-1", namespace));
  assert.doesNotThrow(() => persistSessionId(throwing, null, namespace));
});

test("a typed provider request failure stops cleanly without creating a route card", () => {
  let state = applyAgentEvent(initialState(), {
    type: "turn_started",
    text: "Plan a trip",
  });
  state = applyAgentEvent(state, {
    type: "error",
    code: "invalid_request",
    message: "SmartRoute could not complete that request. Please try again.",
    retryable: false,
  });
  state = applyAgentEvent(state, {
    type: "done",
    session_id: "sess-1",
    turn_id: "t1",
    stop_reason: "error",
    usage: {},
  });

  const assistant = state.messages.at(-1);
  assert.equal(state.isStreaming, false);
  assert.equal(assistant.isStreaming, false);
  assert.equal(assistant.stopReason, "error");
  assert.equal(assistant.routeCards.length, 0);
  assert.equal(assistant.error.code, "invalid_request");
  assert.equal(assistant.error.retryable, false);
});

test("arrival_card attaches production arrival evidence to the streaming assistant turn", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "Next Q at Newkirk Avenue?" });
  state = applyAgentEvent(state, {
    type: "arrival_card",
    turn_id: "t1",
    route_id: "Q",
    stop: {
      id: "D28",
      name: "Newkirk Plaza",
      distance_meters: 322,
      latitude: 40.635,
      longitude: -73.962,
    },
    directions: [
      {
        id: "downtown",
        label: "Downtown / Brooklyn-bound",
        arrivals: [
          { expected_at: "2026-07-25T14:00:00Z", minutes: 0, realtime: true },
          { expected_at: "2026-07-25T14:04:00Z", minutes: 4, realtime: true },
          { expected_at: "2026-07-25T14:11:00Z", minutes: 11, realtime: true },
        ],
      },
    ],
    updated_at: "2026-07-25T14:00:00Z",
    source_status: "live",
    catchability: {
      walking_minutes: 1,
      boarding_buffer_minutes: 2,
      arrival_minutes: [4, 11],
      catchable_arrival_minutes: 4,
      confidence: 0.9,
    },
  });

  const turn = state.messages[1];
  assert.equal(turn.role, "assistant");
  assert.equal(turn.local, undefined);
  assert.deepEqual(turn.arrivals.groups, [
    { direction: "downtown", label: "Downtown / Brooklyn-bound", minutes: [4, 11] },
  ]);
  assert.equal(turn.arrivals.sourceStatus, "live");
  assert.deepEqual(turn.arrivals.stationCoordinates, { lat: 40.635, lng: -73.962 });
  assert.match(turn.arrivals.stationGuidance, /0\.2 mi away/);
});

test("arrival clarification suppresses generic errors and accepts only one terminal event", () => {
  let state = applyAgentEvent(initialState(), {
    type: "turn_started",
    text: "When does the next Q arrive?",
  });
  state = applyAgentEvent(state, {
    type: "meta",
    session_id: "sess-1",
    turn_id: "t2",
  });
  state = applyAgentEvent(state, {
    type: "arrival_card",
    turn_id: "t2",
    route_id: "Q",
    stop: { id: "", name: "Transit stop" },
    directions: [],
    updated_at: "2026-07-25T14:00:00Z",
    source_status: "stop_not_resolved",
    ambiguity: [{ stop_id: "D28", stop_name: "Church Av" }],
  });
  state = applyAgentEvent(state, {
    type: "error",
    code: "upstream_error",
    message: "SmartRoute could not complete that request.",
    retryable: true,
  });
  state = applyAgentEvent(state, {
    type: "done",
    session_id: "sess-1",
    turn_id: "t2",
    stop_reason: "clarification_required",
    usage: {},
  });
  state = applyAgentEvent(state, {
    type: "done",
    session_id: "sess-1",
    turn_id: "t2",
    stop_reason: "error",
    usage: {},
  });

  const assistant = state.messages.at(-1);
  assert.equal(state.error, null);
  assert.equal(assistant.error, undefined);
  assert.equal(assistant.stopReason, "clarification_required");
  assert.equal(assistant.isStreaming, false);
});

test("a stale fresh-load session is recreated once without redispatching the failed attempt", async () => {
  const attempts = [];
  const actions = [];
  let discarded = 0;
  const transport = async function* (request) {
    attempts.push(request);
    if (attempts.length === 1) {
      yield { type: "meta", session_id: "stale", turn_id: "t0" };
      yield {
        type: "error",
        code: "session_expired",
        message: "expired",
        retryable: true,
      };
      yield {
        type: "done",
        session_id: "stale",
        turn_id: "t0",
        stop_reason: "error",
        usage: {},
      };
      return;
    }
    yield { type: "meta", session_id: "fresh", turn_id: "t1" };
    yield { type: "token", text: "Your route is ready." };
    yield {
      type: "done",
      session_id: "fresh",
      turn_id: "t1",
      stop_reason: "end_turn",
      usage: {},
    };
  };
  const controller = new AbortController();
  const inFlightRef = { current: true };
  const abortControllerRef = { current: controller };
  const request = {
    session_id: "stale",
    message: "Plan my trip",
    response_presentation: "auto",
  };

  await runTurn(
    transport,
    request,
    controller,
    (action) => actions.push(action),
    inFlightRef,
    abortControllerRef,
    { canRecoverSession: true, discardSession: () => { discarded += 1; } },
  );

  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].message, attempts[1].message);
  assert.equal(attempts[1].session_id, undefined);
  assert.equal(discarded, 1);
  assert.deepEqual(actions.map((action) => action.type), ["meta", "token", "done"]);
  assert.equal(actions.filter((action) => action.type === "done").length, 1);
});

test("session recreation is capped at one attempt and visible history is not discarded", async () => {
  async function run(canRecoverSession) {
    let attempts = 0;
    let discarded = 0;
    const actions = [];
    const transport = async function* () {
      attempts += 1;
      yield { type: "meta", session_id: "stale", turn_id: "t0" };
      yield {
        type: "error",
        code: "session_expired",
        message: "expired",
        retryable: true,
      };
      yield {
        type: "done",
        session_id: "stale",
        turn_id: "t0",
        stop_reason: "error",
        usage: {},
      };
    };
    const controller = new AbortController();
    await runTurn(
      transport,
      {
        session_id: "stale",
        message: "Keep this message",
        response_presentation: "auto",
      },
      controller,
      (action) => actions.push(action),
      { current: true },
      { current: controller },
      {
        canRecoverSession,
        discardSession: () => { discarded += 1; },
      },
    );
    return { attempts, discarded, actions };
  }

  const fresh = await run(true);
  assert.equal(fresh.attempts, 2);
  assert.equal(fresh.discarded, 1);
  assert.equal(fresh.actions.filter((action) => action.type === "error").length, 1);

  const visible = await run(false);
  assert.equal(visible.attempts, 1);
  assert.equal(visible.discarded, 0);
  assert.equal(visible.actions.find((action) => action.type === "error").code, "session_expired");
});

test("a failed replacement request produces one terminal connection error", async () => {
  let attempts = 0;
  const actions = [];
  const transport = async function* () {
    attempts += 1;
    if (attempts === 1) {
      yield { type: "meta", session_id: "stale", turn_id: "t0" };
      yield {
        type: "error",
        code: "session_expired",
        message: "expired",
        retryable: true,
      };
      yield {
        type: "done",
        session_id: "stale",
        turn_id: "t0",
        stop_reason: "error",
        usage: {},
      };
      return;
    }
    throw new Error("replacement failed");
  };
  const controller = new AbortController();

  await runTurn(
    transport,
    {
      session_id: "stale",
      message: "Preserve me",
      response_presentation: "auto",
    },
    controller,
    (action) => actions.push(action),
    { current: true },
    { current: controller },
    { canRecoverSession: true, discardSession: () => undefined },
  );

  assert.equal(attempts, 2);
  assert.deepEqual(actions, [
    { type: "stream_error", message: "replacement failed" },
  ]);
});

test("chat requests carry response presentation without changing route inputs", () => {
  const shared = {
    sessionId: "sess-42",
    message: "  Take me to Costco  ",
    origin: { lat: 40.65, lng: -74.01 },
    selectedCardId: "rc-primary",
  };
  const automatic = buildAgentChatRequest(shared);
  const quick = buildAgentChatRequest({
    ...shared,
    responsePresentation: "quick",
  });

  assert.equal(automatic.response_presentation, "auto");
  assert.equal(quick.response_presentation, "quick");
  assert.deepEqual(
    { ...quick, response_presentation: "auto" },
    automatic,
  );
  assert.equal(quick.message, "Take me to Costco");
});
