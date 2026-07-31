import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { safeChatFailure } from "./chat-failure-copy";
import { requestPrincipal } from "./request-principal";

const PROD_API_FALLBACK = "https://jarvis-mta-assistant.onrender.com";
const backendBase =
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.VERCEL ? PROD_API_FALLBACK : "http://localhost:8000");
const backendHost = safeBackendHost(backendBase);
const CONNECT_TIMEOUT_MS = 10_000;
const REQUEST_ID_HEADER = "X-SmartRoute-Request-Id";

type FailurePhase = "connect" | "upstream_status" | "stream";
type AbortSource = "client" | "connect_timeout" | "unknown";

interface ProxyFailureDetails {
  event: "agent_chat_proxy_failure";
  correlationId: string;
  backendHost: string;
  path: string;
  failurePhase: FailurePhase;
  upstreamStatus?: number;
  elapsedMs: number;
  abortSource?: AbortSource;
  deploymentEnvironment?: string;
}

function safeBackendHost(value: string): string {
  try {
    return new URL(value).host || "invalid-backend-url";
  } catch {
    return "invalid-backend-url";
  }
}

export function classifyProxyAbort(
  connectTimedOut: boolean,
  clientAborted: boolean,
): AbortSource {
  if (connectTimedOut) return "connect_timeout";
  if (clientAborted) return "client";
  return "unknown";
}

function proxyError(status: number, correlationId: string): NextResponse {
  const failure = safeChatFailure(status);
  return NextResponse.json(
    { error: failure.message, retryable: failure.retryable },
    { status, headers: { [REQUEST_ID_HEADER]: correlationId } },
  );
}

function logFailure(details: ProxyFailureDetails): void {
  // Keep the object deliberately closed: no request body, prompt, session,
  // credentials, upstream response body, cookies, or authorization headers.
  // eslint-disable-next-line no-console -- this is the server diagnostic boundary
  console.error("[agent-chat-proxy]", JSON.stringify(details));
}

function failureDetails(
  correlationId: string,
  path: string,
  failurePhase: FailurePhase,
  startedAt: number,
  extras: Pick<ProxyFailureDetails, "upstreamStatus" | "abortSource"> = {},
): ProxyFailureDetails {
  return {
    event: "agent_chat_proxy_failure",
    correlationId,
    backendHost,
    path: new URL(path, "https://smartroute.invalid").pathname,
    failurePhase,
    ...extras,
    elapsedMs: Math.max(0, Math.round(performance.now() - startedAt)),
    deploymentEnvironment: process.env.VERCEL_ENV ?? process.env.NODE_ENV,
  };
}

function instrumentStream(
  body: ReadableStream<Uint8Array>,
  correlationId: string,
  path: string,
  startedAt: number,
  upstreamController: AbortController,
  requestSignal: AbortSignal | undefined,
  onAbort: () => void,
  clientAborted: () => boolean,
): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let finished = false;

  const cleanup = () => {
    if (finished) return;
    finished = true;
    requestSignal?.removeEventListener("abort", onAbort);
  };

  return new ReadableStream<Uint8Array>({
    async pull(output) {
      try {
        const chunk = await reader.read();
        if (chunk.done) {
          cleanup();
          output.close();
          return;
        }
        output.enqueue(chunk.value);
      } catch {
        cleanup();
        logFailure(
          failureDetails(correlationId, path, "stream", startedAt, {
            abortSource: clientAborted() ? "client" : "unknown",
          }),
        );
        output.error(new Error("SmartRoute stream ended unexpectedly."));
      }
    },
    async cancel(reason) {
      cleanup();
      upstreamController.abort();
      await reader.cancel(reason).catch(() => undefined);
    },
  });
}

/**
 * Streams FastAPI SSE without buffering while keeping credentials and raw
 * upstream failures at the server boundary.
 */
export async function streamProxyToBackend(
  path: string,
  body: unknown,
  signal?: AbortSignal,
  request?: NextRequest,
): Promise<Response> {
  const correlationId = randomUUID();
  const startedAt = performance.now();
  const appKey = process.env.APP_KEY;
  if (!appKey) {
    logFailure(
      failureDetails(correlationId, path, "connect", startedAt, {
        upstreamStatus: 500,
        abortSource: "unknown",
      }),
    );
    return proxyError(500, correlationId);
  }

  const controller = new AbortController();
  const principal = request ? requestPrincipal(request) : null;
  if (request && !principal) {
    logFailure(
      failureDetails(correlationId, path, "connect", startedAt, {
        upstreamStatus: 503,
        abortSource: "unknown",
      }),
    );
    return proxyError(503, correlationId);
  }

  let clientAborted = signal?.aborted ?? false;
  let connectTimedOut = false;
  const onAbort = () => {
    clientAborted = true;
    controller.abort();
  };
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }

  const connectTimer = setTimeout(() => {
    connectTimedOut = true;
    controller.abort();
  }, CONNECT_TIMEOUT_MS);

  let upstream: Response;
  try {
    upstream = await fetch(`${backendBase}${path}`, {
      method: "POST",
      headers: {
        "X-App-Key": appKey,
        [REQUEST_ID_HEADER]: correlationId,
        ...(principal ? { "X-SmartRoute-Principal": principal } : {}),
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      cache: "no-store",
      signal: controller.signal,
    });
  } catch {
    clearTimeout(connectTimer);
    if (signal) signal.removeEventListener("abort", onAbort);
    const abortSource = classifyProxyAbort(connectTimedOut, clientAborted);
    logFailure(
      failureDetails(correlationId, path, "connect", startedAt, { abortSource }),
    );
    return proxyError(502, correlationId);
  }
  clearTimeout(connectTimer);

  if (!upstream.ok) {
    await upstream.body?.cancel().catch(() => undefined);
    if (signal) signal.removeEventListener("abort", onAbort);
    const status = upstream.status >= 400 ? upstream.status : 502;
    logFailure(
      failureDetails(correlationId, path, "upstream_status", startedAt, {
        upstreamStatus: status,
      }),
    );
    return proxyError(status, correlationId);
  }

  const responseBody = upstream.body
    ? instrumentStream(
        upstream.body,
        correlationId,
        path,
        startedAt,
        controller,
        signal,
        onAbort,
        () => clientAborted,
      )
    : null;

  return new Response(responseBody, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
      [REQUEST_ID_HEADER]: correlationId,
    },
  });
}
