import { NextResponse, type NextRequest } from "next/server";
import { createHmac } from "node:crypto";
import { rateLimit } from "@/lib/rate-limit";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Short enough that a leaked ticket is useless almost immediately, long enough
// to cover a connect plus an immediate reconnect.
const TICKET_TTL_S = 90;
const ALLOWED_WS_PATHS = new Set(["/ws/live-feed", "/ws/service-alerts"]);
const PROD_API_FALLBACK = "https://jarvis-mta-assistant.onrender.com";

function isLocalBackendBase(base: string): boolean {
  try {
    const parsed = new URL(base);
    return /^(localhost|127\.0\.0\.1|0\.0\.0\.0)$/.test(parsed.hostname);
  } catch {
    return false;
  }
}

function backendBaseUrl(): string {
  const configured = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL;
  if (configured && !(process.env.VERCEL && isLocalBackendBase(configured))) {
    return configured.replace(/\/+$/, "");
  }
  return process.env.VERCEL ? PROD_API_FALLBACK : "http://localhost:8000";
}

function websocketBaseUrl(): string {
  return backendBaseUrl().replace(/^https:/, "wss:").replace(/^http:/, "ws:");
}

/**
 * Mints a short-lived HMAC ticket so the browser can open the FastAPI
 * WebSocket without ever seeing APP_KEY. The backend recomputes the same HMAC
 * (it shares APP_KEY) and checks the expiry and requested path -- see
 * `_verify_ws_ticket` in backend/app/routers/live_feed.py. This replaces
 * NEXT_PUBLIC_APP_KEY, which would otherwise inline the backend key into the
 * client bundle.
 */
export function GET(req: NextRequest) {
  const limited = rateLimit(req, { key: "ws-ticket", limit: 240, windowMs: 60_000 });
  if (limited) return limited;

  const appKey = process.env.APP_KEY;
  if (!appKey) {
    return NextResponse.json(
      { error: "Server is not configured (missing APP_KEY)." },
      { status: 500 },
    );
  }

  const path = req.nextUrl.searchParams.get("path") ?? "";
  if (!ALLOWED_WS_PATHS.has(path)) {
    return NextResponse.json(
      { error: "Unsupported WebSocket path." },
      { status: 400 },
    );
  }

  const exp = Math.floor(Date.now() / 1000) + TICKET_TTL_S;
  const sig = createHmac("sha256", appKey).update(`${exp}.${path}`).digest("hex");
  return NextResponse.json(
    {
      ticket: `${exp}.${sig}`,
      ws_base_url: websocketBaseUrl(),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
