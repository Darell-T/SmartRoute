const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Empire State Building — demo fallback when GPS is unavailable */
export const DEFAULT_LOCATION = { lng: -73.9857, lat: 40.7484 } as const;

export interface ThinkingResponse {
  text: string;
  audio: string; // base64-encoded audio
}

export async function getThinking(): Promise<ThinkingResponse> {
  const res = await fetch(`${API_URL}/api/thinking`, {
    method: "POST",
  });

  if (!res.ok) throw new Error("Failed to get thinking audio");

  return res.json();
}

export interface ServiceAlert {
  header: string;
  routeIds: string[];
}

export interface RouteStep {
  type: "WALK" | "SUBWAY" | "BUS";
  // Walk fields
  start_point?: { latitude: number; longitude: number };
  end_point?: { latitude: number; longitude: number };
  polyline?: { encodedPolyline: string };
  // Transit fields
  train_line?: string;
  line_color?: string;
  direction?: string;
  departure_stop?: string;
  arrival_stop?: string;
  departure_coords?: { latitude: number; longitude: number };
  arrival_coords?: { latitude: number; longitude: number };
  minutes_until_train_arrives?: number;
  minutes_until_arrival?: number;
  stop_count?: number;
  route_id?: string;
  intermediate_stops?: string[];
}

export interface TripResponse {
  recommendation: string;
  audio: string;
  route: RouteStep[];
  alerts: ServiceAlert[];
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
      const res = await fetch(`${API_URL}/api/trip`, {
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

export interface HealthResponse {
  status: string;
  gtfs_ready: boolean;
  gtfs_error: string | null;
}

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_URL}/health`);
  if (!res.ok) throw new Error("Health check failed");
  return res.json();
}
