import { proxyToBackend } from "@/lib/backend-proxy";

// Stops are static-ish — let the edge cache hold them for an hour to match
// the backend cache TTL.
export const revalidate = 3600;

export async function GET() {
  return proxyToBackend("/api/subway-stops", { method: "GET", next: { revalidate: 3600 } });
}
