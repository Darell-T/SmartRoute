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
  const data = (await res.json()) as { ticket?: string };
  if (!data.ticket) throw new Error("ws-ticket response missing ticket");
  return data.ticket;
}

/** ws(s):// base for the FastAPI backend, derived from NEXT_PUBLIC_API_URL. */
export function wsBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return base.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
}

/** Builds the authenticated WS URL for `path` (e.g. "/ws/live-feed"). */
export function wsUrlWithTicket(path: string, ticket: string): string {
  return `${wsBaseUrl()}${path}?ticket=${encodeURIComponent(ticket)}`;
}
