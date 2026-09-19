import { z } from "zod";

import type {
  CanonicalItinerary,
  CanonicalItineraryPlace,
  RouteSelectionDecision,
} from "./agent-chat/route-card-contract";
import { MAX_LIST, boundedInteger, boundedNumber, limitedText, nonEmptyText, nonEmptyTextList } from "./schema-primitives";

const MAX_SECONDS = 86_400;

const MAX_ALERT_DESCRIPTION = 16_384;

type Coordinate = { latitude: number; longitude: number };

const fullCoordinateSchema = z
  .object({
    latitude: boundedNumber(40.2, 41.2),
    longitude: boundedNumber(-74.6, -73.2),
  });

const compactCoordinateSchema = z
  .object({
    lat: boundedNumber(40.2, 41.2),
    lng: boundedNumber(-74.6, -73.2),
  })
  .transform(
    (coordinate): Coordinate => ({
      latitude: coordinate.lat,
      longitude: coordinate.lng,
    }),
  );

const coordinateSchema = z.union([fullCoordinateSchema, compactCoordinateSchema]);

const intermediateStopSchema = z.object({
  name: nonEmptyText(),
  lat: boundedNumber(40.2, 41.2),
  lng: boundedNumber(-74.6, -73.2),
});

const providerRouteTypes = {
  COMMUTER_RAIL: "RAIL", HEAVY_RAIL: "RAIL", METRO_RAIL: "RAIL",
  METRO: "RAIL", HIGH_SPEED_TRAIN: "RAIL", LONG_DISTANCE_TRAIN: "RAIL", FERRY: "RAIL",
  INTERCITY_BUS: "BUS", TROLLEYBUS: "BUS", SHARE_TAXI: "BUS",
  CABLE_CAR: "TRAM", FUNICULAR: "TRAM", GONDOLA: "TRAM", MONORAIL: "TRAM",
} as const;

const routeTypeSchema = z.union([
  z.enum(["WALK", "SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL", "TRAM"]),
  z.enum([
    "COMMUTER_RAIL", "HEAVY_RAIL", "METRO_RAIL", "METRO", "HIGH_SPEED_TRAIN",
    "LONG_DISTANCE_TRAIN", "FERRY", "INTERCITY_BUS", "TROLLEYBUS", "SHARE_TAXI",
    "CABLE_CAR", "FUNICULAR", "GONDOLA", "MONORAIL",
  ]).transform((type) => providerRouteTypes[type]),
]);

export const routeStepSchema = z.object({
  type: routeTypeSchema,
  start_point: coordinateSchema.optional(),
  end_point: coordinateSchema.optional(),
  departure_coords: coordinateSchema.optional(),
  arrival_coords: coordinateSchema.optional(),
  polyline: z.object({ encodedPolyline: nonEmptyText(65_536) }).optional(),
  train_line: limitedText().optional(),
  line_color: limitedText().optional(),
  direction: limitedText().optional(),
  departure_stop: limitedText().optional(),
  arrival_stop: limitedText().optional(),
  route_id: limitedText().optional(),
  departure_time_iso: limitedText(64).optional(),
  arrival_time_iso: limitedText(64).optional(),
  minutes_until_train_arrives: boundedNumber(-1_440, 1_440).optional(),
  minutes_until_arrival: boundedNumber(-1_440, 1_440).optional(),
  route_total_minutes: boundedNumber(0, 1_440).optional(),
  route_total_seconds: boundedNumber(0, MAX_SECONDS).optional(),
  duration_minutes: boundedNumber(0, 1_440).optional(),
  distance_meters: boundedNumber(0, 1_000_000).optional(),
  stop_count: boundedInteger(0, 256).optional(),
  segment_index: boundedInteger(0, 64).optional(),
  intermediate_stops: nonEmptyTextList(MAX_LIST).optional(),
  intermediate_stop_locations: z.array(intermediateStopSchema).max(MAX_LIST).optional(),
});

export const alertSchema = z.object({
  header: nonEmptyText(480),
  description: limitedText(MAX_ALERT_DESCRIPTION).optional(),
  routeIds: nonEmptyTextList(64).optional(),
  route_ids: nonEmptyTextList(64).optional(),
});

const reasonCodeSchema = z.enum([
  "fastest",
  "less_walking",
  "fewer_transfers",
  "avoids_active_disruption",
  "lower_event_crowd_exposure",
  "meets_hard_constraints",
  "accessibility",
  "coverage_gap",
  "reasonable_local_option",
]);

export const selectionDecisionSchema = z
  .object({
    selection_reason: z.enum([
      "lowest_final_score",
      "hard_constraint",
      "advisor_tiebreak",
      "outer_agent_selection",
      "deterministic_fallback",
    ]),
    reason_code: reasonCodeSchema.nullable().optional(),
    selection_source: z.enum(["model", "deterministic_fallback"]),
  })
  .passthrough()
  .transform((record): RouteSelectionDecision => {
    const decision: RouteSelectionDecision = {
      selection_reason: record.selection_reason,
      selection_source: record.selection_source,
    };
    if (record.reason_code !== undefined) {
      decision.reason_code = record.reason_code;
    }
    return decision;
  });

const placeFieldsSchema = z.object({
  display_name: limitedText().nullable().optional(),
  label: limitedText().nullable().optional(),
  name: limitedText().nullable().optional(),
  address: limitedText().nullable().optional(),
  place_id: limitedText().nullable().optional(),
  lat: boundedNumber(40.2, 41.2).nullable().optional(),
  lng: boundedNumber(-74.6, -73.2).nullable().optional(),
  latitude: boundedNumber(40.2, 41.2).nullable().optional(),
  longitude: boundedNumber(-74.6, -73.2).nullable().optional(),
  dwell_minutes: boundedNumber(0, 1_440).nullable().optional(),
  dwell_source: limitedText().nullable().optional(),
});

const placeSchema = placeFieldsSchema.transform((place): CanonicalItineraryPlace => {
  const result: CanonicalItineraryPlace = {};
  for (const field of ["display_name", "label", "name", "address", "place_id"] as const) {
    if (place[field] !== undefined) result[field] = place[field];
  }
  for (const field of ["lat", "lng", "latitude", "longitude", "dwell_minutes"] as const) {
    const value = place[field];
    if (value !== null && value !== undefined) result[field] = value;
  }
  if (place.dwell_source !== undefined) result.dwell_source = place.dwell_source;
  return result;
});

const legReferenceObjectSchema = z
  .object({
    name: nonEmptyText().optional(),
    label: nonEmptyText().optional(),
    display_name: nonEmptyText().optional(),
    station_name: nonEmptyText().optional(),
  })
  .passthrough()
  .refine(
    (reference) =>
      Boolean(
        reference.name ||
          reference.label ||
          reference.display_name ||
          reference.station_name,
      ),
  );

const legReferenceSchema = z
  .union([nonEmptyText(), legReferenceObjectSchema])
  .nullable()
  .optional();

const transferKindSchema = z.enum([
  "same_platform",
  "same_station",
  "station_complex",
  "street_transfer",
  "ordinary_walk",
]);

const accessibilitySchema = z.enum(["accessible", "inaccessible", "unknown"]);

const transferSemanticsSchema = z.object({
  kind: transferKindSchema,
  accessibility: accessibilitySchema,
  street_walking_seconds: boundedInteger(0, MAX_SECONDS),
  in_station_transfer_seconds: boundedInteger(0, MAX_SECONDS),
  total_seconds: boundedInteger(0, MAX_SECONDS),
  fragment_count: boundedInteger(1, MAX_LIST),
  group_id: limitedText().nullable().optional(),
  from_route_id: limitedText().nullable().optional(),
  to_route_id: limitedText().nullable().optional(),
  from_stop_id: limitedText().nullable().optional(),
  to_stop_id: limitedText().nullable().optional(),
  from_parent_station: limitedText().nullable().optional(),
  to_parent_station: limitedText().nullable().optional(),
  from_station_label: limitedText().nullable().optional(),
  to_station_label: limitedText().nullable().optional(),
});

const canonicalStopSchema = z.object({
  name: nonEmptyText(),
  lat: z.number().finite().optional(),
  lng: z.number().finite().optional(),
});

const itineraryLegSchema = z.object({
  mode: nonEmptyText(),
  service_id: limitedText().nullable().optional(),
  board: legReferenceSchema,
  alight: legReferenceSchema,
  stop_count: boundedInteger(0, MAX_LIST).nullable().optional(),
  stops: z.array(canonicalStopSchema).max(MAX_LIST).optional(),
  departure_at: limitedText().nullable().optional(),
  arrival_at: limitedText().nullable().optional(),
  walk_seconds: boundedInteger(0, MAX_SECONDS).optional(),
  wait_seconds: boundedInteger(0, MAX_SECONDS).optional(),
  ride_seconds: boundedInteger(0, MAX_SECONDS).optional(),
  transfer_seconds: boundedInteger(0, MAX_SECONDS).optional(),
  segment_index: boundedInteger(0, 64).optional(),
  transfer_kind: transferKindSchema.nullable().optional(),
  transfer_semantics: transferSemanticsSchema.nullable().optional(),
  accessibility: accessibilitySchema.nullable().optional(),
  street_walking_seconds: boundedInteger(0, MAX_SECONDS).optional(),
  in_station_transfer_seconds: boundedInteger(0, MAX_SECONDS).optional(),
  geometry: z
    .object({ encodedPolyline: nonEmptyText(65_536) })
    .strict()
    .nullable()
    .optional(),
  service_data_basis: limitedText().optional(),
});

const segmentLocationSchema = z.union([z.string(), placeSchema]).nullable().optional();

const itinerarySegmentSchema = z.object({
  segment_index: boundedInteger(0, 64),
  origin: segmentLocationSchema,
  destination: segmentLocationSchema,
  legs: z.array(itineraryLegSchema).max(MAX_LIST),
  duration_seconds: boundedInteger(0, MAX_SECONDS).optional(),
});

const dwellEventSchema = z.object({
  event_type: z.literal("dwell"),
  after_segment_index: boundedInteger(0, 64),
  waypoint: placeSchema,
  duration_seconds: boundedInteger(0, MAX_SECONDS),
  source: nonEmptyText(),
});

const crowdEvidenceSchema = {
  crowd_evidence_status: nonEmptyText().optional(),
};

const recommendationReasonSchema = z.union([
  nonEmptyText(),
  z
    .discriminatedUnion("code", [
      z.object({
        code: z.literal("fastest"),
        difference_seconds: boundedInteger(0, MAX_SECONDS).optional(),
        ...crowdEvidenceSchema,
      }),
      z.object({ code: z.literal("less_walking"), ...crowdEvidenceSchema }),
      z.object({
        code: z.literal("fewer_transfers"),
        transfer_difference: boundedInteger(0, 64),
        ...crowdEvidenceSchema,
      }),
      z.object({ code: z.literal("avoids_active_disruption"), ...crowdEvidenceSchema }),
      z.object({
        code: z.literal("lower_event_crowd_exposure"),
        event_count: boundedInteger(0, 64),
        provider_status: nonEmptyText(),
        ...crowdEvidenceSchema,
      }),
      z.object({ code: z.literal("meets_hard_constraints"), ...crowdEvidenceSchema }),
      z.object({ code: z.literal("accessibility"), ...crowdEvidenceSchema }),
      z.object({ code: z.literal("coverage_gap"), ...crowdEvidenceSchema }),
      z.object({ code: z.literal("reasonable_local_option"), ...crowdEvidenceSchema }),
    ])
    .and(z.object({}).passthrough()),
]);

const itineraryPayloadSchema = z.object({
  itinerary_id: nonEmptyText(),
  origin: z.union([z.string(), placeSchema]).optional(),
  waypoints: z.array(placeSchema).max(64).optional(),
  destination: z.union([z.string(), placeSchema]).optional(),
  timezone: limitedText().optional(),
  planning_mode: limitedText().optional(),
  requested_departure: limitedText().nullable().optional(),
  requested_arrival: limitedText().nullable().optional(),
  generated_at: limitedText().nullable().optional(),
  data_basis: limitedText().optional(),
  data_freshness: limitedText().nullable().optional(),
  departure_at: limitedText().nullable().optional(),
  arrival_at: limitedText().nullable().optional(),
  total_duration_seconds: boundedNumber(0, MAX_SECONDS),
  total_walk_seconds: boundedInteger(0, MAX_SECONDS).nullable().optional(),
  total_wait_seconds: boundedInteger(0, MAX_SECONDS).nullable().optional(),
  total_in_vehicle_seconds: boundedInteger(0, MAX_SECONDS).nullable().optional(),
  total_dwell_seconds: boundedInteger(0, MAX_SECONDS).nullable().optional(),
  transfer_count: boundedInteger(0, 64),
  legs: z.array(itineraryLegSchema).max(MAX_LIST),
  segments: z.array(itinerarySegmentSchema).max(64).optional(),
  dwell_events: z.array(dwellEventSchema).max(64).optional(),
  structured_recommendation_reasons: z
    .array(recommendationReasonSchema)
    .max(32)
    .optional(),
  selection_decision: selectionDecisionSchema.optional(),
});

export type ValidatedCanonicalItinerary = Omit<
  z.output<typeof itineraryPayloadSchema>,
  | "total_walk_seconds"
  | "total_wait_seconds"
  | "total_in_vehicle_seconds"
  | "total_dwell_seconds"
> & {
  itinerary_id: string;
  total_duration_seconds: number;
  transfer_count: number;
  total_walk_seconds?: number;
  total_wait_seconds?: number;
  total_in_vehicle_seconds?: number;
  total_dwell_seconds?: number;
};

export const canonicalItinerarySchema = itineraryPayloadSchema.transform(
  (itinerary): ValidatedCanonicalItinerary => {
    const {
      total_walk_seconds: totalWalkSeconds,
      total_wait_seconds: totalWaitSeconds,
      total_in_vehicle_seconds: totalInVehicleSeconds,
      total_dwell_seconds: totalDwellSeconds,
      ...required
    } = itinerary;
    const result: ValidatedCanonicalItinerary = required;
    if (totalWalkSeconds !== null && totalWalkSeconds !== undefined) {
      result.total_walk_seconds = totalWalkSeconds;
    }
    if (totalWaitSeconds !== null && totalWaitSeconds !== undefined) {
      result.total_wait_seconds = totalWaitSeconds;
    }
    if (totalInVehicleSeconds !== null && totalInVehicleSeconds !== undefined) {
      result.total_in_vehicle_seconds = totalInVehicleSeconds;
    }
    if (totalDwellSeconds !== null && totalDwellSeconds !== undefined) {
      result.total_dwell_seconds = totalDwellSeconds;
    }
    return result;
  },
);

export function parseCanonicalItinerary<T>(value: T): ValidatedCanonicalItinerary | null {
  const parsed = canonicalItinerarySchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

export type { CanonicalItinerary, RouteSelectionDecision };
