/**
 * Short-lived WebSocket auth ticket helpers.
 *
 * The backend WS routes (`/ws/live-feed`, `/ws/service-alerts`) authenticate a
 * signed, expiring ticket instead of a static key. The ticket is minted by the
 * Next `/api/ws-ticket` route, which runs server-side where APP_KEY lives, so
 * the backend key is never inlined into the browser bundle (as a
 * `NEXT_PUBLIC_*` value would be).
 */

/** Requests a fresh path-bound ticket from the server-side minting route. Throws on failure. */
export async function fetchWsTicket(
  path: "/ws/live-feed" | "/ws/service-alerts",
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(`/api/ws-ticket?path=${encodeURIComponent(path)}`, { cache: "no-store", signal });
  if (!res.ok) throw new Error(`ws-ticket request failed (${res.status})`);
  const data = (await res.json()) as { ticket?: string; ws_base_url?: string };
  if (!data.ticket) throw new Error("ws-ticket response missing ticket");
  if (data.ws_base_url) {
    serverWsBaseUrl = data.ws_base_url.replace(/\/+$/, "");
  }
  return data.ticket;
}

/**
 * Prod backend used when NEXT_PUBLIC_API_URL is absent from the build (e.g. a
 * Vercel deploy that didn't receive the env var). NEXT_PUBLIC_API_URL still wins
 * when it is set, so this never overrides an explicit configuration.
 */
const PROD_API_FALLBACK = "https://jarvis-mta-assistant.onrender.com";
let serverWsBaseUrl: string | null = null;

function isLocalBrowserHost(): boolean {
  if (typeof window === "undefined") return true;
  return /^(localhost|127\.0\.0\.1|0\.0\.0\.0)$/.test(window.location.hostname);
}

function isLocalBackendBase(base: string): boolean {
  try {
    const parsed = new URL(base);
    return /^(localhost|127\.0\.0\.1|0\.0\.0\.0)$/.test(parsed.hostname);
  } catch {
    return false;
  }
}

/** http(s):// base for the FastAPI backend. */
export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (configured && (isLocalBrowserHost() || !isLocalBackendBase(configured))) {
    return configured.replace(/\/+$/, "");
  }
  // No build-time env var: choose by host so a deployed site reaches the prod
  // backend instead of localhost, while local dev keeps using the local one.
  if (!isLocalBrowserHost()) {
    return PROD_API_FALLBACK;
  }
  return "http://localhost:8000";
}

/** ws(s):// base for the FastAPI backend. */
export function wsBaseUrl(): string {
  if (serverWsBaseUrl) return serverWsBaseUrl;
  const base = apiBaseUrl();
  return base.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
}

/** Builds the authenticated WS URL for `path` (e.g. "/ws/live-feed"). */
export function wsUrlWithTicket(path: string, ticket: string): string {
  return `${wsBaseUrl()}${path}?ticket=${encodeURIComponent(ticket)}`;
}
