import { NextResponse, type NextRequest } from "next/server";
import { postProxy } from "@/lib/backend-proxy";

/** Browsers default to GET; opening `/api/thinking` in a tab hits this (not POST). */
export function GET() {
  return NextResponse.json(
    { error: "Use POST. This route proxies to the FastAPI POST /api/thinking endpoint." },
    { status: 405, headers: { Allow: "POST" } },
  );
}

/** Backend exposes POST /api/thinking (matches `getThinking()` in lib/api.ts). */
export function POST(req: NextRequest) {
  return postProxy(req, { path: "/api/thinking", key: "thinking", limit: 30 });
}
