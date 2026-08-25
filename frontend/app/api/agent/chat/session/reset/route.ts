import type { NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SessionResetSchema = z
  .object({ session_id: z.string().min(1).max(128) })
  .strict();

export function POST(req: NextRequest) {
  return postProxy(req, {
    path: "/api/agent/chat/session/reset",
    key: "agent-chat-session-reset",
    limit: 10,
    schema: SessionResetSchema,
    invalidMessage: "Invalid session reset request.",
  });
}
