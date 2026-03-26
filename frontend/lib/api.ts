const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
  const res = await fetch(`${API_URL}/api/trip`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ origin_lat: originLat, origin_lng: originLng, destination }),
  });

  if (!res.ok) {
    const errorText = await res.text();
    console.error("Backend error:", res.status, errorText);
    throw new Error("Failed to plan trip");
  }
  return res.json();
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
