import type { Dispatch } from "react";
import { safeChatFailure } from "./failure-copy";
import { parseSseStream, type AgentEvent } from "./stream";
import type { AgentChatRequestBody } from "./request";
import type { ChatReducerAction } from "./state";

const CHAT_ENDPOINT = "/api/agent/chat";
const DROPPED_STREAM_MESSAGE =
  "The connection to SmartRoute dropped before it finished responding.";
const GENERIC_TRANSPORT_MESSAGE = "SmartRoute couldn’t complete this request.";

export type AgentChatTransport = (
  request: AgentChatRequestBody,
  signal: AbortSignal,
) => AsyncGenerator<AgentEvent>;

export interface SessionRecoveryOptions {
  discardSession: () => void;
}

interface MutableRef<T> {
  current: T;
}

export class AgentChatTransportError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryable: boolean,
    readonly correlationId: string | null,
  ) {
    super(message);
    this.name = "AgentChatTransportError";
  }
}

type TurnAttemptOutcome =
  | {
      kind: "ended";
      receivedDone: boolean;
      sessionExpired: boolean;
      retryableFailure: boolean;
      sawRiderOutput: boolean;
      buffered: AgentEvent[];
    }
  | { kind: "cancelled" }
  | {
      kind: "transport_error";
      error: AgentChatTransportError;
      sawRiderOutput: boolean;
    };

export async function* fetchAgentChatEvents(
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
    // The proxy body is deliberately ignored. Status is the only trusted
    // browser-facing input, which prevents an upstream/provider body from
    // ever becoming assistant copy if a proxy boundary regresses.
    const failure = safeChatFailure(res.ok ? 502 : res.status);
    throw new AgentChatTransportError(
      failure.message,
      res.ok ? 502 : res.status,
      failure.retryable,
      res.headers.get("x-smartroute-request-id"),
    );
  }
  yield* parseSseStream(res.body.getReader());
}

function isRiderVisibleOutput(event: AgentEvent): boolean {
  if (event.type === "token") return event.text.trim().length > 0;
  return event.type === "route_card" || event.type === "arrival_card";
}

function isRetryableProviderFailure(event: AgentEvent): boolean {
  return event.type === "error" && event.retryable && event.code !== "session_expired";
}

function shouldBufferStreamEvent(
  event: AgentEvent,
  waitingForFirstPair: boolean,
  sessionExpired: boolean,
): boolean {
  if (waitingForFirstPair && event.type === "meta") return true;
  if (event.type === "error" && event.code === "session_expired") return true;
  return sessionExpired;
}

function transportErrorFromUnknown(cause: unknown): AgentChatTransportError {
  return cause instanceof AgentChatTransportError
    ? cause
    : new AgentChatTransportError(GENERIC_TRANSPORT_MESSAGE, 500, true, null);
}

async function runOneTurnAttempt(
  transport: AgentChatTransport,
  request: AgentChatRequestBody,
  controller: AbortController,
  dispatch: Dispatch<ChatReducerAction>,
): Promise<TurnAttemptOutcome> {
  let sawRiderOutput = false;
  const buffered: AgentEvent[] = [];
  let sessionExpired = false;
  let retryableFailure = false;
  let receivedDone = false;
  try {
    for await (const event of transport(request, controller.signal)) {
      if (isRiderVisibleOutput(event)) sawRiderOutput = true;
      if (isRetryableProviderFailure(event)) retryableFailure = true;
      if (shouldBufferStreamEvent(event, buffered.length === 0, sessionExpired)) {
        if (event.type === "error" && event.code === "session_expired") {
          sessionExpired = true;
        }
        buffered.push(event);
        if (event.type === "done") receivedDone = true;
        continue;
      }
      for (const pending of buffered.splice(0)) dispatch(pending);
      if (event.type === "done") receivedDone = true;
      dispatch(event);
    }
    return {
      kind: "ended",
      receivedDone,
      sessionExpired,
      retryableFailure,
      sawRiderOutput,
      buffered,
    };
  } catch (err) {
    if (controller.signal.aborted) return { kind: "cancelled" };
    return {
      kind: "transport_error",
      error: transportErrorFromUnknown(err),
      sawRiderOutput,
    };
  }
}

function shouldRetryTransportFailure(
  outcome: Extract<TurnAttemptOutcome, { kind: "transport_error" }>,
  failureRetryAttempted: boolean,
): boolean {
  return !failureRetryAttempted && !outcome.sawRiderOutput && outcome.error.retryable;
}

function shouldRetryEndedAttempt(
  outcome: Extract<TurnAttemptOutcome, { kind: "ended" }>,
  failureRetryAttempted: boolean,
  aborted: boolean,
): boolean {
  if (aborted || outcome.sawRiderOutput || failureRetryAttempted) return false;
  return outcome.retryableFailure || !outcome.receivedDone;
}

function dispatchTransportFailure(
  dispatch: Dispatch<ChatReducerAction>,
  error: AgentChatTransportError,
): void {
  dispatch({
    type: "stream_error",
    message: error.message,
    code: `transport_${error.status}`,
    retryable: error.retryable,
    correlationId: error.correlationId ?? undefined,
  });
}

function shouldRecoverExpiredSession(
  outcome: Extract<TurnAttemptOutcome, { kind: "ended" }>,
  recoveryAttempted: boolean,
  aborted: boolean,
): boolean {
  return outcome.sessionExpired && !recoveryAttempted && !aborted;
}

function withoutExpiredSession(request: AgentChatRequestBody): AgentChatRequestBody {
  return {
    ...request,
    session_id: undefined,
    selected_card_id: undefined,
  };
}

function dispatchDroppedStream(
  dispatch: Dispatch<ChatReducerAction>,
  outcome: Extract<TurnAttemptOutcome, { kind: "ended" }>,
  aborted: boolean,
): void {
  if (outcome.receivedDone || aborted) return;
  dispatch({
    type: "stream_error",
    message: DROPPED_STREAM_MESSAGE,
  });
}

export async function runTurn(
  transport: AgentChatTransport,
  request: AgentChatRequestBody,
  controller: AbortController,
  dispatch: Dispatch<ChatReducerAction>,
  inFlightRef: MutableRef<boolean>,
  abortControllerRef: MutableRef<AbortController | null>,
  recovery: SessionRecoveryOptions = {
    discardSession: () => undefined,
  },
): Promise<void> {
  let recoveryAttempted = false;
  let failureRetryAttempted = false;
  let activeRequest = request;
  try {
    while (true) {
      const outcome = await runOneTurnAttempt(
        transport,
        activeRequest,
        controller,
        dispatch,
      );
      if (outcome.kind === "cancelled") {
        dispatch({ type: "stream_cancelled" });
        break;
      }
      if (outcome.kind === "transport_error") {
        if (shouldRetryTransportFailure(outcome, failureRetryAttempted)) {
          failureRetryAttempted = true;
          dispatch({ type: "turn_retry_started" });
          continue;
        }
        dispatchTransportFailure(dispatch, outcome.error);
        break;
      }
      if (shouldRecoverExpiredSession(outcome, recoveryAttempted, controller.signal.aborted)) {
        recoveryAttempted = true;
        failureRetryAttempted = true;
        recovery.discardSession();
        // The new backend session cannot resolve a card owned by the
        // expired session. Preserve the rider's message and location, but
        // let the fresh turn resolve its own authoritative context.
        activeRequest = withoutExpiredSession(request);
        continue;
      }
      for (const pending of outcome.buffered) dispatch(pending);
      if (shouldRetryEndedAttempt(outcome, failureRetryAttempted, controller.signal.aborted)) {
        failureRetryAttempted = true;
        dispatch({ type: "turn_retry_started" });
        continue;
      }
      dispatchDroppedStream(dispatch, outcome, controller.signal.aborted);
      break;
    }
  } finally {
    inFlightRef.current = false;
    if (abortControllerRef.current === controller) abortControllerRef.current = null;
  }
}
