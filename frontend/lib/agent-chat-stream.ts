/**
 * SSE-over-POST wire format for the conversational transit agent. Pure
 * parsing/typing module — no React, no fetch. Mirrors the field names in
 * `backend/app/services/agent/events.py` exactly (the backend's `to_data()`
 * output is what these types decode), so a change on that side should show
 * up here as a type error rather than a silent runtime mismatch.
 *
 * Event order contract (enforced by the backend, not this module): `meta`
 * first, `done` always last — even after an `error`.
 */

import type { RouteStep, ServiceAlert } from "@/types/api";

/** `meta` — first event of every turn; carries the (possibly new) session. */
export interface MetaEvent {
  type: "meta";
  session_id: string;
  turn_id: string;
}

/** `token` — one chunk of the assistant's streamed prose. */
export interface TokenEvent {
  type: "token";
  text: string;
}

/** `tool_start` — a tool call kicked off; `label` is server-generated rider
 *  copy ("Finding routes to Costco (no bus)…"), never raw model text. */
export interface ToolStartEvent {
  type: "tool_start";
  tool_call_id: string;
  tool: string;
  label: string;
}

/** `tool_end` — resolves a `tool_start` by `tool_call_id`. */
export interface ToolEndEvent {
  type: "tool_end";
  tool_call_id: string;
  tool: string;
  ok: boolean;
  duration_ms: number;
  summary?: string;
}

/** A route candidate endpoint (origin or destination). */
export interface RouteCardEndpoint {
  label: string;
  lat: number;
  lng: number;
  name?: string;
  address?: string | null;
  place_id?: string | null;
  source?: "places" | "geocoder" | "user" | "fallback" | string;
}

/** Compact digest the model reasoned over — mirrors `RouteCardEvent.summary`
 *  on the backend (`events.py`). */
export interface RouteCardSummary {
  eta_minutes: number;
  transfers: number;
  lines: string[];
  reason: string;
}

/** One normalized leg inside a canonical itinerary (backend
 *  `build_canonical_itinerary`). UI may format seconds; it must not invent. */
export interface CanonicalItineraryLeg {
  mode: string;
  service_id?: string | null;
  board?: unknown;
  alight?: unknown;
  /** Provider-owned number of stops ridden on this leg. */
  stop_count?: number | null;
  /** Ordered provider/enrichment stop sequence, including endpoints when known. */
  stops?: CanonicalItineraryStop[];
  departure_at?: string | null;
  arrival_at?: string | null;
  walk_seconds?: number;
  wait_seconds?: number;
  ride_seconds?: number;
  transfer_seconds?: number;
  geometry?: unknown;
  service_data_basis?: string;
  [key: string]: unknown;
}

export interface CanonicalItineraryStop {
  name: string;
  lat?: number | null;
  lng?: number | null;
}

/** A rider-facing endpoint retained by the canonical itinerary. */
export interface CanonicalItineraryPlace {
  display_name?: string | null;
  label?: string | null;
  name?: string | null;
  address?: string | null;
  place_id?: string | null;
  lat?: number | null;
  lng?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  dwell_minutes?: number | null;
  dwell_source?: "default" | "user" | string | null;
  [key: string]: unknown;
}

/** One ordered OD portion of a canonical chained journey. */
export interface CanonicalItinerarySegment {
  segment_index: number;
  origin?: CanonicalItineraryPlace | RouteCardEndpoint | string | null;
  destination?: CanonicalItineraryPlace | RouteCardEndpoint | string | null;
  legs: CanonicalItineraryLeg[];
  duration_seconds?: number;
}

/** Server-owned intermediate stop time. This is never a transit transfer. */
export interface CanonicalDwellEvent {
  event_type: "dwell";
  after_segment_index: number;
  waypoint: CanonicalItineraryPlace;
  duration_seconds: number;
  source: "default" | "user" | string;
}

/**
 * Seconds-based immutable itinerary from the backend normalizer
 * (`backend/app/services/trips/itinerary.py`). Optional on older servers;
 * when present, chat/map must prefer these fields over re-derived totals.
 */
export interface CanonicalItinerary {
  itinerary_id?: string;
  origin?: unknown;
  waypoints?: CanonicalItineraryPlace[];
  destination?: unknown;
  timezone?: string;
  planning_mode?: string;
  requested_departure?: string | null;
  requested_arrival?: string | null;
  generated_at?: string | null;
  data_basis?: string;
  data_freshness?: string | null;
  departure_at?: string | null;
  arrival_at?: string | null;
  total_duration_seconds?: number;
  total_walk_seconds?: number;
  total_wait_seconds?: number;
  total_in_vehicle_seconds?: number;
  total_dwell_seconds?: number;
  transfer_count?: number;
  legs?: CanonicalItineraryLeg[];
  /** Present for server-owned multi-stop journeys; preserves OD boundaries. */
  segments?: CanonicalItinerarySegment[];
  /** Present for multi-stop journeys; dwell is a distinct semantic event. */
  dwell_events?: CanonicalDwellEvent[];
  /** Typed facts for current payloads; strings are a legacy-session adapter. */
  structured_recommendation_reasons?: Array<RecommendationReason | string>;
  [key: string]: unknown;
}

export type RecommendationReason =
  | {
      code: "fastest";
      difference_seconds?: number;
    }
  | {
      code: "fewer_transfers";
      transfer_difference: number;
    }
  | {
      code: "avoids_active_disruption";
    };

/** A single transit step, additively extended with the absolute departure /
 *  arrival timestamps future-departure turns need (design correction #1 in
 *  the plan: "future departures break the current step shape"). Everything
 *  else is byte-compatible with the existing `/api/trip` step shape so the
 *  map renderer draws these unchanged. */
export interface AgentRouteStep extends RouteStep {
  departure_time_iso?: string;
  arrival_time_iso?: string;
}

/** `route_card` — one recommended or alternative itinerary. The full event
 *  payload minus the `type`/`turn_id` discriminants a card doesn't need to
 *  carry once attached to its turn (see `RouteCard` below). */
export interface RouteCardEvent {
  type: "route_card";
  card_id: string;
  turn_id: string;
  role: "recommended" | "alternative";
  origin: RouteCardEndpoint;
  destination: RouteCardEndpoint;
  summary: RouteCardSummary;
  route: AgentRouteStep[];
  alerts: ServiceAlert[];
  leg_label?: string;
  depart_iso?: string;
  /** Canonical seconds-based itinerary when the server emits it (Task 2+). */
  itinerary?: CanonicalItinerary;
}

/** `route_card` payload as attached to an assistant turn (same shape as the
 *  wire event — kept as a distinct name so UI code isn't coupled to "this is
 *  an SSE event" framing). */
export type RouteCard = Omit<RouteCardEvent, "type">;

/** `error` — codes match `backend/app/services/agent/events.py`. */
export type AgentErrorCode =
  | "rate_limited"
  | "budget_exceeded"
  | "session_expired"
  | "upstream_error"
  | "internal";

export interface ErrorEvent {
  type: "error";
  code: AgentErrorCode;
  message: string;
  retryable: boolean;
}

export type AgentStopReason = "end_turn" | "max_rounds" | "deadline" | "error";

export interface DoneUsage {
  input_tokens?: number;
  output_tokens?: number;
  [key: string]: unknown;
}

/** `done` — always the last event of a turn. */
export interface DoneEvent {
  type: "done";
  session_id: string;
  turn_id: string;
  stop_reason: AgentStopReason;
  usage: DoneUsage;
}

export type AgentEvent =
  | MetaEvent
  | TokenEvent
  | ToolStartEvent
  | ToolEndEvent
  | RouteCardEvent
  | ErrorEvent
  | DoneEvent;

const KNOWN_EVENT_TYPES = new Set<AgentEvent["type"]>([
  "meta",
  "token",
  "tool_start",
  "tool_end",
  "route_card",
  "error",
  "done",
]);

function warnSkip(reason: string, detail: unknown): void {
  // eslint-disable-next-line no-console
  console.warn(`[agent-chat-stream] skipping malformed SSE frame: ${reason}`, detail);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

/** Validates the decoded JSON payload against the event's minimum required
 *  shape and returns a typed `AgentEvent`, or `null` (after a console.warn)
 *  when the frame doesn't match — a single bad frame must never crash the
 *  whole turn's stream. */
function buildEvent(eventType: string, data: unknown): AgentEvent | null {
  if (!KNOWN_EVENT_TYPES.has(eventType as AgentEvent["type"])) {
    warnSkip(`unknown event type "${eventType}"`, data);
    return null;
  }
  if (!isRecord(data)) {
    warnSkip(`"${eventType}" data is not an object`, data);
    return null;
  }

  switch (eventType as AgentEvent["type"]) {
    case "meta": {
      if (!isString(data.session_id) || !isString(data.turn_id)) break;
      return { type: "meta", session_id: data.session_id, turn_id: data.turn_id };
    }
    case "token": {
      if (!isString(data.text)) break;
      return { type: "token", text: data.text };
    }
    case "tool_start": {
      if (!isString(data.tool_call_id) || !isString(data.tool) || !isString(data.label)) break;
      return {
        type: "tool_start",
        tool_call_id: data.tool_call_id,
        tool: data.tool,
        label: data.label,
      };
    }
    case "tool_end": {
      if (
        !isString(data.tool_call_id) ||
        !isString(data.tool) ||
        !isBoolean(data.ok) ||
        !isNumber(data.duration_ms)
      ) {
        break;
      }
      return {
        type: "tool_end",
        tool_call_id: data.tool_call_id,
        tool: data.tool,
        ok: data.ok,
        duration_ms: data.duration_ms,
        summary: isString(data.summary) ? data.summary : undefined,
      };
    }
    case "route_card": {
      if (
        !isString(data.card_id) ||
        !isString(data.turn_id) ||
        (data.role !== "recommended" && data.role !== "alternative") ||
        !isRecord(data.origin) ||
        !isRecord(data.destination) ||
        !isRecord(data.summary) ||
        !Array.isArray(data.route) ||
        !Array.isArray(data.alerts)
      ) {
        break;
      }
      return {
        type: "route_card",
        card_id: data.card_id,
        turn_id: data.turn_id,
        role: data.role,
        origin: data.origin as unknown as RouteCardEndpoint,
        destination: data.destination as unknown as RouteCardEndpoint,
        summary: data.summary as unknown as RouteCardSummary,
        route: data.route as unknown as AgentRouteStep[],
        alerts: data.alerts as unknown as ServiceAlert[],
        leg_label: isString(data.leg_label) ? data.leg_label : undefined,
        depart_iso: isString(data.depart_iso) ? data.depart_iso : undefined,
        // Copy opaque object only when present; omit key for legacy payloads.
        ...(isRecord(data.itinerary)
          ? { itinerary: data.itinerary as CanonicalItinerary }
          : {}),
      };
    }
    case "error": {
      if (!isString(data.code) || !isString(data.message) || !isBoolean(data.retryable)) break;
      return {
        type: "error",
        code: data.code as AgentErrorCode,
        message: data.message,
        retryable: data.retryable,
      };
    }
    case "done": {
      if (
        !isString(data.session_id) ||
        !isString(data.turn_id) ||
        !isString(data.stop_reason) ||
        !isRecord(data.usage)
      ) {
        break;
      }
      return {
        type: "done",
        session_id: data.session_id,
        turn_id: data.turn_id,
        stop_reason: data.stop_reason as AgentStopReason,
        usage: data.usage as DoneUsage,
      };
    }
  }
  warnSkip(`"${eventType}" data is missing required fields`, data);
  return null;
}

/** Parses one `\n`-joined SSE frame (already split on the blank-line frame
 *  separator) into an `AgentEvent`. Returns `null` for heartbeat comments
 *  (`: ping`), blank frames, and malformed frames (which also log a
 *  `console.warn` so a bad frame is visible in dev tools without breaking
 *  the stream). */
function parseSseFrame(frame: string): AgentEvent | null {
  let eventType: string | null = null;
  const dataLines: string[] = [];

  for (const rawLine of frame.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "" || line.startsWith(":")) continue; // blank / comment-heartbeat
    if (line.startsWith("event:")) {
      eventType = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }

  if (eventType === null && dataLines.length === 0) return null; // pure heartbeat/blank frame
  if (eventType === null) {
    warnSkip("data field with no event field", frame);
    return null;
  }

  const raw = dataLines.join("\n");
  let data: unknown;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch (err) {
    warnSkip(`invalid JSON in "${eventType}" frame`, err);
    return null;
  }
  return buildEvent(eventType, data);
}

/**
 * Reads a `text/event-stream` body and yields decoded `AgentEvent`s in
 * arrival order. Frames are separated by a blank line (`\n\n`); this buffers
 * across chunk boundaries so a frame split mid-stream by the network layer
 * still parses correctly, and flushes a trailing partial frame (if any) once
 * the stream closes. Heartbeat comments and malformed frames are skipped
 * rather than raised — one bad frame must not kill an otherwise-good stream.
 */
export async function* parseSseStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): AsyncGenerator<AgentEvent> {
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      const event = parseSseFrame(frame);
      if (event) yield event;
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    const event = parseSseFrame(buffer);
    if (event) yield event;
  }
}
