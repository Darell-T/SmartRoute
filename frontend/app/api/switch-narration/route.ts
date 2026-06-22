import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

const SwitchNarrationSchema = z.object({ route_id: z.string().min(1) });

/** Browsers default to GET; opening the route in a tab hits this (not POST). */
export function GET() {
  return NextResponse.json(
    { error: "Use POST. This route proxies to the FastAPI POST /api/switch-narration endpoint." },
    { status: 405, headers: { Allow: "POST" } },
  );
}

/** Backend exposes POST /api/switch-narration {route_id} -> {text, audio}. */
export function POST(request: NextRequest) {
  return postProxy(request, {
    path: "/api/switch-narration",
    key: "switch-narration",
    limit: 30,
    schema: SwitchNarrationSchema,
  });
}
