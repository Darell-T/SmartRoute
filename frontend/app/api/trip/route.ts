import type { NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

const TripSchema = z.object({
  origin_lat: z.number(),
  origin_lng: z.number(),
  destination: z.string().min(1),
  destination_lat: z.number().nullish(),
  destination_lng: z.number().nullish(),
});

export function POST(req: NextRequest) {
  return postProxy(req, {
    path: "/api/trip",
    key: "trip",
    limit: 20,
    schema: TripSchema,
    invalidMessage: "Invalid trip request.",
  });
}
