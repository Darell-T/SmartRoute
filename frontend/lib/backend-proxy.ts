import { NextResponse, type NextRequest } from "next/server";
import type { ZodType } from "zod";
import {
  appendRequestSearch,
  fetchBackendText,
  readJsonBody,
} from "./backend-proxy-core";
import { rateLimit } from "./rate-limit";

export { appendRequestSearch } from "./backend-proxy-core";

// API_URL / NEXT_PUBLIC_API_URL win when set. Otherwise fall back by
// environment so a Vercel deploy that is missing the env var still reaches the
// prod backend instead of localhost (local dev keeps using localhost).
const PROD_API_FALLBACK = "https://jarvis-mta-assistant.onrender.com";
const backendBase =
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.VERCEL ? PROD_API_FALLBACK : "http://localhost:8000");

// Trip planning is the slowest backend call (real-time providers + advisor +
// TTS), so the default proxy budget is generous; faster routes can override.
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

/**
 * The single, safe way for a public Next route to reach the FastAPI backend.
 * Centralizes APP_KEY injection, request timeouts, non-JSON handling, upstream
 * status preservation, and error redaction so the route handlers stay thin and
 * never leak backend/provider details to the browser.
 */
export async function proxyToBackend(path: string, options: ProxyOptions = {}) {
  const appKey = process.env.APP_KEY;
  if (!appKey) {
    // Operator misconfiguration, not a data leak: APP_KEY must match FastAPI's.
    return NextResponse.json(
      { error: "Server is not configured (missing APP_KEY)." },
      { status: 500 },
    );
  }

  const { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS, cache, next } = options;
  const headers: Record<string, string> = { "X-App-Key": appKey };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const result = await fetchBackendText(
    `${backendBase}${path}`,
    {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      ...(cache ? { cache } : {}),
      ...(next ? { next } : {}),
    },
    timeoutMs,
  );

  if (!result.ok) {
    return NextResponse.json(
      { error: result.aborted ? "Upstream request timed out." : "Upstream request failed." },
      { status: result.aborted ? 504 : 502 },
    );
  }

  if (!result.raw) {
    return new NextResponse(null, { status: result.status });
  }
  try {
    return NextResponse.json(JSON.parse(result.raw), { status: result.status });
  } catch {
    // Backend returned non-JSON (e.g. a plain "Internal Server Error"). Do not
    // forward the raw body; return a clean, redacted error with a useful status.
    return NextResponse.json(
      { error: "Upstream returned an unexpected response." },
      { status: result.status >= 400 ? result.status : 502 },
    );
  }
}

interface PostProxyOptions<T> {
  /** FastAPI path to forward to, e.g. "/api/trip". */
  path: string;
  /** Rate-limit bucket key and per-minute ceiling for this route. */
  key: string;
  limit: number;
  /** Validates and narrows the JSON body before it reaches the backend. Omit for
   *  body-less POSTs (e.g. /api/thinking). */
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
        { error: "Malformed JSON request body." },
        { status: 400 },
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
  return proxyToBackend(opts.path, { method: "POST", body, cache: opts.cache });
}
