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

// The message is the only required chat input. Session/card/location metadata
// is optional and may be serialized as `null` by an older browser bundle or a
// location refresh. Normalize that metadata at the boundary instead of
// rejecting an otherwise valid rider message.
const ChatSchema = z
  .object({
    session_id: z.string().max(128).nullable().optional(),
    message: z.string().min(1).max(500),
    origin: z
      .object({
        lat: z.number(),
        lng: z.number(),
      })
      // Browser location objects can include display metadata such as
      // `accuracyMeters`. It is not part of the agent contract, so retain
      // the validated coordinates and discard any extra client metadata.
      .strip()
      .nullable()
      .optional(),
    selected_card_id: z.string().max(64).nullable().optional(),
    response_presentation: z.enum(["auto", "quick"]).default("auto"),
  })
  .strip()
  .transform((payload) => ({
    ...payload,
    session_id: payload.session_id ?? undefined,
    origin: payload.origin ?? undefined,
    selected_card_id: payload.selected_card_id ?? undefined,
  }));

export async function POST(req: NextRequest) {
  const limited = rateLimit(req, { key: "agent-chat", limit: 10, windowMs: 60_000 });
  if (limited) return limited;

  const jsonBody = await readJsonBody(req);
  if (!jsonBody.ok) {
    return NextResponse.json({ error: "Malformed JSON request body." }, { status: 400 });
  }

  const parsed = ChatSchema.safeParse(jsonBody.empty ? {} : jsonBody.value);
  if (!parsed.success) {
    const issues = parsed.error.issues.map((issue) => ({
      path: issue.path.join(".") || "body",
      code: issue.code,
      message: issue.message,
    }));
    console.warn("[agent-chat] rejected invalid request", issues);
    return NextResponse.json(
      {
        error:
          process.env.NODE_ENV === "development"
            ? `Invalid chat request: ${issues.map((issue) => `${issue.path} (${issue.message})`).join("; ")}`
            : "Invalid chat request.",
      },
      { status: 400 },
    );
  }

  return streamProxyToBackend("/api/agent/chat", parsed.data, req.signal);
}
