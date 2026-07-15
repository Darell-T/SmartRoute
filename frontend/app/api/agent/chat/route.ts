import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { readJsonBody } from "@/lib/backend-proxy-core";
import { streamProxyToBackend } from "@/lib/backend-stream-proxy";
import { rateLimit } from "@/lib/rate-limit";

// Node runtime (not edge): the backend proxy needs the standard fetch/stream
// primitives and a longer execution budget than the edge runtime allows.
export const runtime = "nodejs";
// Agent turns can run tens of seconds (multi-round tool use); this must stay
// under the platform's request ceiling -- see plan section 10 (Vercel
// maxDuration risk).
export const maxDuration = 90;
export const dynamic = "force-dynamic";

// Strict: an agent chat turn takes exactly this shape. Reject anything else
// rather than silently dropping unknown fields, so a client typo surfaces
// immediately instead of the field being ignored.
const ChatSchema = z
  .object({
    session_id: z.string().max(64).optional(),
    message: z.string().min(1).max(500),
    origin: z
      .object({
        lat: z.number(),
        lng: z.number(),
      })
      .strict()
      .optional(),
    selected_card_id: z.string().max(32).optional(),
  })
  .strict();

export async function POST(req: NextRequest) {
  const limited = rateLimit(req, { key: "agent-chat", limit: 10, windowMs: 60_000 });
  if (limited) return limited;

  const jsonBody = await readJsonBody(req);
  if (!jsonBody.ok) {
    return NextResponse.json({ error: "Malformed JSON request body." }, { status: 400 });
  }

  const parsed = ChatSchema.safeParse(jsonBody.empty ? {} : jsonBody.value);
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid chat request." }, { status: 400 });
  }

  return streamProxyToBackend("/api/agent/chat", parsed.data, req.signal);
}
