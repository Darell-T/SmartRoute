import { NextRequest } from "next/server";
import { appendRequestSearch, proxyToBackend } from "@/lib/backend-proxy";
import { rateLimit } from "@/lib/rate-limit";

export async function GET(req: NextRequest) {
  const limited = rateLimit(req, { key: "vehicles", limit: 120, windowMs: 60_000 });
  if (limited) return limited;
  return proxyToBackend(appendRequestSearch("/api/vehicles", req), {
    method: "GET",
    cache: "no-store",
  });
}
