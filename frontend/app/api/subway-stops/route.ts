import { proxyToBackend } from "@/lib/backend-proxy";

// Stops are static-ish — let the edge cache hold them for an hour to match
// the backend cache TTL.
// The handler itself must stay dynamic: a build must never depend on the live
// backend being reachable. The proxied fetch retains its one-hour cache.
export const dynamic = "force-dynamic";
export const revalidate = 3600;

export async function GET() {
  return proxyToBackend("/api/subway-stops", { method: "GET", next: { revalidate: 3600 } });
}
