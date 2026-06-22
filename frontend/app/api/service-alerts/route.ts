import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/backend-proxy";
import { rateLimit } from "@/lib/rate-limit";

export async function GET(req: NextRequest) {
  const limited = rateLimit(req, { key: "service-alerts", limit: 120, windowMs: 60_000 });
  if (limited) return limited;
  return proxyToBackend("/api/service-alerts", { method: "GET", cache: "no-store" });
}
