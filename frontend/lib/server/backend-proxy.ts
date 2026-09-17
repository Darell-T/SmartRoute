import { NextResponse, type NextRequest } from "next/server";
import type { ZodType } from "zod";
import {
  fetchBackendText,
  readJsonBody,
  resolveBackendBaseUrl,
} from "./backend-proxy-core";
import { rateLimit } from "./rate-limit";
import { requestPrincipal } from "./request-principal";

export { appendRequestSearch } from "./backend-proxy-core";

const backendBase = resolveBackendBaseUrl();

// Trip planning is the slowest backend call (real-time providers + advisor),
// so the default proxy budget is generous; faster routes can override.
const DEFAULT_TIMEOUT_MS = 65_000;

interface ProxyOptions {
  method?: "GET" | "POST";
  /** Parsed JSON body to forward. Omit for GET / body-less POST. */
  body?: unknown;
  timeoutMs?: number;
  cache?: RequestCache;
  /** Next.js fetch revalidation hint (e.g. { revalidate: 3600 }). */
  next?: { revalidate?: number };
}

function failedUpstreamResponse(aborted: boolean) {
  return NextResponse.json(
    { error: aborted ? "Upstream request timed out." : "Upstream request failed." },
    { status: aborted ? 504 : 502 },
  );
}

function jsonOrRedactedUpstream(raw: string, status: number) {
  try {
    return NextResponse.json(JSON.parse(raw), { status });
  } catch {
    // Backend returned non-JSON (e.g. a plain "Internal Server Error"). Do not
    // forward the raw body; return a clean, redacted error with a useful status.
    return NextResponse.json(
      { error: "Upstream returned an unexpected response." },
      { status: status >= 400 ? status : 502 },
    );
  }
}

function buildProxyHeaders(
  appKey: string,
  request: NextRequest | undefined,
  hasBody: boolean,
): Record<string, string> | NextResponse {
  const headers: Record<string, string> = { "X-App-Key": appKey };
  if (request) {
    const principal = requestPrincipal(request);
    if (!principal) {
      return NextResponse.json({ error: "Request identity is unavailable." }, { status: 503 });
    }
    headers["X-SmartRoute-Principal"] = principal;
  }
  if (hasBody) headers["Content-Type"] = "application/json";
  return headers;
}

/**
 * The single, safe way for a public Next route to reach the FastAPI backend.
 * Centralizes APP_KEY injection, request timeouts, non-JSON handling, upstream
 * status preservation, and error redaction so the route handlers stay thin and
 * never leak backend/provider details to the browser.
 */
export async function proxyToBackend(path: string, options: ProxyOptions = {}, request?: NextRequest) {
  const appKey = process.env.APP_KEY;
  if (!appKey) {
    return NextResponse.json(
      { error: "Server is not configured (missing APP_KEY)." },
      { status: 500 },
    );
  }

  const { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS, cache, next } = options;
  const headers = buildProxyHeaders(appKey, request, body !== undefined);
  if (headers instanceof NextResponse) return headers;

  const init: Omit<RequestInit, "signal"> = {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  };
  if (cache) {
    init.cache = cache;
  }
  if (next) {
    init.next = next;
  }
  const result = await fetchBackendText(
    `${backendBase}${path}`,
    init,
    timeoutMs,
  );

  if (!result.ok) return failedUpstreamResponse(result.aborted);
  if (!result.raw) return new NextResponse(null, { status: result.status });
  return jsonOrRedactedUpstream(result.raw, result.status);
}

interface PostProxyOptions<T> {
  /** FastAPI path to forward to, e.g. "/api/trip". */
  path: string;
  /** Rate-limit bucket key and per-minute ceiling for this route. */
  key: string;
  limit: number;
  /** Validates and narrows the JSON body before it reaches the backend. */
  schema?: ZodType<T>;
  cache?: RequestCache;
  /** Message returned on a 400; defaults to a generic one. */
  invalidMessage?: string;
}

/**
 * The standard shape for a paid POST proxy route: rate-limit, validate the JSON
 * body against `schema`, then forward via {@link proxyToBackend}. Centralizing it
 * keeps every provider-backed route on the same guarded path -- a new route
 * cannot forget the limiter or validation -- and the handlers down to one call.
 */
export async function postProxy<T>(
  req: NextRequest,
  opts: PostProxyOptions<T>,
): Promise<NextResponse> {
  const limited = rateLimit(req, { key: opts.key, limit: opts.limit, windowMs: 60_000 });
  if (limited) return limited;

  let body: unknown;
  if (opts.schema) {
    const jsonBody = await readJsonBody(req);
    if (!jsonBody.ok) {
      return NextResponse.json(
        { error: jsonBody.tooLarge ? "Request body is too large." : "Malformed JSON request body." },
        { status: jsonBody.tooLarge ? 413 : 400 },
      );
    }

    const parsed = opts.schema.safeParse(jsonBody.empty ? {} : jsonBody.value);
    if (!parsed.success) {
      return NextResponse.json(
        { error: opts.invalidMessage ?? "Invalid request." },
        { status: 400 },
      );
    }
    body = parsed.data;
  }
  return proxyToBackend(opts.path, { method: "POST", body, cache: opts.cache }, req);
}
