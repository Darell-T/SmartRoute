import type {
  DestinationSelection,
  RouteStep,
  SwitchNarrationResponse,
  ThinkingResponse,
  TripResponse,
} from "@/types";

/** Empire State Building — demo fallback when GPS is unavailable */
export const DEFAULT_LOCATION = { lng: -73.9857, lat: 40.7484 } as const;

export async function getThinking(): Promise<ThinkingResponse> {
  const res = await fetch(`/api/thinking`, {
    method: "POST",
  });

  if (!res.ok) throw new Error("Failed to get thinking audio");

  return res.json();
}

/** Short cached route line for switching to an alternative route. */
export async function getSwitchNarration(routeId: string): Promise<SwitchNarrationResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  try {
    const res = await fetch("/api/switch-narration", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route_id: routeId }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error("Failed to get switch narration");
    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}

/** Lazily enrich an alternate route's intermediate stops when it is selected.
 *  The initial trip response only enriches the chosen route; alternates carry
 *  can_enrich_on_select=true and call this on demand. */
export async function enrichRoute(
  steps: RouteStep[],
): Promise<{ steps: RouteStep[]; enriched: boolean }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  try {
    const res = await fetch("/api/trip/enrich-route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ steps }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error("Failed to enrich route");
    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}

export async function planTrip(
  originLat: number,
  originLng: number,
  destination: string,
  selection?: DestinationSelection | null,
): Promise<TripResponse> {
  async function attempt(): Promise<TripResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60_000);
    try {
      const res = await fetch("/api/trip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          origin_lat: originLat,
          origin_lng: originLng,
          destination,
          destination_lat: selection?.coordinates.lat,
          destination_lng: selection?.coordinates.lng,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errorText = await res.text();
        console.error("Backend error:", res.status, errorText);
        throw new Error(res.status === 503 ? "Service unavailable" : "Failed to plan trip");
      }
      return res.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  try {
    return await attempt();
  } catch (err) {
    const msg = err instanceof Error ? err.message : "";
    const isRetryable = msg === "Service unavailable" || msg.includes("abort") || msg === "Failed to fetch";
    if (isRetryable) {
      console.log("[api] first attempt failed, retrying in 2s…", msg);
      await new Promise((r) => setTimeout(r, 2000));
      return attempt();
    }
    throw err;
  }
}
