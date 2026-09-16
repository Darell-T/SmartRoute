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
import type { RouteCardEvent } from "./agent-route-card-contract";
import {
  alertSchema,
  canonicalItinerarySchema,
  routeStepSchema,
  selectionDecisionSchema,
} from "./canonical-itinerary-schema";
import { MAX_LIST, boundedInteger, boundedNumber, limitedText, nonEmptyText, nonEmptyTextList } from "./schema-primitives";

const MAX_SOURCES = 8;
const MAX_SOURCE_TITLE = 100;
const MAX_SOURCE_URL = 2_048;

export const eventRecordSchema = z.record(z.unknown());
type AgentEventPayload = z.input<typeof eventRecordSchema>;

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
    itinerary: canonicalItinerarySchema.optional(),
    selection_decision: selectionDecisionSchema.optional(),
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

const AGENT_EVENT_SCHEMAS = {
  meta: metaEventSchema,
  token: tokenEventSchema,
  reasoning: reasoningEventSchema,
  sources: sourcesEventSchema,
  progress: progressEventSchema,
  tool_start: toolStartEventSchema,
  tool_end: toolEndEventSchema,
  route_card: routeCardEventSchema,
  arrival_card: arrivalEventSchema,
  transit_status_action: transitStatusActionEventSchema,
  error: errorEventSchema,
  done: doneEventSchema,
};

function isKnownEventType(eventType: string): eventType is keyof typeof AGENT_EVENT_SCHEMAS {
  return Object.hasOwn(AGENT_EVENT_SCHEMAS, eventType);
}

export function parseAgentEvent(
  eventType: string,
  data: AgentEventPayload,
): AgentEvent | null {
  const payload = eventRecordSchema.safeParse(data);
  if (!payload.success || !isKnownEventType(eventType)) return null;
  const parsed = AGENT_EVENT_SCHEMAS[eventType].safeParse(payload.data);
  return parsed.success ? parsed.data : null;
}
