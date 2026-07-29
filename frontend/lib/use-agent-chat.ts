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
  clearPersistedSession,
  initChatState,
  persistSessionId,
  readPersistedSessionId,
  safeSessionStorage,
  sessionStorageKey,
} from "./agent-chat-session";
import type { ResponsePresentationMode } from "./response-presentation";

export { applyAgentEvent, buildAgentChatRequest, persistSessionId, readPersistedSessionId, runTurn, sessionStorageKey };
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

  useEffect(() => {
    persistSessionId(safeSessionStorage(), state.sessionId);
  }, [state.sessionId]);

  function send(
    text: string,
    responsePresentation: ResponsePresentationMode = "auto",
  ): void {
    const trimmed = text.trim();
    if (!trimmed || inFlightRef.current) return;

    inFlightRef.current = true;
    const controller = new AbortController();
    abortControllerRef.current = controller;

    const cardId = selectedCardId;
    setSelectedCardId(null); // spec: cleared once it's been included in a send

    dispatch({ type: "turn_started", text: trimmed });

    const request = buildAgentChatRequest({
      sessionId: state.sessionId,
      message: trimmed,
      origin: getOrigin?.(),
      selectedCardId: cardId,
      responsePresentation,
    });

    void runTurn(
      transport,
      request,
      controller,
      dispatch,
      inFlightRef,
      abortControllerRef,
      {
        canRecoverSession: state.messages.length === 0,
        discardSession: () => {
          persistSessionId(safeSessionStorage(), null);
          dispatch({ type: "session_discarded" });
        },
      },
    );
  }

  function cancel(): void {
    abortControllerRef.current?.abort();
  }

  function reset(): void {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    inFlightRef.current = false;
    setSelectedCardId(null);
    localTurnSeqRef.current = 0;
    clearPersistedSession();
    dispatch({ type: "chat_reset" });
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
    isStreaming: state.isStreaming,
    error: state.error,
    sessionId: state.sessionId,
    selectCard,
    selectedCardId,
    appendLocalTurn,
  };
}

