import { z } from "zod";

import type { RouteCandidate, TripResponse } from "@/types/api";
import {
  alertSchema,
  canonicalItinerarySchema,
  routeStepSchema,
  type ValidatedCanonicalItinerary,
} from "./canonical-itinerary-schema";
import { boundedInteger } from "./schema-primitives";

export const TRIP_PLAN_FAILED = "Failed to plan trip";

const absentWhenNull = <Schema extends z.ZodTypeAny>(schema: Schema) =>
  z.preprocess((value) => (value === null ? undefined : value), schema);

export type ValidatedRouteCandidate = RouteCandidate & {
  total_minutes: number;
  itinerary: ValidatedCanonicalItinerary;
  score_breakdown: NonNullable<RouteCandidate["score_breakdown"]> & {
    transfers: number;
  };
};

export type ValidatedTripResponse = TripResponse & {
  selected_route_index: number;
  route_candidates: ValidatedRouteCandidate[];
};

const routeCandidateObjectSchema = z.object({
  id: z.string().min(1),
  index: boundedInteger(0, 64),
  steps: z.array(routeStepSchema),
  is_recommended: z.boolean(),
  total_minutes: boundedInteger(0, 1_440),
  itinerary: canonicalItinerarySchema,
  arrival_at: absentWhenNull(z.string().optional()),
  selection_score: z.number().finite().optional(),
  selection_rank: boundedInteger(0, 64).optional(),
  score_breakdown: z.object({
    duration_minutes: boundedInteger(0, 1_440).optional(),
    transfers: boundedInteger(0, 64),
    active_alerts: boundedInteger(0, 64).optional(),
    transit_lines: z.array(z.string()).optional(),
  }),
  enriched: z.boolean().optional(),
  can_enrich_on_select: z.boolean().optional(),
  recommendation_reason: absentWhenNull(z.string().optional()),
  rejection_reason: absentWhenNull(z.string().optional()),
  itinerary_id: z.string().optional(),
  origin: z
    .object({
      label: z.string(),
      lat: z.number(),
      lng: z.number(),
      name: z.string().optional(),
      address: z.string().nullable().optional(),
    })
    .optional(),
  destination: z
    .object({
      label: z.string(),
      lat: z.number(),
      lng: z.number(),
      name: z.string().optional(),
      address: z.string().nullable().optional(),
    })
    .optional(),
});

const tripResponseSchema = z
  .object({
    recommendation: z.string(),
    route: z.array(routeStepSchema),
    selected_route_index: boundedInteger(0, 64),
    route_candidates: z.array(routeCandidateObjectSchema).min(1),
    alerts: z.array(alertSchema),
  })
  .superRefine((response, context) => {
    if (
      !response.route_candidates.some(
        (candidate) => candidate.index === response.selected_route_index,
      )
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "selected_route_index does not match a candidate",
      });
    }
  });

export function parseTripResponse(value: unknown): ValidatedTripResponse {
  const parsed = tripResponseSchema.safeParse(value);
  if (!parsed.success) {
    throw new Error(TRIP_PLAN_FAILED);
  }
  const trip = parsed.data;
  return {
    recommendation: trip.recommendation,
    route: trip.route,
    selected_route_index: trip.selected_route_index,
    route_candidates: trip.route_candidates,
    alerts: trip.alerts,
  };
}
