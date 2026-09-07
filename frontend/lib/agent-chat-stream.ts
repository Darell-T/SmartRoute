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

import { parseAgentEvent } from "./agent-chat-event-validator";
import type {
  ArrivalSourceStatus,
  RouteCardEvent,
} from "./agent-route-card-contract";

export type {
  AgentRouteStep,
  ArrivalSourceStatus,
  CanonicalAccessibility,
  CanonicalDwellEvent,
  CanonicalItinerary,
  CanonicalItineraryLeg,
  CanonicalItineraryPlace,
  CanonicalItinerarySegment,
  CanonicalItineraryStop,
  CanonicalTransferKind,
  CanonicalTransferSemantics,
  RecommendationReason,
  RouteCard,
  RouteCardEndpoint,
  RouteCardEvent,
  RouteCardSummary,
  RouteSelectionDecision,
} from "./agent-route-card-contract";

export interface EvidenceEnvelope<T> {
  source: string;
  observedAt: string;
  validUntil?: string;
  status: "current" | "stale" | "unavailable";
  payload: T;
}

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

/** `reasoning` — rider-safe deliberation, kept separate from prose. */
export interface ReasoningEvent {
  type: "reasoning";
  text: string;
}

export interface AgentSource {
  title: string;
  url: string;
}

/** `sources` — trusted provider attribution for the active assistant turn. */
export interface SourcesEvent {
  type: "sources";
  sources: AgentSource[];
}

export type ProgressStage = "finding_routes" | "checking_live_conditions" | "comparing_options";
export type ProgressStatus = "active" | "complete";
export interface ProgressEvent {
  type: "progress";
  stage: ProgressStage;
  status: ProgressStatus;
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

export interface ArrivalPrediction {
  expected_at: string;
  minutes: number;
  realtime: boolean;
  trip_id?: string | null;
  vehicle_id?: string | null;
}

export interface ArrivalDirection {
  id: string;
  label: string;
  arrivals: ArrivalPrediction[];
}

export type ArrivalResolutionStatus =
  | "resolved"
  | "ambiguous"
  | "location_required"
  | "no_predictions"
  | "provider_unavailable";

export interface ArrivalCardEvent {
  type: "arrival_card";
  turn_id: string;
  route_id: string;
  stop: {
    id?: string;
    name?: string;
    distance_meters?: number | null;
    latitude?: number | null;
    longitude?: number | null;
  };
  directions: ArrivalDirection[];
  updated_at: string;
  source_status: ArrivalSourceStatus;
  resolution_status: ArrivalResolutionStatus;
  evidence?: EvidenceEnvelope<{ directions: ArrivalDirection[] }>;
  catchability?: {
    walking_minutes: number;
    boarding_buffer_minutes: number;
    arrival_minutes: number[];
    catchable_arrival_minutes?: number | null;
    confidence: number;
  };
  ambiguity?: Array<{ stop_id?: string; stop_name?: string }>;
}

/** `transit_status_action` — a server-owned passenger action offered by a
 * current transit-status response. The UI must not infer this from prose. */
export type TransitStatusAction = "view_alerts";
export interface TransitStatusActionEvent {
  type: "transit_status_action";
  turn_id: string;
  action: TransitStatusAction;
}

/** `error` — codes match `backend/app/services/agent/events.py`. */
export type AgentErrorCode =
  | "rate_limited"
  | "budget_exceeded"
  | "session_expired"
  | "invalid_request"
  | "provider_configuration"
  | "upstream_error"
  | "deadline"
  | "internal";

export interface ErrorEvent {
  type: "error";
  code: AgentErrorCode;
  message: string;
  retryable: boolean;
}

export type AgentStopReason =
  | "end_turn"
  | "clarification_required"
  | "max_rounds"
  | "deadline"
  | "error";

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
  terminal_state?: "completed" | "clarification_required" | "failed" | "cancelled";
  usage: DoneUsage;
}

export type AgentEvent =
  | MetaEvent
  | TokenEvent
  | ReasoningEvent
  | SourcesEvent
  | ProgressEvent
  | ToolStartEvent
  | ToolEndEvent
  | RouteCardEvent
  | ArrivalCardEvent
  | TransitStatusActionEvent
  | ErrorEvent
  | DoneEvent;

const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([
  "meta",
  "token",
  "reasoning",
  "sources",
  "progress",
  "tool_start",
  "tool_end",
  "route_card",
  "arrival_card",
  "transit_status_action",
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

/** Validates the decoded JSON payload against the event's minimum required
 *  shape and returns a typed `AgentEvent`, or `null` (after a console.warn)
 *  when the frame doesn't match — a single bad frame must never crash the
 *  whole turn's stream. */
function buildEvent(eventType: string, data: Record<string, unknown>): AgentEvent | null {
  if (!KNOWN_EVENT_TYPES.has(eventType)) {
    warnSkip(`unknown event type "${eventType}"`, data);
    return null;
  }
  const parsed = parseAgentEvent(eventType, data);
  if (parsed) return parsed;
  warnSkip(`"${eventType}" data failed nested contract validation`, data);
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
  if (!isRecord(data)) {
    warnSkip(`"${eventType}" data is not an object`, data);
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
