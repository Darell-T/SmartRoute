import type {
  DestinationSelection,
  RouteStep,
  TripResponse,
} from "@/types";

/** Empire State Building — demo fallback when GPS is unavailable */
export const DEFAULT_LOCATION = { lng: -73.9857, lat: 40.7484 } as const;

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

type PlanTripOptions = {
  signal?: AbortSignal;
};

function signalWithTimeout(
  timeoutMs: number,
  externalSignal?: AbortSignal,
): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  const abortFromExternal = () => {
    controller.abort(externalSignal?.reason);
  };

  if (externalSignal?.aborted) {
    abortFromExternal();
  } else {
    externalSignal?.addEventListener("abort", abortFromExternal, {
      once: true,
    });
  }

  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timeout);
      externalSignal?.removeEventListener("abort", abortFromExternal);
    },
  };
}

export async function planTrip(
  originLat: number,
  originLng: number,
  destination: string,
  selection?: DestinationSelection | null,
  options: PlanTripOptions = {},
): Promise<TripResponse> {
  async function attempt(): Promise<TripResponse> {
    const abort = signalWithTimeout(60_000, options.signal);
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
        signal: abort.signal,
      });

      if (!res.ok) {
        await res.body?.cancel();
        throw new Error(res.status === 503 ? "Service unavailable" : "Failed to plan trip");
      }
      return res.json();
    } finally {
      abort.cleanup();
    }
  }

  try {
    return await attempt();
  } catch (err) {
    if (options.signal?.aborted) throw err;
    const msg = err instanceof Error ? err.message : "";
    const isRetryable = msg === "Service unavailable" || msg.includes("abort") || msg === "Failed to fetch";
    if (isRetryable) {
      await new Promise((r) => setTimeout(r, 2000));
      return attempt();
    }
    throw err;
  }
}
