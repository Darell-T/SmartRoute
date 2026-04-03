import type { ThinkingResponse, TripResponse, HealthResponse } from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Empire State Building — demo fallback when GPS is unavailable */
export const DEFAULT_LOCATION = { lng: -73.9857, lat: 40.7484 } as const;

export async function getThinking(): Promise<ThinkingResponse> {
  const res = await fetch(`/api/thinking`, {
    method: "POST",
  });

  if (!res.ok) throw new Error("Failed to get thinking audio");

  return res.json();
}

export async function planTrip(
  originLat: number,
  originLng: number,
  destination: string,
): Promise<TripResponse> {
  async function attempt(): Promise<TripResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60_000);
    try {
      const res = await fetch("/api/trip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ origin_lat: originLat, origin_lng: originLng, destination }),
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

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_URL}/health`);
  if (!res.ok) throw new Error("Health check failed");
  return res.json();
}
