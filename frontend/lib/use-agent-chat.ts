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
 * route returns redacted JSON (`{error: string}`), not an SSE stream.
 */

import { useEffect, useReducer, useRef, useState } from "react";
import {
  fetchAgentChatEvents,
  runTurn,
  type AgentChatTransport,
} from "./agent-chat-controller";
import { buildAgentChatRequest, type AgentChatRequestBody } from "./agent-chat-request";
import {
  applyAgentEvent,
  type ArrivalsTurnDirectionGroup,
  type ArrivalsTurnPayload,
  type AssistantTurn,
  type ChatReducerAction,
  type ChatState,
  type ChatTurn,
  type ToolChip,
  type UserTurn,
} from "./agent-chat-state";
import {
  buildTurnsFromSnapshot,
  clearPersistedSession,
  fetchSessionSnapshot,
  initChatState,
  persistSessionId,
  readPersistedSessionId,
  resetSession,
  safeSessionStorage,
  sessionStorageKey,
} from "./agent-chat-session";
import type { ResponsePresentationMode } from "./response-presentation";

export {
  applyAgentEvent,
  buildAgentChatRequest,
  buildTurnsFromSnapshot,
  fetchSessionSnapshot,
  persistSessionId,
  readPersistedSessionId,
  resetSession,
  runTurn,
  sessionStorageKey,
};
export type {
  AgentChatRequestBody,
  AgentChatTransport,
  ArrivalsTurnDirectionGroup,
  ArrivalsTurnPayload,
  AssistantTurn,
  ChatReducerAction,
  ChatState,
  ChatTurn,
  ToolChip,
  UserTurn,
};

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
  send: (text: string, responsePresentation?: ResponsePresentationMode) => void;
  cancel: () => void;
  reset: () => void;
  retryLast: () => void;
  dismissError: () => void;
  isStreaming: boolean;
  error: string | null;
  sessionId: string | null;
  selectCard: (cardId: string) => void;
  selectedCardId: string | null;
  /** Appends a display-only assistant-style turn carrying an arrivals
   *  payload instead of streamed text — the "tap a Near You bullet" flow.
   *  Never touches the network or the session; the backend never sees it. */
  appendLocalTurn: (content: { text: string; arrivals: ArrivalsTurnPayload }) => void;
}

export function useAgentChat(options: UseAgentChatOptions = {}): UseAgentChatResult {
  const { getOrigin, transport = fetchAgentChatEvents } = options;
  const [state, dispatch] = useReducer(applyAgentEvent, undefined, initChatState);
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);
  const localTurnSeqRef = useRef(0);

  // A plain (non-React-state) guard: `send` is a fresh closure every render,
  // so two calls issued in the same tick (before React re-renders) would
  // both see the same stale `state.isStreaming` if that were the guard.
  // This ref is the single source of truth for "a turn is in flight" and is
  // only ever touched from event handlers, never during render.
  const inFlightRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const restoreGenerationRef = useRef(0);
  const lastRequestRef = useRef<{
    request: AgentChatRequestBody;
  } | null>(null);

  useEffect(() => {
    persistSessionId(safeSessionStorage(), state.sessionId);
  }, [state.sessionId]);

  // Page-refresh restore: a still-valid persisted session id is hydrated with
  // the backend transcript (text turns + canonical cards) so the rider
  // sees the same conversation the agent remembers. Expired sessions discard
  // the persisted id; transient failures leave the server session intact.
  useEffect(() => {
    const sessionId = state.sessionId;
    if (!sessionId || state.messages.length > 0) return;
    const generation = ++restoreGenerationRef.current;
    void fetchSessionSnapshot(sessionId)
      .then((result) => {
        if (restoreGenerationRef.current !== generation) return;
        if (result.status === "ok") {
          dispatch({ type: "session_restored", sessionId, turns: result.turns });
        } else if (result.status === "expired") {
          clearPersistedSession();
          dispatch({ type: "session_discarded" });
        }
      })
      .catch(() => undefined);
    return () => {
      if (restoreGenerationRef.current === generation) {
        restoreGenerationRef.current += 1;
      }
    };
  }, [state.sessionId, state.messages.length]);

  function send(
    text: string,
    responsePresentation: ResponsePresentationMode = "auto",
  ): void {
    const trimmed = text.trim();
    if (!trimmed || inFlightRef.current) return;

    const cardId = selectedCardId;
    setSelectedCardId(null); // spec: cleared once it's been included in a send

    const request = buildAgentChatRequest({
      sessionId: state.sessionId,
      message: trimmed,
      origin: getOrigin?.(),
      selectedCardId: cardId,
      responsePresentation,
    });

    lastRequestRef.current = {
      request,
    };
    startTurn(request, { type: "turn_started", text: trimmed });
  }

  function startTurn(
    request: AgentChatRequestBody,
    action: ChatReducerAction,
  ): void {
    // A live turn supersedes any in-flight refresh restore: its snapshot
    // result must not overwrite the turn the rider just started.
    restoreGenerationRef.current += 1;
    inFlightRef.current = true;
    const controller = new AbortController();
    abortControllerRef.current = controller;
    dispatch(action);

    void runTurn(
      transport,
      request,
      controller,
      dispatch,
      inFlightRef,
      abortControllerRef,
      {
        discardSession: () => {
          persistSessionId(safeSessionStorage(), null);
          dispatch({ type: "session_discarded" });
          dispatch({
            type: "session_restarted",
            message: "Earlier context expired, so this request is starting a fresh session.",
          });
        },
      },
    );
  }

  function retryLast(): void {
    const previous = lastRequestRef.current;
    if (!previous || inFlightRef.current) return;
    startTurn(
      previous.request,
      { type: "turn_retry_started" },
    );
  }

  function dismissError(): void {
    lastRequestRef.current = null;
    dispatch({ type: "turn_error_dismissed" });
  }

  function cancel(): void {
    abortControllerRef.current?.abort();
  }

  function reset(): void {
    const sessionId = state.sessionId;
    restoreGenerationRef.current += 1;
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    inFlightRef.current = false;
    setSelectedCardId(null);
    localTurnSeqRef.current = 0;
    lastRequestRef.current = null;
    clearPersistedSession();
    dispatch({ type: "chat_reset" });
    if (sessionId) void resetSession(sessionId);
  }

  function selectCard(cardId: string): void {
    setSelectedCardId(cardId);
  }

  function appendLocalTurn(content: { text: string; arrivals: ArrivalsTurnPayload }): void {
    localTurnSeqRef.current += 1;
    dispatch({
      type: "local_turn_appended",
      turnId: `local-${localTurnSeqRef.current}`,
      text: content.text,
      arrivals: content.arrivals,
    });
  }

  return {
    messages: state.messages,
    send,
    cancel,
    reset,
    retryLast,
    dismissError,
    isStreaming: state.isStreaming,
    error: state.error,
    sessionId: state.sessionId,
    selectCard,
    selectedCardId,
    appendLocalTurn,
  };
}
