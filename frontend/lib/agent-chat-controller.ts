import type { Dispatch } from "react";
import { safeChatFailure } from "./chat-failure-copy";
import { parseSseStream, type AgentEvent } from "./agent-chat-stream";
import type { AgentChatRequestBody } from "./agent-chat-request";
import type { ChatReducerAction } from "./agent-chat-state";

const CHAT_ENDPOINT = "/api/agent/chat";

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
  let receivedDone = false;
  let recoveryAttempted = false;
  let failureRetryAttempted = false;
  let activeRequest = request;
  try {
    while (true) {
      let sawRiderOutput = false;
      try {
        const buffered: AgentEvent[] = [];
        let sessionExpired = false;
        let retryableFailure = false;
        receivedDone = false;

        for await (const event of transport(activeRequest, controller.signal)) {
          if (isRiderVisibleOutput(event)) sawRiderOutput = true;
          if (
            event.type === "error" &&
            event.retryable &&
            event.code !== "session_expired"
          ) {
            retryableFailure = true;
          }
          if (buffered.length === 0 && event.type === "meta") {
            buffered.push(event);
            continue;
          }
          if (event.type === "error" && event.code === "session_expired") {
            sessionExpired = true;
            buffered.push(event);
            continue;
          }
          if (sessionExpired) {
            buffered.push(event);
            if (event.type === "done") receivedDone = true;
            continue;
          }
          for (const pending of buffered.splice(0)) dispatch(pending);
          if (event.type === "done") receivedDone = true;
          dispatch(event);
        }

        if (
          sessionExpired && !recoveryAttempted && !controller.signal.aborted
        ) {
          recoveryAttempted = true;
          failureRetryAttempted = true;
          recovery.discardSession();
          // The new backend session cannot resolve a card owned by the
          // expired session. Preserve the rider's message and location, but
          // let the fresh turn resolve its own authoritative context.
          activeRequest = {
            ...request,
            session_id: undefined,
            selected_card_id: undefined,
          };
          continue;
        }

        for (const pending of buffered) dispatch(pending);

        const dropped = !receivedDone && !controller.signal.aborted;
        const canRetryFailure =
          !controller.signal.aborted &&
          !sawRiderOutput &&
          !failureRetryAttempted &&
          (retryableFailure || dropped);
        if (canRetryFailure) {
          failureRetryAttempted = true;
          dispatch({ type: "turn_retry_started" });
          continue;
        }
        if (dropped) {
          dispatch({
            type: "stream_error",
            message: "The connection to SmartRoute dropped before it finished responding.",
          });
        }
        break;
      } catch (err) {
        if (controller.signal.aborted) {
          dispatch({ type: "stream_cancelled" });
          break;
        }
        const transportFailure =
          err instanceof AgentChatTransportError
            ? err
            : new AgentChatTransportError(
                "SmartRoute couldn’t complete this request.",
                500,
                true,
                null,
              );
        if (!failureRetryAttempted && !sawRiderOutput && transportFailure.retryable) {
          failureRetryAttempted = true;
          dispatch({ type: "turn_retry_started" });
          continue;
        }
        dispatch({
          type: "stream_error",
          message: transportFailure.message,
          code: `transport_${transportFailure.status}`,
          retryable: transportFailure.retryable,
          correlationId: transportFailure.correlationId ?? undefined,
        });
        break;
      }
    }
  } finally {
    inFlightRef.current = false;
    if (abortControllerRef.current === controller) abortControllerRef.current = null;
  }
}
