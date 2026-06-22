import type { NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

const LiveFeedSchema = z.object({ lat: z.number(), lng: z.number() });

export function POST(req: NextRequest) {
  return postProxy(req, {
    path: "/api/live-feed",
    key: "live-feed",
    limit: 120,
    schema: LiveFeedSchema,
    cache: "no-store",
  });
}
