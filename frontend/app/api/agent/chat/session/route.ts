import type { NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

// Node runtime keeps parity with the chat proxy (fetch + standard primitives).
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SessionSnapshotSchema = z
  .object({
    session_id: z.string().min(1).max(128),
  })
  .strict();

/**
 * Read-only transcript restore for page-refresh continuity. The backend
 * returns the rider-visible conversation plus canonical route-card payloads,
 * or a stable 404 when the session expired -- the client then discards its
 * persisted session id.
 */
export function POST(req: NextRequest) {
  return postProxy(req, {
    path: "/api/agent/chat/session",
    key: "agent-chat-session",
    limit: 10,
    schema: SessionSnapshotSchema,
    invalidMessage: "Invalid session restore request.",
  });
}
