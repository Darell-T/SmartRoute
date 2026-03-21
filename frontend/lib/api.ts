const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export async function getThinkingAudio(): Promise<ArrayBuffer> {
  const res = await fetch(`${API_URL}/api/thinking`, {
    method: "POST",
  });

  if (!res.ok) throw new Error("Failed to get thinking audio");

  return res.arrayBuffer();
}

export async function planTrip(
  origin: string,
  destination: string,
): Promise<{
  text: string;
  audio: string;
}> {
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
