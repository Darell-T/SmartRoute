import type { Dispatch } from "react";
import { parseSseStream, type AgentEvent } from "./agent-chat-stream";
import type { AgentChatRequestBody } from "./agent-chat-request";
import type { ChatReducerAction } from "./agent-chat-state";

const CHAT_ENDPOINT = "/api/agent/chat";

export type AgentChatTransport = (
  request: AgentChatRequestBody,
  signal: AbortSignal,
) => AsyncGenerator<AgentEvent>;

export interface SessionRecoveryOptions {
  canRecoverSession: boolean;
  discardSession: () => void;
}

interface MutableRef<T> {
  current: T;
}

function hasErrorMessage(body: unknown): body is { error: string } {
  return Boolean(body) && typeof body === "object" &&
    typeof Object.getOwnPropertyDescriptor(body, "error")?.value === "string";
}

async function readTransportErrorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (hasErrorMessage(body)) return body.error;
  } catch {
    // Non-JSON (or empty) transport failures use the status fallback.
  }
  return `SmartRoute is unavailable right now (${res.status}).`;
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

  if (!res.ok || !res.body) throw new Error(await readTransportErrorMessage(res));
  yield* parseSseStream(res.body.getReader());
}

export async function runTurn(
  transport: AgentChatTransport,
  request: AgentChatRequestBody,
  controller: AbortController,
  dispatch: Dispatch<ChatReducerAction>,
  inFlightRef: MutableRef<boolean>,
  abortControllerRef: MutableRef<AbortController | null>,
  recovery: SessionRecoveryOptions = {
    canRecoverSession: false,
    discardSession: () => undefined,
  },
): Promise<void> {
  let receivedDone = false;
  let recoveryAttempted = false;
  let activeRequest = request;
  try {
    while (true) {
      const buffered: AgentEvent[] = [];
      let sessionExpired = false;
      receivedDone = false;

      for await (const event of transport(activeRequest, controller.signal)) {
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
        sessionExpired && recovery.canRecoverSession && !recoveryAttempted && !controller.signal.aborted
      ) {
        recoveryAttempted = true;
        recovery.discardSession();
        activeRequest = { ...request, session_id: undefined };
        continue;
      }

      for (const pending of buffered) dispatch(pending);
      if (!receivedDone && !controller.signal.aborted) {
        dispatch({
          type: "stream_error",
          message: "The connection to SmartRoute dropped before it finished responding.",
        });
      }
      break;
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
