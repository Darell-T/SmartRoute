import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { requestPrincipal } from "./request-principal";

// Mirrors the backendBase resolution in backend-proxy.ts (kept private there,
// so duplicated rather than imported to avoid coupling two proxy modules that
// may be edited concurrently). API_URL / NEXT_PUBLIC_API_URL win when set;
// otherwise fall back by environment so a Vercel deploy missing the env var
// still reaches the prod backend instead of localhost.
const PROD_API_FALLBACK = "https://jarvis-mta-assistant.onrender.com";
const backendBase =
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.VERCEL ? PROD_API_FALLBACK : "http://localhost:8000");

// Guards against a backend that never responds at all (dead host, hung
// listener). Once headers arrive this timer is cleared -- the stream itself
// has no timeout because agent turns can legitimately run tens of seconds.
const CONNECT_TIMEOUT_MS = 10_000;

function redactedError(message: string, status: number): NextResponse {
  return NextResponse.json({ error: message }, { status });
}

/**
 * Streaming counterpart to `proxyToBackend` (backend-proxy.ts): that helper
 * buffers the full upstream response before returning it, which is wrong for
 * a Server-Sent Events turn where the browser needs tokens as the model
 * produces them. This pipes `upstream.body` straight through instead, while
 * keeping the same guarantees callers already rely on: APP_KEY is injected
 * server-side and never reaches the browser, and error responses are always
 * redacted, generic JSON -- raw upstream bodies (which may carry provider or
 * internal detail) are never forwarded to the client on a failure path.
 *
 * `signal` should be the incoming request's AbortSignal. It is wired to the
 * upstream fetch so a closed tab / navigated-away client cancels the backend
 * turn instead of letting it run to completion unattended.
 */
export async function streamProxyToBackend(
  path: string,
  body: unknown,
  signal?: AbortSignal,
  request?: NextRequest,
): Promise<Response> {
  const appKey = process.env.APP_KEY;
  if (!appKey) {
    // Operator misconfiguration, not a data leak: APP_KEY must match FastAPI's.
    return redactedError("Server is not configured (missing APP_KEY).", 500);
  }

  const controller = new AbortController();
  const principal = request ? requestPrincipal(request) : null;
  if (request && !principal) return redactedError("Request identity is unavailable.", 503);
  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort);
  }

  const connectTimer = setTimeout(() => controller.abort(), CONNECT_TIMEOUT_MS);
  let upstream: Response;
  try {
    upstream = await fetch(`${backendBase}${path}`, {
      method: "POST",
      headers: {
        "X-App-Key": appKey,
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
    return redactedError("Upstream request failed.", 502);
  }
  clearTimeout(connectTimer);

  if (!upstream.ok) {
    // Never forward the raw upstream error body -- pass the status through
    // (so rate-limit/validation-style codes still make sense to the client)
    // but replace the payload with a generic, redacted message.
    await upstream.body?.cancel().catch(() => {});
    if (signal) signal.removeEventListener("abort", onAbort);
    return redactedError(
      "Upstream request failed.",
      upstream.status >= 400 ? upstream.status : 502,
    );
  }

  // Deliberately keep `onAbort` attached for the life of this streamed
  // response: the same AbortSignal backs both the fetch and the body read,
  // so a later tab-close still needs to reach `controller` to cancel the
  // in-flight backend turn.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
