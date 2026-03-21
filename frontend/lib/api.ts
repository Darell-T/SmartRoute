const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function getThinkingAudio(): Promise<ArrayBuffer> {
  const res = await fetch(`${API_URL}/api/thinking`, {
    method: "POST",
  });

  if (!res.ok) throw new Error("Failed to get thinking audio");

  return res.arrayBuffer();
}

export interface ServiceAlert {
  header: string;
  routeIds: string[];
}

export interface TripResponse {
  text: string;
  audio: string;
  originCoords?: { lat: number; lng: number };
  destCoords?: { lat: number; lng: number };
  trainLine?: string;
  originStation?: { name: string; lat: number; lng: number };
  destStation?: { name: string; lat: number; lng: number };
  departureTimestamp?: number | null;
  direction?: string;
  rideDurationMinutes?: number | null;
  serviceAlerts?: ServiceAlert[];
}

export async function planTrip(
  origin: string,
  destination: string,
): Promise<TripResponse> {
  const res = await fetch(`${API_URL}/api/trip`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ origin, destination }),
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
