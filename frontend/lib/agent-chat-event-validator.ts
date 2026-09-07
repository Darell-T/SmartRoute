import { z } from "zod";

import type {
  AgentEvent,
  AgentSource,
  ArrivalCardEvent,
  DoneEvent,
  ErrorEvent,
  MetaEvent,
  ProgressEvent,
  ReasoningEvent,
  SourcesEvent,
  TokenEvent,
  ToolEndEvent,
  ToolStartEvent,
  TransitStatusActionEvent,
} from "./agent-chat-stream";
import type {
  CanonicalItinerary,
  CanonicalItineraryPlace,
  RouteCardEvent,
} from "./agent-route-card-contract";

const MAX_TEXT = 300;
const MAX_ALERT_DESCRIPTION = 16_384;
const MAX_LIST = 256;
const MAX_SECONDS = 86_400;
const MAX_SOURCES = 8;
const MAX_SOURCE_TITLE = 100;
const MAX_SOURCE_URL = 2_048;

const eventRecordSchema = z.record(z.unknown());
type AgentEventPayload = z.input<typeof eventRecordSchema>;
type Coordinate = { latitude: number; longitude: number };

const limitedText = (max = MAX_TEXT) => z.string().max(max);
const nonEmptyText = (max = MAX_TEXT) =>
  limitedText(max).refine((value) => value.trim().length > 0);
const boundedNumber = (minimum: number, maximum: number) =>
  z.number().finite().min(minimum).max(maximum);
const boundedInteger = (minimum = 0, maximum = MAX_LIST) =>
  z.number().int().min(minimum).max(maximum);
const nonEmptyTextList = (maximum = MAX_LIST) =>
  z.array(nonEmptyText()).max(maximum);

function normalizedSourceUrl(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (
    url.protocol !== "https:" ||
    !url.hostname ||
    url.port ||
    url.username ||
    url.password
  ) {
    return null;
  }
  url.hash = "";
  return url.toString();
}

const sourceUrlSchema = nonEmptyText(MAX_SOURCE_URL).transform(
  (value, context): string => {
    const normalized = normalizedSourceUrl(value);
    if (normalized) return normalized;
    context.addIssue({ code: z.ZodIssueCode.custom, message: "Unsafe source URL" });
    return z.NEVER;
  },
);

const agentSourceSchema = z.object({
  title: nonEmptyText(MAX_SOURCE_TITLE).transform((title) => title.trim()),
  url: sourceUrlSchema,
});

const sourceListSchema = z
  .array(agentSourceSchema)
  .min(1)
  .max(MAX_SOURCES)
  .transform((items): AgentSource[] => {
    const seen = new Set<string>();
    return items.filter((item) => {
      if (seen.has(item.url)) return false;
      seen.add(item.url);
      return true;
    });
  });

const arrivalSourceStatusSchema = z.enum([
  "live",
  "scheduled",
  "stale",
  "provider_unavailable",
  "no_predictions",
  "stop_not_resolved",
]);

const arrivalResolutionStatusSchema = z.enum([
  "resolved",
  "ambiguous",
  "location_required",
  "no_predictions",
  "provider_unavailable",
]);

const errorCodeSchema = z.enum([
  "rate_limited",
  "budget_exceeded",
  "session_expired",
  "invalid_request",
  "provider_configuration",
  "upstream_error",
  "deadline",
  "internal",
]);

const stopReasonSchema = z.enum([
  "end_turn",
  "clarification_required",
  "max_rounds",
  "deadline",
  "error",
]);

const terminalStateSchema = z.enum([
  "completed",
  "clarification_required",
  "failed",
  "cancelled",
]);

const fullCoordinateSchema = z
  .object({
    latitude: boundedNumber(40.2, 41.2),
    longitude: boundedNumber(-74.6, -73.2),
  })
  .strict();

const compactCoordinateSchema = z
  .object({
    lat: boundedNumber(40.2, 41.2),
    lng: boundedNumber(-74.6, -73.2),
  })
  .strict()
  .transform(
    (coordinate): Coordinate => ({
      latitude: coordinate.lat,
      longitude: coordinate.lng,
    }),
  );

const coordinateSchema = z.union([
  fullCoordinateSchema,
  compactCoordinateSchema,
]);

const endpointSchema = z.object({
  label: nonEmptyText(),
  lat: boundedNumber(40.2, 41.2),
  lng: boundedNumber(-74.6, -73.2),
  name: limitedText().optional(),
  address: limitedText().nullable().optional(),
  place_id: limitedText().nullable().optional(),
  source: limitedText().optional(),
});

const firstLegArrivalSchema = z.object({
  route_id: limitedText().optional(),
  stop_name: limitedText().optional(),
  source_status: arrivalSourceStatusSchema.optional(),
  walking_minutes: z.number().finite().optional(),
  catchable_arrival_minutes: z.number().finite().nullable().optional(),
  arrival_minutes: z
    .array(boundedNumber(-1_440, 1_440))
    .max(32)
    .optional(),
});

const routeSummarySchema = z.object({
  eta_minutes: boundedNumber(0, 1_440),
  transfers: boundedInteger(0, 64),
  lines: nonEmptyTextList(32),
  reason: nonEmptyText(),
  first_leg_arrival: firstLegArrivalSchema.nullable().optional(),
});

const intermediateStopSchema = z.object({
  name: nonEmptyText(),
  lat: boundedNumber(40.2, 41.2),
  lng: boundedNumber(-74.6, -73.2),
});

const routeStepSchema = z.object({
  type: z.enum(["WALK", "SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL", "TRAM"]),
  start_point: coordinateSchema.optional(),
  end_point: coordinateSchema.optional(),
  departure_coords: coordinateSchema.optional(),
  arrival_coords: coordinateSchema.optional(),
  polyline: z.object({ encodedPolyline: nonEmptyText(8_192) }).optional(),
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
  intermediate_stops: nonEmptyTextList(64).optional(),
  intermediate_stop_locations: z.array(intermediateStopSchema).max(64).optional(),
});

const alertSchema = z.object({
  header: nonEmptyText(),
  description: limitedText(MAX_ALERT_DESCRIPTION).optional(),
  routeIds: nonEmptyTextList(64).optional(),
  route_ids: nonEmptyTextList(64).optional(),
});

const selectionSchema = z
  .object({
    selection_reason: z.enum(["outer_agent_selection", "deterministic_fallback"]),
    reason_code: z
      .enum([
        "fastest",
        "less_walking",
        "fewer_transfers",
        "avoids_active_disruption",
        "lower_event_crowd_exposure",
        "meets_hard_constraints",
        "accessibility",
        "coverage_gap",
        "reasonable_local_option",
      ])
      .optional(),
    selection_source: z.enum(["model", "deterministic_fallback"]),
  })
  .strict();

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
    .object({ encodedPolyline: nonEmptyText(8_192) })
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
  total_duration_seconds: boundedInteger(0, MAX_SECONDS),
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
  selection_decision: selectionSchema.optional(),
});

const itinerarySchema = itineraryPayloadSchema.transform((itinerary): CanonicalItinerary => {
  const {
    total_walk_seconds: totalWalkSeconds,
    total_wait_seconds: totalWaitSeconds,
    total_in_vehicle_seconds: totalInVehicleSeconds,
    total_dwell_seconds: totalDwellSeconds,
    ...required
  } = itinerary;
  const result: CanonicalItinerary = required;
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
});

const arrivalPredictionSchema = z.object({
  expected_at: nonEmptyText(64),
  minutes: boundedNumber(-1_440, 1_440),
  realtime: z.boolean(),
  trip_id: limitedText().nullable().optional(),
  vehicle_id: limitedText().nullable().optional(),
});

const arrivalDirectionSchema = z.object({
  id: nonEmptyText(),
  label: nonEmptyText(),
  arrivals: z.array(arrivalPredictionSchema).max(64),
});

const evidenceSchema = z.object({
  source: nonEmptyText(),
  observedAt: nonEmptyText(64),
  validUntil: limitedText().optional(),
  status: z.enum(["current", "stale", "unavailable"]),
  payload: z.object({ directions: z.array(arrivalDirectionSchema).max(32) }),
});

const catchabilitySchema = z.object({
  walking_minutes: boundedNumber(0, 1_440),
  boarding_buffer_minutes: boundedNumber(0, 1_440),
  arrival_minutes: z.array(boundedNumber(-1_440, 1_440)).max(64),
  catchable_arrival_minutes: z.number().finite().nullable().optional(),
  confidence: boundedNumber(0, 1),
});

const ambiguityItemSchema = z
  .object({
    stop_id: limitedText().optional(),
    stop_name: limitedText().optional(),
  })
  .refine((item) => Boolean(item.stop_id?.trim() || item.stop_name?.trim()));

const arrivalEventSchema = z
  .object({
    turn_id: nonEmptyText(),
    route_id: nonEmptyText(),
    stop: z.object({
      id: limitedText().optional(),
      name: limitedText().optional(),
      distance_meters: boundedNumber(0, 1_000_000).nullable().optional(),
      latitude: boundedNumber(40.2, 41.2).nullable().optional(),
      longitude: boundedNumber(-74.6, -73.2).nullable().optional(),
    }),
    directions: z.array(arrivalDirectionSchema).max(32),
    updated_at: nonEmptyText(64),
    source_status: arrivalSourceStatusSchema,
    resolution_status: arrivalResolutionStatusSchema,
    evidence: evidenceSchema.optional(),
    catchability: catchabilitySchema.optional(),
    ambiguity: z.array(ambiguityItemSchema).max(64).optional(),
  })
  .transform((arrival): ArrivalCardEvent => ({ type: "arrival_card", ...arrival }));

const metaEventSchema = z
  .object({ session_id: nonEmptyText(), turn_id: nonEmptyText() })
  .transform((meta): MetaEvent => ({ type: "meta", ...meta }));

const tokenEventSchema = z
  .object({ text: limitedText(32_768) })
  .transform((token): TokenEvent => ({ type: "token", ...token }));

const reasoningEventSchema = z
  .object({ text: limitedText(32_768) })
  .transform((reasoning): ReasoningEvent => ({ type: "reasoning", ...reasoning }));

const sourcesEventSchema = z
  .object({ sources: sourceListSchema })
  .transform((sources): SourcesEvent => ({ type: "sources", ...sources }));

const progressEventSchema = z
  .object({
    stage: z.enum(["finding_routes", "checking_live_conditions", "comparing_options"]),
    status: z.enum(["active", "complete"]),
  })
  .transform((progress): ProgressEvent => ({ type: "progress", ...progress }));

const toolStartEventSchema = z
  .object({
    tool_call_id: nonEmptyText(),
    tool: nonEmptyText(),
    label: nonEmptyText(),
  })
  .transform((tool): ToolStartEvent => ({ type: "tool_start", ...tool }));

const toolEndEventSchema = z
  .object({
    tool_call_id: nonEmptyText(),
    tool: nonEmptyText(),
    ok: z.boolean(),
    duration_ms: boundedNumber(0, 300_000),
    summary: limitedText().optional(),
  })
  .transform((tool): ToolEndEvent => ({ type: "tool_end", ...tool }));

const routeCardEventSchema = z
  .object({
    card_id: nonEmptyText(),
    turn_id: nonEmptyText(),
    role: z.enum(["recommended", "alternative"]),
    origin: endpointSchema,
    destination: endpointSchema,
    summary: routeSummarySchema,
    route: z.array(routeStepSchema).max(MAX_LIST),
    alerts: z.array(alertSchema).max(MAX_LIST),
    leg_label: limitedText().optional(),
    depart_iso: limitedText().optional(),
    itinerary: itinerarySchema.optional(),
    selection_decision: selectionSchema.optional(),
  })
  .transform((card): RouteCardEvent => ({ type: "route_card", ...card }));

const transitStatusActionEventSchema = z
  .object({ turn_id: nonEmptyText(), action: z.literal("view_alerts") })
  .transform(
    (action): TransitStatusActionEvent => ({ type: "transit_status_action", ...action }),
  );

const errorEventSchema = z
  .object({
    code: errorCodeSchema,
    message: nonEmptyText(),
    retryable: z.boolean(),
  })
  .transform((error): ErrorEvent => ({ type: "error", ...error }));

const doneEventSchema = z
  .object({
    session_id: nonEmptyText(),
    turn_id: nonEmptyText(),
    stop_reason: stopReasonSchema,
    terminal_state: terminalStateSchema.optional(),
    usage: z
      .object({
        input_tokens: boundedInteger(0, 10_000_000).optional(),
        output_tokens: boundedInteger(0, 10_000_000).optional(),
      })
      .passthrough(),
  })
  .transform((done): DoneEvent => ({ type: "done", ...done }));

function parseEvent<T extends AgentEvent, Input>(
  schema: z.ZodType<T, z.ZodTypeDef, Input>,
  data: AgentEventPayload,
): T | null {
  const parsed = schema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

export function parseAgentEvent(
  eventType: string,
  data: AgentEventPayload,
): AgentEvent | null {
  const payload = eventRecordSchema.safeParse(data);
  if (!payload.success) return null;

  switch (eventType) {
    case "meta":
      return parseEvent(metaEventSchema, payload.data);
    case "token":
      return parseEvent(tokenEventSchema, payload.data);
    case "reasoning":
      return parseEvent(reasoningEventSchema, payload.data);
    case "sources":
      return parseEvent(sourcesEventSchema, payload.data);
    case "progress":
      return parseEvent(progressEventSchema, payload.data);
    case "tool_start":
      return parseEvent(toolStartEventSchema, payload.data);
    case "tool_end":
      return parseEvent(toolEndEventSchema, payload.data);
    case "route_card":
      return parseEvent(routeCardEventSchema, payload.data);
    case "arrival_card":
      return parseEvent(arrivalEventSchema, payload.data);
    case "transit_status_action":
      return parseEvent(transitStatusActionEventSchema, payload.data);
    case "error":
      return parseEvent(errorEventSchema, payload.data);
    case "done":
      return parseEvent(doneEventSchema, payload.data);
    default:
      return null;
  }
}
