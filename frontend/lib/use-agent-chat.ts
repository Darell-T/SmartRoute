"use client";

/**
 * Stateful hook fronting the conversational transit agent. Owns the turn
 * lifecycle (send/cancel/stream-dispatch) and delegates all state
 * transitions to `applyAgentEvent`, a pure reducer kept exportable so the
 * event-sequence → final-turn-state behavior is unit-testable without a DOM
 * (see `use-agent-chat.test.mjs`).
 *
 * Talks to `/api/agent/chat` (frontend/app/api/agent/chat/route.ts), which
 * proxies `backend/app/routers/agent_chat.py`. On a non-2xx response that
 * route returns redacted JSON (`{error: string}`), not an SSE stream — see
 * `readTransportErrorMessage` below.
 */

import { useEffect, useReducer, useRef, useState, type Dispatch } from "react";
import {
  parseSseStream,
  type AgentEvent,
  type RouteCard,
} from "./agent-chat-stream";

const SESSION_STORAGE_KEY = "sr-agent-session";
const CHAT_ENDPOINT = "/api/agent/chat";

export interface ToolChip {
  id: string;
  tool: string;
  label: string;
  status: "running" | "ok" | "failed";
  durationMs?: number;
  summary?: string;
}

export interface UserTurn {
  role: "user";
  text: string;
}

export interface AssistantTurn {
  role: "assistant";
  /** Empty until the `meta` event for this turn arrives. */
  turnId: string;
  /** Accumulates from `token` events. */
  text: string;
  toolChips: ToolChip[];
  routeCards: RouteCard[];
  isStreaming: boolean;
  /** `end_turn|max_rounds|deadline|error` from the backend, or the two
   *  frontend-only outcomes for a turn that never got a `done`: `cancelled`
   *  (the rider hit stop) and `dropped` (the connection died first). */
  stopReason?: "end_turn" | "max_rounds" | "deadline" | "error" | "cancelled" | "dropped";
  error?: { code: string; message: string; retryable: boolean };
}

export type ChatTurn = UserTurn | AssistantTurn;

export interface ChatState {
  messages: ChatTurn[];
  sessionId: string | null;
  isStreaming: boolean;
  /** Last turn-level error message, surfaced independently of any one
   *  turn's `error` field so the composer can show a banner. */
  error: string | null;
}

/** Actions the reducer accepts: every real `AgentEvent` off the wire, plus
 *  three synthetic actions the hook raises for turn bookkeeping the backend
 *  has no event for (starting a turn, and the two non-`done` ways a stream
 *  can end). */
export type ChatReducerAction =
  | AgentEvent
  | { type: "turn_started"; text: string }
  | { type: "stream_error"; message: string }
  | { type: "stream_cancelled" };

function lastAssistantTurn(messages: ChatTurn[]): AssistantTurn | null {
  const last = messages[messages.length - 1];
  return last && last.role === "assistant" ? last : null;
}

/** Immutably updates the last message if (and only if) it's an assistant
 *  turn — every reducer branch below targets "the turn currently
 *  streaming," which by construction is always the last message once
 *  `turn_started` has fired. Events arriving with no assistant turn yet
 *  (shouldn't happen given `meta` is always first) are dropped rather than
 *  crashing the reducer. */
function updateLastAssistantTurn(
  state: ChatState,
  update: (turn: AssistantTurn) => AssistantTurn,
): ChatState {
  const turn = lastAssistantTurn(state.messages);
  if (!turn) return state;
  const messages = state.messages.slice();
  messages[messages.length - 1] = update(turn);
  return { ...state, messages };
}

function cardFromEvent(event: Extract<AgentEvent, { type: "route_card" }>): RouteCard {
  const { type: _type, ...card } = event;
  return card;
}

/**
 * Pure state transition: `(state, action) -> nextState`. Exported
 * specifically so the event-sequence tests can drive it directly without
 * mounting a component or faking `fetch`.
 */
export function applyAgentEvent(state: ChatState, action: ChatReducerAction): ChatState {
  switch (action.type) {
    case "turn_started": {
      const userTurn: UserTurn = { role: "user", text: action.text };
      const assistantTurn: AssistantTurn = {
        role: "assistant",
        turnId: "",
        text: "",
        toolChips: [],
        routeCards: [],
        isStreaming: true,
      };
      return {
        ...state,
        messages: [...state.messages, userTurn, assistantTurn],
        isStreaming: true,
        error: null,
      };
    }
    case "meta": {
      return {
        ...updateLastAssistantTurn(state, (turn) => ({ ...turn, turnId: action.turn_id })),
        sessionId: action.session_id,
      };
    }
    case "token": {
      return updateLastAssistantTurn(state, (turn) => ({ ...turn, text: turn.text + action.text }));
    }
    case "tool_start": {
      return updateLastAssistantTurn(state, (turn) => ({
        ...turn,
        toolChips: [
          ...turn.toolChips,
          { id: action.tool_call_id, tool: action.tool, label: action.label, status: "running" },
        ],
      }));
    }
    case "tool_end": {
      return updateLastAssistantTurn(state, (turn) => ({
        ...turn,
        toolChips: turn.toolChips.map((chip) =>
          chip.id === action.tool_call_id
            ? {
                ...chip,
                status: action.ok ? "ok" : "failed",
                durationMs: action.duration_ms,
                summary: action.summary,
              }
            : chip,
        ),
      }));
    }
    case "route_card": {
      const card = cardFromEvent(action);
      return updateLastAssistantTurn(state, (turn) => ({
        ...turn,
        routeCards: [...turn.routeCards, card],
      }));
    }
    case "error": {
      return {
        ...updateLastAssistantTurn(state, (turn) => ({
          ...turn,
          error: { code: action.code, message: action.message, retryable: action.retryable },
        })),
        error: action.message,
      };
    }
    case "done": {
      return {
        ...updateLastAssistantTurn(state, (turn) => ({
          ...turn,
          isStreaming: false,
          stopReason: action.stop_reason,
        })),
        sessionId: action.session_id,
        isStreaming: false,
      };
    }
    case "stream_cancelled": {
      return {
        ...updateLastAssistantTurn(state, (turn) => ({
          ...turn,
          isStreaming: false,
          stopReason: "cancelled",
        })),
        isStreaming: false,
      };
    }
    case "stream_error": {
      return {
        ...updateLastAssistantTurn(state, (turn) => ({
          ...turn,
          isStreaming: false,
          stopReason: "dropped",
          error: turn.error ?? { code: "upstream_error", message: action.message, retryable: true },
        })),
        isStreaming: false,
        error: action.message,
      };
    }
    default:
      return state;
  }
}

/** `Pick<Storage, ...>` rather than `Storage` so tests can pass a plain
 *  object instead of needing `jsdom`/`sessionStorage`. */
type StorageLike = Pick<Storage, "getItem" | "setItem">;

/** `sessionStorage` throws in some privacy-mode/embedded-webview contexts
 *  even though `window` exists — treat that the same as "no storage
 *  available" rather than crashing turn 1. */
function safeSessionStorage(): StorageLike | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const storage = window.sessionStorage;
    storage.getItem(SESSION_STORAGE_KEY); // touch it — some contexts throw lazily
    return storage;
  } catch {
    return undefined;
  }
}

/** Reads the persisted session id (if any) so a page refresh continues the
 *  same conversation instead of silently starting a new one. */
export function readPersistedSessionId(storage: StorageLike | undefined): string | null {
  if (!storage) return null;
  try {
    return storage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Persists the session id from a `meta`/`done` event. No-op for a null
 *  session id or unavailable storage. */
export function persistSessionId(storage: StorageLike | undefined, sessionId: string | null): void {
  if (!storage || !sessionId) return;
  try {
    storage.setItem(SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Storage full/blocked: the conversation still works for this tab, it
    // just won't survive a refresh. Not worth surfacing to the rider.
  }
}

function initChatState(): ChatState {
  return {
    messages: [],
    sessionId: readPersistedSessionId(safeSessionStorage()),
    isStreaming: false,
    error: null,
  };
}

export interface AgentChatRequestBody {
  session_id?: string;
  message: string;
  origin?: { lat: number; lng: number };
  selected_card_id?: string;
}

/** Injection point for the dev harness: replaces the real fetch/SSE
 *  transport with a canned async generator so `/dev/agent-chat` works with
 *  no backend at all. The default implementation is `fetchAgentChatEvents`
 *  below. */
export type AgentChatTransport = (
  request: AgentChatRequestBody,
  signal: AbortSignal,
) => AsyncGenerator<AgentEvent>;

async function readTransportErrorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && typeof (body as { error?: unknown }).error === "string") {
      return (body as { error: string }).error;
    }
  } catch {
    // Not JSON (or empty body) — fall through to the generic message.
  }
  return `SmartRoute is unavailable right now (${res.status}).`;
}

/** Real transport: POSTs to the Next proxy and decodes its SSE body. On a
 *  non-2xx response the proxy returns redacted JSON instead of a stream
 *  (see backend-stream-proxy.ts), so that path is handled before ever
 *  touching `parseSseStream`. */
async function* fetchAgentChatEvents(
  request: AgentChatRequestBody,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch(CHAT_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(await readTransportErrorMessage(res));
  }

  yield* parseSseStream(res.body.getReader());
}

export interface UseAgentChatOptions {
  /** Rider's current location, attached to the next `send()` when
   *  available. Optional: the agent can still geocode a named origin. */
  getOrigin?: () => { lat: number; lng: number } | null;
  /** Overrides the real fetch/SSE transport — used by the `/dev/agent-chat`
   *  harness to replay a scripted event sequence with no backend. */
  transport?: AgentChatTransport;
}

export interface UseAgentChatResult {
  messages: ChatTurn[];
  send: (text: string) => void;
  cancel: () => void;
  isStreaming: boolean;
  error: string | null;
  sessionId: string | null;
  selectCard: (cardId: string) => void;
  selectedCardId: string | null;
}

export function useAgentChat(options: UseAgentChatOptions = {}): UseAgentChatResult {
  const { getOrigin, transport = fetchAgentChatEvents } = options;
  const [state, dispatch] = useReducer(applyAgentEvent, undefined, initChatState);
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);

  // A plain (non-React-state) guard: `send` is a fresh closure every render,
  // so two calls issued in the same tick (before React re-renders) would
  // both see the same stale `state.isStreaming` if that were the guard.
  // This ref is the single source of truth for "a turn is in flight" and is
  // only ever touched from event handlers, never during render.
  const inFlightRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    persistSessionId(safeSessionStorage(), state.sessionId);
  }, [state.sessionId]);

  function send(text: string): void {
    const trimmed = text.trim();
    if (!trimmed || inFlightRef.current) return;

    inFlightRef.current = true;
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const cardId = selectedCardId;
    setSelectedCardId(null); // spec: cleared once it's been included in a send

    dispatch({ type: "turn_started", text: trimmed });

    const request: AgentChatRequestBody = {
      session_id: state.sessionId ?? undefined,
      message: trimmed,
      origin: getOrigin?.() ?? undefined,
      selected_card_id: cardId ?? undefined,
    };

    void runTurn(transport, request, controller, dispatch, inFlightRef, abortControllerRef);
  }

  function cancel(): void {
    abortControllerRef.current?.abort();
  }

  function selectCard(cardId: string): void {
    setSelectedCardId(cardId);
  }

  return {
    messages: state.messages,
    send,
    cancel,
    isStreaming: state.isStreaming,
    error: state.error,
    sessionId: state.sessionId,
    selectCard,
    selectedCardId,
  };
}

/** Drives one turn's transport to completion and dispatches the terminal
 *  bookkeeping action (`stream_cancelled` / `stream_error`) when the stream
 *  ends without a `done` event — "no polling, no reconnection: a dropped
 *  stream just finalizes the turn." Pulled out of `send` so the guard/abort
 *  refs it needs are passed explicitly rather than closed over stale. */
async function runTurn(
  transport: AgentChatTransport,
  request: AgentChatRequestBody,
  controller: AbortController,
  dispatch: Dispatch<ChatReducerAction>,
  inFlightRef: { current: boolean },
  abortControllerRef: { current: AbortController | null },
): Promise<void> {
  let receivedDone = false;
  try {
    for await (const event of transport(request, controller.signal)) {
      if (event.type === "done") receivedDone = true;
      dispatch(event);
    }
    if (!receivedDone && !controller.signal.aborted) {
      dispatch({
        type: "stream_error",
        message: "The connection to SmartRoute dropped before it finished responding.",
      });
    }
  } catch (err) {
    if (controller.signal.aborted) {
      dispatch({ type: "stream_cancelled" });
    } else {
      dispatch({
        type: "stream_error",
        message: err instanceof Error ? err.message : "Could not reach SmartRoute.",
      });
    }
  } finally {
    inFlightRef.current = false;
    if (abortControllerRef.current === controller) abortControllerRef.current = null;
  }
}

