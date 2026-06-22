import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

const EnrichRouteSchema = z.object({ steps: z.array(z.unknown()).default([]) });

/** Browsers default to GET; opening the route in a tab hits this (not POST). */
export function GET() {
  return NextResponse.json(
    { error: "Use POST. This route proxies to the FastAPI POST /api/trip/enrich-route endpoint." },
    { status: 405, headers: { Allow: "POST" } },
  );
}

/** Backend exposes POST /api/trip/enrich-route {steps} -> {steps, enriched}. */
export function POST(request: NextRequest) {
  return postProxy(request, {
    path: "/api/trip/enrich-route",
    key: "enrich-route",
    limit: 60,
    schema: EnrichRouteSchema,
  });
}
