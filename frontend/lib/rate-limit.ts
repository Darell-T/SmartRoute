import { NextRequest, NextResponse } from "next/server";

// Best-effort in-memory sliding-window limiter for the public proxy routes. It
// protects the paid/provider-backed endpoints (route planning, narration, TTS)
// from a single abusive client. NOTE: this is per-instance memory; a multi-
// instance/serverless deployment that needs a hard global limit should back this
// with a shared store (e.g. Upstash/Redis). For a single backend it is effective.
interface Bucket {
  count: number;
  resetAt: number;
}

const buckets = new Map<string, Bucket>();
let lastSweep = 0;

function sweep(now: number) {
  if (now - lastSweep < 60_000) return;
  lastSweep = now;
  for (const [key, bucket] of buckets) {
    if (now >= bucket.resetAt) buckets.delete(key);
  }
}

function clientIp(req: NextRequest): string {
  const forwarded = req.headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return req.headers.get("x-real-ip") ?? "unknown";
}

/**
 * Returns a 429 NextResponse when the caller has exceeded `limit` requests in
 * `windowMs` for the given route `key`, or null when the request may proceed.
 */
export function rateLimit(
  req: NextRequest,
  opts: { key: string; limit: number; windowMs: number },
): NextResponse | null {
  const now = Date.now();
  sweep(now);

  const id = `${opts.key}:${clientIp(req)}`;
  const bucket = buckets.get(id);
  if (!bucket || now >= bucket.resetAt) {
    buckets.set(id, { count: 1, resetAt: now + opts.windowMs });
    return null;
  }
  if (bucket.count >= opts.limit) {
    const retryAfter = Math.ceil((bucket.resetAt - now) / 1000);
    return NextResponse.json(
      { error: "Too many requests. Please slow down." },
      { status: 429, headers: { "Retry-After": String(retryAfter) } },
    );
  }
  bucket.count += 1;
  return null;
}
