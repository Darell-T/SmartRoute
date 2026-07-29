import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { postProxy } from "@/lib/backend-proxy";

const LatitudeSchema = z.number().finite().gte(40.2).lte(41.2);
const LongitudeSchema = z.number().finite().gte(-74.6).lte(-73.2);
const PointSchema = z.union([
  z.object({ lat: LatitudeSchema, lng: LongitudeSchema }).strict(),
  z.object({ latitude: LatitudeSchema, longitude: LongitudeSchema }).strict(),
]);
const StepSchema = z.object({
  type: z.enum(["WALK", "SUBWAY", "BUS"]),
  start_point: PointSchema.optional(), end_point: PointSchema.optional(),
  departure_coords: PointSchema.optional(), arrival_coords: PointSchema.optional(),
  route_id: z.string().max(300).optional(), departure_stop: z.string().max(300).optional(), arrival_stop: z.string().max(300).optional(),
  train_line: z.string().max(300).optional(), line_color: z.string().max(300).optional(), direction: z.string().max(300).optional(),
  minutes_until_train_arrives: z.number().finite().gte(-1440).lte(1440).optional(),
  minutes_until_arrival: z.number().finite().gte(-1440).lte(1440).optional(),
  route_total_minutes: z.number().finite().nonnegative().max(1440).optional(),
  route_total_seconds: z.number().finite().nonnegative().max(86_400).optional(),
  duration_minutes: z.number().finite().nonnegative().max(1440).optional(),
  distance_meters: z.number().finite().nonnegative().max(1_000_000).optional(),
  stop_count: z.number().int().nonnegative().max(256).optional(),
  segment_index: z.number().int().nonnegative().max(64).optional(),
  departure_time_iso: z.string().max(64).optional(), arrival_time_iso: z.string().max(64).optional(),
  intermediate_stops: z.array(z.string().max(300)).max(64).optional(),
  intermediate_stop_locations: z.array(z.object({ name: z.string().max(300), lat: LatitudeSchema, lng: LongitudeSchema }).strict()).max(64).optional(),
  polyline: z.object({ encodedPolyline: z.string().max(8192) }).strict().optional(),
}).strict();
export const EnrichRouteSchema = z.object({ steps: z.array(StepSchema).max(64).default([]) }).strict();

/** Browsers default to GET; opening the route in a tab hits this (not POST). */
export function GET() {
  return NextResponse.json(
    { error: "Use POST. This route proxies to the FastAPI POST /api/trip/enrich-route endpoint." },
    { status: 405, headers: { Allow: "POST" } },
  );
}

/** Backend exposes POST /api/trip/enrich-route {steps} -> {steps, enriched}. */
export function POST(request: NextRequest) {
  return postProxy(request, {
    path: "/api/trip/enrich-route",
    key: "enrich-route",
    limit: 60,
    schema: EnrichRouteSchema,
  });
}
