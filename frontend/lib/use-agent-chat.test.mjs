import assert from "node:assert/strict";
import test from "node:test";

import {
  applyAgentEvent,
  buildAgentChatRequest,
  buildTurnsFromSnapshot,
  fetchSessionSnapshot,
  persistSessionId,
  readPersistedSessionId,
  runTurn,
  sessionStorageKey,
} from "./use-agent-chat.ts";
import {
  isRoutePreparationTool,
  isRouteResultTool,
  isRouteWorkflowTool,
  isSearchActivityTool,
} from "./agent-route-tools.ts";

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

test("session_restarted preserves visible history and marks the replayed turn", () => {
  let state = applyAgentEvent(initialState(), {
    type: "turn_started",
    text: "How is the Q?",
  });

  state = applyAgentEvent(state, { type: "session_discarded" });
  state = applyAgentEvent(state, {
    type: "session_restarted",
    message: "Earlier context expired, so this request is starting a fresh session.",
  });

  assert.equal(state.sessionId, null);
  assert.equal(state.messages[0].text, "How is the Q?");
  assert.equal(
    state.messages[1].notice,
    "Earlier context expired, so this request is starting a fresh session.",
  );
});

test("a full happy-path event sequence assembles the expected final turn state", () => {
  let state = initialState();
  const events = [
    { type: "turn_started", text: "heading to Costco, no bus" },
    { type: "meta", session_id: "sess-1", turn_id: "turn-1" },
    { type: "reasoning", text: "I should compare the verified candidates." },
    { type: "token", text: "Here's " },
    { type: "token", text: "your route." },
    { type: "reasoning", text: "The route facts support one option." },
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
  assert.equal(
    assistantTurn.reasoning,
    "I should compare the verified candidates.\nThe route facts support one option.",
  );
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

test("transit-status action is carried by its typed event and ignores stale turns", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "Are any trains delayed?" });
  state = applyAgentEvent(state, { type: "meta", session_id: "sess-1", turn_id: "turn-1" });
  state = applyAgentEvent(state, {
    type: "transit_status_action",
    turn_id: "old-turn",
    action: "view_alerts",
  });
  assert.equal(state.messages[1].transitStatusAction, undefined);

  state = applyAgentEvent(state, {
    type: "transit_status_action",
    turn_id: "turn-1",
    action: "view_alerts",
  });
  assert.equal(state.messages[1].transitStatusAction, "view_alerts");

  state = applyAgentEvent(state, { type: "turn_retry_started" });
  assert.equal(state.messages[1].transitStatusAction, undefined);
});

test("sources attach only to the active assistant turn and deduplicate by URL", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "Pizza nearby" });
  state = applyAgentEvent(state, { type: "meta", session_id: "sess-1", turn_id: "turn-1" });
  state = applyAgentEvent(state, {
    type: "sources",
    sources: [{ title: "Damn Lines", url: "https://damnlines.com/camera/l-industrie" }],
  });
  state = applyAgentEvent(state, {
    type: "sources",
    sources: [{ title: "Damn Lines", url: "https://damnlines.com/camera/l-industrie" }],
  });

  assert.deepEqual(state.messages[1].sources, [
    { title: "Damn Lines", url: "https://damnlines.com/camera/l-industrie" },
  ]);

  state = applyAgentEvent(state, {
    type: "done",
    session_id: "sess-1",
    turn_id: "turn-1",
    stop_reason: "end_turn",
    usage: {},
  });
  const completed = state;
  state = applyAgentEvent(state, {
    type: "sources",
    sources: [{ title: "Damn Lines", url: "https://damnlines.com/camera/late-event" }],
  });
  assert.deepEqual(state, completed);

  state = applyAgentEvent(state, { type: "turn_started", text: "Another place" });
  assert.equal(state.messages[3].sources, undefined);
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

test("a deadline error remains visible after the terminal done event", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "Can I get the directions?" });
  state = applyAgentEvent(state, { type: "meta", session_id: "s1", turn_id: "t1" });
  state = applyAgentEvent(state, {
    type: "error",
    code: "deadline",
    message: "The response took too long. Please try again.",
    retryable: true,
  });
  state = applyAgentEvent(state, {
    type: "done",
    session_id: "s1",
    turn_id: "t1",
    stop_reason: "deadline",
    terminal_state: "failed",
    usage: {},
  });

  assert.equal(state.isStreaming, false);
  assert.equal(state.messages[1].stopReason, "deadline");
  assert.deepEqual(state.messages[1].error, {
    code: "deadline",
    message: "The response took too long. Please try again.",
    retryable: true,
  });
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

test("manual retry reuses the failed turn instead of duplicating the user message", () => {
  let state = applyAgentEvent(initialState(), {
    type: "turn_started",
    text: "Plan a trip to Coney Island",
  });
  state = applyAgentEvent(state, {
    type: "stream_error",
    message: "SmartRoute is temporarily unavailable.",
    code: "transport_503",
    retryable: true,
    correlationId: "request-123",
  });
  state = applyAgentEvent(state, { type: "turn_retry_started" });

  assert.equal(state.messages.length, 2);
  assert.equal(state.messages[0].role, "user");
  assert.equal(state.messages[0].text, "Plan a trip to Coney Island");
  assert.equal(state.messages[1].role, "assistant");
  assert.equal(state.messages[1].isStreaming, true);
  assert.equal(state.messages[1].error, undefined);
  assert.equal(state.error, null);
});

test("semantic progress replaces the current stage, permits chained-leg cycles, and ignores late events", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to JFK" });
  state = applyAgentEvent(state, { type: "meta", session_id: "s1", turn_id: "t1" });
  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "active" });
  assert.deepEqual(state.messages[1].progress, { stage: "finding_routes", status: "active" });

  state = applyAgentEvent(state, { type: "progress", stage: "checking_live_conditions", status: "active" });
  assert.deepEqual(state.messages[1].progress, { stage: "checking_live_conditions", status: "active" });
  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "complete" });
  assert.deepEqual(state.messages[1].progress, { stage: "checking_live_conditions", status: "active" });

  state = applyAgentEvent(state, { type: "progress", stage: "checking_live_conditions", status: "complete" });
  assert.equal(state.messages[1].progress, undefined);

  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "active" });
  assert.deepEqual(state.messages[1].progress, { stage: "finding_routes", status: "active" });
  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "complete" });
  state = applyAgentEvent(state, { type: "progress", stage: "comparing_options", status: "active" });
  assert.deepEqual(state.messages[1].progress, { stage: "comparing_options", status: "active" });
  state = applyAgentEvent(state, { type: "progress", stage: "comparing_options", status: "complete" });
  assert.equal(state.messages[1].progress, undefined);

  state = applyAgentEvent(state, { type: "done", session_id: "s1", turn_id: "t1", stop_reason: "end_turn", usage: {} });
  state = applyAgentEvent(state, { type: "progress", stage: "comparing_options", status: "active" });
  assert.equal(state.messages[1].progress, undefined);
});

test("terminal route results clear a working stage without changing the route contract", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to Coney Island" });
  state = applyAgentEvent(state, { type: "progress", stage: "comparing_options", status: "active" });
  state = applyAgentEvent(state, {
    type: "route_card",
    card_id: "rc-progress",
    turn_id: "turn-progress",
    role: "recommended",
    origin: { label: "Home", lat: 40.7, lng: -73.9 },
    destination: { label: "Coney Island", lat: 40.57, lng: -73.98 },
    summary: { eta_minutes: 30, transfers: 0, lines: ["Q"], reason: "Direct" },
    route: [],
    alerts: [],
  });
  assert.equal(state.messages[1].progress, undefined);
  assert.equal(state.messages[1].routeCards[0].card_id, "rc-progress");
});

test("failed route tool clears semantic progress so a retry cannot inherit stale work", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to JFK" });
  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "active" });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "failed-trip",
    tool: "plan_trip",
    label: "Finding routes",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "failed-trip",
    tool: "plan_trip",
    ok: false,
    duration_ms: 100,
  });
  assert.equal(state.messages[1].progress, undefined);
});

test("canonical and legacy route tools classify for searching UI and route results", () => {
  // prepare_route_options drives route-search/searching but is not itself a
  // completed route result.
  assert.equal(isRoutePreparationTool("prepare_route_options"), true);
  assert.equal(isRouteResultTool("prepare_route_options"), false);
  assert.equal(isRouteWorkflowTool("prepare_route_options"), true);

  // present_route is a completed route result but not preparation/searching.
  assert.equal(isRoutePreparationTool("present_route"), false);
  assert.equal(isRouteResultTool("present_route"), true);
  assert.equal(isRouteWorkflowTool("present_route"), true);

  // plan_trip stays both preparation and route-result compatibility.
  assert.equal(isRoutePreparationTool("plan_trip"), true);
  assert.equal(isRouteResultTool("plan_trip"), true);
  assert.equal(isRouteWorkflowTool("plan_trip"), true);

  // Unrelated tools are none of the above.
  assert.equal(isRoutePreparationTool("search_local_places"), false);
  assert.equal(isRouteResultTool("search_local_places"), false);
  assert.equal(isRouteWorkflowTool("search_local_places"), false);
});

test("searching UI activates only for real retrieval capability tools", () => {
  for (const tool of [
    "search_local_places",
    "prepare_route_options",
    "transit_snapshot",
    "lookup_arrivals",
    "event_lookup",
    "web_search",
  ]) {
    assert.equal(isSearchActivityTool(tool), true, tool);
  }
  assert.equal(isSearchActivityTool("present_route"), false);
  assert.equal(isSearchActivityTool(""), false);
});

test("duplicate reasoning events reconcile idempotently", () => {
  let state = applyAgentEvent(initialState(), {
    type: "turn_started",
    text: "find pizza",
  });
  const event = { type: "reasoning", text: "Reviewing your place request…" };
  state = applyAgentEvent(state, event);
  state = applyAgentEvent(state, event);

  assert.equal(state.messages.at(-1).reasoning, "Reviewing your place request…");
});

test("failed canonical route preparation clears semantic progress so a retry cannot inherit stale work", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to JFK" });
  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "active" });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "prep-1",
    tool: "prepare_route_options",
    label: "Finding routes",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "prep-1",
    tool: "prepare_route_options",
    ok: false,
    duration_ms: 120,
  });
  assert.equal(state.messages[1].progress, undefined);
});

test("successful route preparation alone neither clears nor finalizes semantic progress", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to JFK" });
  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "active" });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "prep-ok",
    tool: "prepare_route_options",
    label: "Finding routes",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "prep-ok",
    tool: "prepare_route_options",
    ok: true,
    duration_ms: 500,
  });
  assert.deepEqual(state.messages[1].progress, { stage: "finding_routes", status: "active" });
});

test("failed route presentation clears semantic progress so a retry cannot inherit stale work", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to JFK" });
  state = applyAgentEvent(state, { type: "progress", stage: "comparing_options", status: "active" });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "present-1",
    tool: "present_route",
    label: "Presenting your route",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "present-1",
    tool: "present_route",
    ok: false,
    duration_ms: 90,
  });
  assert.equal(state.messages[1].progress, undefined);
});

test("an unrelated failed tool does not clear route progress", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "to JFK" });
  state = applyAgentEvent(state, { type: "progress", stage: "finding_routes", status: "active" });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "places-1",
    tool: "search_local_places",
    label: "Finding nearby places",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "places-1",
    tool: "search_local_places",
    ok: false,
    duration_ms: 40,
  });
  assert.deepEqual(state.messages[1].progress, { stage: "finding_routes", status: "active" });
});

test("failed steps preserve contextual activity without diagnostic timing", () => {
  let state = applyAgentEvent(initialState(), {
    type: "turn_started",
    text: "Find a place and route me there",
  });
  state = applyAgentEvent(state, {
    type: "tool_start",
    tool_call_id: "places-validation-1",
    tool: "present_places",
    label: "Reviewing verified places",
  });
  state = applyAgentEvent(state, {
    type: "tool_end",
    tool_call_id: "places-validation-1",
    tool: "present_places",
    ok: false,
    duration_ms: 7,
    summary: "That step could not be completed",
  });

  const chip = state.messages[1].toolChips[0];
  assert.equal(chip.status, "failed");
  assert.equal(chip.label, "Reviewing verified places");
  assert.equal(chip.durationMs, undefined);
});

test("dismissing an empty failed response preserves the original user message", () => {
  let state = applyAgentEvent(initialState(), { type: "turn_started", text: "hi" });
  state = applyAgentEvent(state, {
    type: "stream_error",
    message: "SmartRoute couldn’t complete this request.",
  });
  state = applyAgentEvent(state, { type: "turn_error_dismissed" });

  assert.deepEqual(state.messages, [{ role: "user", text: "hi" }]);
  assert.equal(state.error, null);
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

test("arrival clarification stays prose-only and accepts only one terminal event", () => {
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
  assert.equal(assistant.arrivals, undefined);
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
    selected_card_id: "expired-card",
    response_presentation: "auto",
  };

  await runTurn(
    transport,
    request,
    controller,
    (action) => actions.push(action),
    inFlightRef,
    abortControllerRef,
    { discardSession: () => { discarded += 1; } },
  );

  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].message, attempts[1].message);
  assert.equal(attempts[1].session_id, undefined);
  assert.equal(attempts[1].selected_card_id, undefined);
  assert.equal(discarded, 1);
  assert.deepEqual(actions.map((action) => action.type), ["meta", "token", "done"]);
  assert.equal(actions.filter((action) => action.type === "done").length, 1);
});

test("session recreation is capped at one attempt even when visible history exists", async () => {
  async function run() {
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
        discardSession: () => { discarded += 1; },
      },
    );
    return { attempts, discarded, actions };
  }

  const result = await run();
  assert.equal(result.attempts, 2);
  assert.equal(result.discarded, 1);
  assert.equal(result.actions.filter((action) => action.type === "error").length, 1);
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
    { discardSession: () => undefined },
  );

  assert.equal(attempts, 2);
  assert.deepEqual(actions, [
    {
      type: "stream_error",
      message: "SmartRoute couldn’t complete this request.",
      code: "transport_500",
      retryable: true,
      correlationId: undefined,
    },
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

test("runTurn retries once when a retryable error arrives with no rider output", async () => {
  let attempts = 0;
  const actions = [];
  const transport = async function* () {
    attempts += 1;
    yield { type: "meta", session_id: "sess-1", turn_id: `t${attempts}` };
    if (attempts === 1) {
      yield {
        type: "error",
        code: "deadline",
        message: "The response took too long. Please try again.",
        retryable: true,
      };
      yield {
        type: "done",
        session_id: "sess-1",
        turn_id: "t1",
        stop_reason: "deadline",
        usage: {},
      };
      return;
    }
    yield { type: "token", text: "The Q is the best fit." };
    yield {
      type: "done",
      session_id: "sess-1",
      turn_id: "t2",
      stop_reason: "end_turn",
      usage: {},
    };
  };
  const controller = new AbortController();

  await runTurn(
    transport,
    { message: "Get me to Barclays", response_presentation: "auto" },
    controller,
    (action) => actions.push(action),
    { current: true },
    { current: controller },
  );

  assert.equal(attempts, 2);
  assert.deepEqual(
    actions.map((action) => action.type),
    ["meta", "error", "done", "turn_retry_started", "meta", "token", "done"],
  );
});

test("runTurn does not retry after a token has already been shown", async () => {
  let attempts = 0;
  const actions = [];
  const transport = async function* () {
    attempts += 1;
    yield { type: "meta", session_id: "sess-1", turn_id: "t1" };
    yield { type: "token", text: "I'll compare live routes." };
    yield {
      type: "error",
      code: "upstream_error",
      message: "Live trip planning is temporarily unavailable.",
      retryable: true,
    };
    yield {
      type: "done",
      session_id: "sess-1",
      turn_id: "t1",
      stop_reason: "error",
      usage: {},
    };
  };
  const controller = new AbortController();

  await runTurn(
    transport,
    { message: "Get me to Barclays", response_presentation: "auto" },
    controller,
    (action) => actions.push(action),
    { current: true },
    { current: controller },
  );

  assert.equal(attempts, 1);
  assert.equal(actions.filter((action) => action.type === "turn_retry_started").length, 0);
});

test("runTurn retries a dropped connection once when no tokens arrived", async () => {
  let attempts = 0;
  const actions = [];
  const transport = async function* () {
    attempts += 1;
    yield { type: "meta", session_id: "sess-1", turn_id: `t${attempts}` };
    if (attempts === 1) return;
    yield { type: "token", text: "The next Q is in 4 minutes." };
    yield {
      type: "done",
      session_id: "sess-1",
      turn_id: "t2",
      stop_reason: "end_turn",
      usage: {},
    };
  };
  const controller = new AbortController();

  await runTurn(
    transport,
    { message: "When is the next Q?", response_presentation: "auto" },
    controller,
    (action) => actions.push(action),
    { current: true },
    { current: controller },
  );

  assert.equal(attempts, 2);
  assert.ok(actions.some((action) => action.type === "turn_retry_started"));
  assert.equal(actions.at(-1)?.type, "done");
});


function validCard(cardId, turnId, role = "recommended") {
  return {
    card_id: cardId,
    turn_id: turnId,
    role,
    origin: { label: "Your location", lat: 40.7484, lng: -73.9857 },
    destination: { label: "Costco", lat: 40.7549, lng: -73.9872 },
    summary: { eta_minutes: 18, transfers: 0, lines: ["Q"], reason: "fastest" },
    route: [{ type: "SUBWAY", route_id: "Q" }],
    alerts: [],
  };
}

function validSnapshot(overrides = {}) {
  return {
    session_id: "sess-1",
    history: [
      { role: "user", text: "to Costco" },
      { role: "assistant", text: "Here is your route.", turn_id: "t1" },
    ],
    route_cards: [validCard("rc_1", "t1")],
    arrival_cards: [],
    ...overrides,
  };
}

test("session_restored replaces state with the snapshot transcript", () => {
  const turns = [
    { role: "user", text: "to Costco" },
    {
      role: "assistant", turnId: "t1", text: "Here is your route.", reasoning: "",
      toolChips: [], routeCards: [], isStreaming: false,
    },
  ];
  const state = applyAgentEvent(
    initialState({
      sessionId: "sess-old",
      error: "old error",
    }),
    { type: "session_restored", sessionId: "sess-1", turns },
  );

  assert.deepEqual(state, {
    messages: turns,
    sessionId: "sess-1",
    isStreaming: false,
    error: null,
  });
});

test("session_restored cannot overwrite a turn that started while restore was pending", () => {
  const active = applyAgentEvent(initialState({ sessionId: "sess-1" }), {
    type: "turn_started",
    text: "new request",
  });
  const restored = applyAgentEvent(active, {
    type: "session_restored",
    sessionId: "sess-1",
    turns: [{ role: "user", text: "old request" }],
  });
  assert.deepEqual(restored, active);
});

test("buildTurnsFromSnapshot rebuilds the transcript and attaches cards by turn id", () => {
  const snapshot = validSnapshot({
    history: [
      { role: "user", text: "to Costco" },
      { role: "assistant", text: "Here is your route.", turn_id: "t1" },
      { role: "user", text: "and back?" },
      { role: "assistant", text: "Same route works.", turn_id: "t2" },
    ],
    route_cards: [validCard("rc_1", "t1"), validCard("rc_2", "t2", "alternative")],
  });

  const turns = buildTurnsFromSnapshot(snapshot);

  assert.equal(turns.length, 4);
  assert.equal(turns[0].role, "user");
  assert.equal(turns[1].role, "assistant");
  assert.equal(turns[1].turnId, "t1");
  assert.deepEqual(
    turns[1].routeCards.map((card) => card.card_id),
    ["rc_1"],
  );
  assert.equal(turns[2].role, "user");
  assert.deepEqual(
    turns[3].routeCards.map((card) => card.card_id),
    ["rc_2"],
  );
});

test("buildTurnsFromSnapshot drops cards without a matching transcript turn", () => {
  const snapshot = validSnapshot({
    history: [
      { role: "user", text: "to Costco" },
      { role: "assistant", text: "Here is your route.", turn_id: "t1" },
      { role: "assistant", text: "Older turn without a stored turn id." },
    ],
    route_cards: [
      validCard("rc_1", "t1"),
      validCard("rc_orphan", "t9"),
      validCard("rc_unmatched", "t99"),
    ],
  });

  const turns = buildTurnsFromSnapshot(snapshot);

  assert.deepEqual(turns[1].routeCards.map((card) => card.card_id), ["rc_1"]);
  // No card is attached to the assistant entry lacking a turn id.
  assert.deepEqual(turns[2].routeCards, []);
});

test("buildTurnsFromSnapshot restores an arrivals card on its producing turn", () => {
  const snapshot = validSnapshot({
    route_cards: [],
    arrival_cards: [{
      type: "arrival_card",
      turn_id: "t1",
      route_id: "Q",
      stop: { name: "Church Av", latitude: 40.64, longitude: -73.96 },
      directions: [{
        id: "downtown",
        label: "Downtown / Brooklyn-bound",
        arrivals: [{ expected_at: "2026-08-13T12:05:00-04:00", minutes: 5, realtime: true }],
      }],
      updated_at: "2026-08-13T12:00:00-04:00",
      source_status: "live",
      resolution_status: "resolved",
    }],
  });

  const turns = buildTurnsFromSnapshot(snapshot);
  assert.equal(turns[1].arrivals.routeId, "Q");
  assert.equal(turns[1].arrivals.stationName, "Church Av");
  assert.deepEqual(turns[1].arrivals.groups[0].minutes, [5]);
});

test("buildTurnsFromSnapshot restores validated sources on their producing turn", async () => {
  const result = await fetchSessionSnapshot(
    "sess-1",
    async () => new Response(JSON.stringify(validSnapshot({
      sources: [{
        turn_id: "t1",
        sources: [
          { title: "Damn Lines", url: "https://damnlines.com/camera/l-industrie" },
          { title: "Untrusted", url: "https://example.com/camera/l-industrie" },
        ],
      }, {
        turn_id: "t1",
        sources: [{ title: "Damn Lines", url: "https://damnlines.com/average-wait-times" }],
      }],
    })), { status: 200 }),
  );

  assert.equal(result.status, "ok");
  assert.deepEqual(result.turns[1].sources, [
    { title: "Damn Lines", url: "https://damnlines.com/average-wait-times" },
  ]);
});

test("snapshot restore remains compatible when older transcripts omit sources", async () => {
  const result = await fetchSessionSnapshot(
    "sess-1",
    async () => new Response(JSON.stringify(validSnapshot()), { status: 200 }),
  );

  assert.equal(result.status, "ok");
  assert.equal(result.turns[1].sources, undefined);
});

test("fetchSessionSnapshot returns expired for a 404", async () => {
  const result = await fetchSessionSnapshot(
    "sess-1",
    async () => new Response(JSON.stringify({ detail: "session_expired" }), { status: 404 }),
  );

  assert.deepEqual(result, { status: "expired" });
});

test("fetchSessionSnapshot rebuilds turns from a valid snapshot", async () => {
  const result = await fetchSessionSnapshot(
    "sess-1",
    async () => new Response(JSON.stringify(validSnapshot()), { status: 200 }),
  );

  assert.equal(result.status, "ok");
  assert.equal(result.turns.length, 2);
  assert.equal(result.turns[0].role, "user");
  assert.deepEqual(
    result.turns[1].routeCards.map((card) => card.card_id),
    ["rc_1"],
  );
});

test("fetchSessionSnapshot drops invalid cards but keeps the transcript", async () => {
  const snapshot = validSnapshot({
    route_cards: [
      validCard("rc_ok", "t1"),
      { card_id: "rc_bad", turn_id: "t1", role: "recommended" },
    ],
  });
  const result = await fetchSessionSnapshot(
    "sess-1",
    async () => new Response(JSON.stringify(snapshot), { status: 200 }),
  );

  assert.equal(result.status, "ok");
  assert.deepEqual(
    result.turns[1].routeCards.map((card) => card.card_id),
    ["rc_ok"],
  );
});

test("fetchSessionSnapshot treats malformed snapshots as unavailable", async () => {
  for (const payload of [
    { session_id: "sess-1", history: [{ role: "weird", text: "x" }], route_cards: [] },
    { session_id: "sess-1", history: [{ role: "user", text: 42 }], route_cards: [] },
    { session_id: "", history: [], route_cards: [] },
    "not json at all",
  ]) {
    const result = await fetchSessionSnapshot(
      "sess-1",
      async () => new Response(JSON.stringify(payload), { status: 200 }),
    );
    assert.deepEqual(result, { status: "unavailable" }, JSON.stringify(payload));
  }
});

test("fetchSessionSnapshot treats transport and server failures as unavailable", async () => {
  const failing = [
    async () => new Response("boom", { status: 500 }),
    async () => new Response("oops", { status: 503 }),
    async () => {
      throw new TypeError("network down");
    },
  ];
  for (const transport of failing) {
    const result = await fetchSessionSnapshot("sess-1", transport);
    assert.deepEqual(result, { status: "unavailable" });
  }
});
