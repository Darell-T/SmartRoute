import type { NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

const Coordinate = z.number().finite();
const TripSchema = z.object({
  origin_lat: z.number().finite().gte(40.2).lte(41.2),
  origin_lng: z.number().finite().gte(-74.6).lte(-73.2),
  destination: z.string().trim().min(1).max(300),
  destination_lat: Coordinate.gte(40.2).lte(41.2).nullable().optional(),
  destination_lng: Coordinate.gte(-74.6).lte(-73.2).nullable().optional(),
}).strict().superRefine((value, context) => {
  if ((value.destination_lat == null) !== (value.destination_lng == null)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "Destination coordinates must be paired." });
  }
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
