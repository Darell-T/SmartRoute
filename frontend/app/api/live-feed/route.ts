import type { NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

const LiveFeedSchema = z.object({
  lat: z.number().finite().gte(40.2).lte(41.2),
  lng: z.number().finite().gte(-74.6).lte(-73.2),
}).strict();

export function POST(req: NextRequest) {
  return postProxy(req, {
    path: "/api/live-feed",
    key: "live-feed",
    limit: 120,
    schema: LiveFeedSchema,
    cache: "no-store",
  });
}
