import type {
  AgentErrorCode,
  AgentEvent,
  AgentRouteStep,
  AgentStopReason,
  ArrivalCardEvent,
  ArrivalDirection,
  ArrivalPrediction,
  ArrivalResolutionStatus,
  ArrivalSourceStatus,
  CanonicalDwellEvent,
  CanonicalItinerary,
  CanonicalItineraryLeg,
  CanonicalItineraryPlace,
  CanonicalItinerarySegment,
  CanonicalItineraryStop,
  RecommendationReason,
  RouteCardEndpoint,
  RouteCardSummary,
  RouteSelectionDecision,
} from "./agent-chat-stream";
import type { ServiceAlert } from "@/types/api";

type RecordValue = Record<string, unknown>;
type Coordinate = { latitude: number; longitude: number };

const MAX_TEXT = 300;
const MAX_LIST = 256;
const MAX_SECONDS = 86_400;

const record = (value: unknown): value is RecordValue => typeof value === "object" && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const text = (value: unknown, max = MAX_TEXT): value is string => typeof value === "string" && value.length <= max;
const nonEmptyText = (value: unknown, max = MAX_TEXT): value is string => text(value, max) && value.trim().length > 0;
const integer = (value: unknown, min = 0, max = MAX_LIST): value is number => Number.isInteger(value) && finite(value) && value >= min && value <= max;
const bounded = (value: unknown, min: number, max: number): value is number => finite(value) && value >= min && value <= max;
const optional = <T>(value: unknown, parse: (candidate: unknown) => candidate is T): value is T | undefined => value === undefined || parse(value);
const nullable = <T>(value: unknown, parse: (candidate: unknown) => candidate is T): value is T | undefined | null => value === undefined || value === null || parse(value);
const textList = (value: unknown, max = MAX_LIST): value is string[] => Array.isArray(value) && value.length <= max && value.every((item) => nonEmptyText(item));

function arrivalSourceStatus(value: unknown): value is ArrivalSourceStatus {
  return value === "live" || value === "scheduled" || value === "stale" || value === "provider_unavailable" || value === "no_predictions" || value === "stop_not_resolved";
}

function arrivalResolutionStatus(value: unknown): value is ArrivalResolutionStatus {
  return value === "resolved" || value === "ambiguous" || value === "location_required" || value === "no_predictions" || value === "provider_unavailable";
}

function errorCode(value: unknown): value is AgentErrorCode {
  return value === "rate_limited" || value === "budget_exceeded" || value === "session_expired" || value === "invalid_request" || value === "provider_configuration" || value === "upstream_error" || value === "internal";
}

function stopReason(value: unknown): value is AgentStopReason {
  return value === "end_turn" || value === "clarification_required" || value === "max_rounds" || value === "deadline" || value === "error";
}

function terminalState(value: unknown): value is "completed" | "clarification_required" | "failed" | "cancelled" {
  return value === "completed" || value === "clarification_required" || value === "failed" || value === "cancelled";
}

function coordinate(value: unknown): Coordinate | null {
  if (!record(value)) return null;
  const keys = Object.keys(value).sort().join(",");
  const latitude = keys === "latitude,longitude" && finite(value.latitude)
    ? value.latitude
    : keys === "lat,lng" && finite(value.lat) ? value.lat : null;
  const longitude = keys === "latitude,longitude" && finite(value.longitude)
    ? value.longitude
    : keys === "lat,lng" && finite(value.lng) ? value.lng : null;
  return latitude !== null && longitude !== null && bounded(latitude, 40.2, 41.2) && bounded(longitude, -74.6, -73.2)
    ? { latitude, longitude }
    : null;
}

function endpoint(value: unknown): RouteCardEndpoint | null {
  if (!record(value) || !nonEmptyText(value.label) || !finite(value.lat) || !finite(value.lng)
    || !bounded(value.lat, 40.2, 41.2) || !bounded(value.lng, -74.6, -73.2)
    || !optional(value.name, text) || !nullable(value.address, text) || !nullable(value.place_id, text) || !optional(value.source, text)) return null;
  return {
    label: value.label, lat: value.lat, lng: value.lng,
    ...(typeof value.name === "string" ? { name: value.name } : {}),
    ...(value.address === null || typeof value.address === "string" ? { address: value.address } : {}),
    ...(value.place_id === null || typeof value.place_id === "string" ? { place_id: value.place_id } : {}),
    ...(typeof value.source === "string" ? { source: value.source } : {}),
  };
}

function summary(value: unknown): RouteCardSummary | null {
  if (!record(value) || !bounded(value.eta_minutes, 0, 1_440) || !integer(value.transfers, 0, 64)
    || !textList(value.lines, 32) || !nonEmptyText(value.reason)) return null;
  const first = value.first_leg_arrival;
  if (first !== undefined && first !== null && (!record(first) || !optional(first.route_id, text)
    || !optional(first.stop_name, text) || !optional(first.source_status, arrivalSourceStatus)
    || !optional(first.walking_minutes, finite) || !nullable(first.catchable_arrival_minutes, finite)
    || (first.arrival_minutes !== undefined && (!Array.isArray(first.arrival_minutes)
      || first.arrival_minutes.length > 32 || !first.arrival_minutes.every((item) => bounded(item, -1_440, 1_440)))))) return null;
  return {
    eta_minutes: value.eta_minutes, transfers: value.transfers, lines: value.lines, reason: value.reason,
    ...(first === null ? { first_leg_arrival: null } : first === undefined ? {} : {
      first_leg_arrival: {
        ...(typeof first.route_id === "string" ? { route_id: first.route_id } : {}),
        ...(typeof first.stop_name === "string" ? { stop_name: first.stop_name } : {}),
        ...(arrivalSourceStatus(first.source_status) ? { source_status: first.source_status } : {}),
        ...(finite(first.walking_minutes) ? { walking_minutes: first.walking_minutes } : {}),
        ...(first.catchable_arrival_minutes === null || finite(first.catchable_arrival_minutes) ? { catchable_arrival_minutes: first.catchable_arrival_minutes } : {}),
        ...(Array.isArray(first.arrival_minutes) ? { arrival_minutes: first.arrival_minutes } : {}),
      },
    }),
  };
}

function routeStep(value: unknown): AgentRouteStep | null {
  if (!record(value) || (value.type !== "WALK" && value.type !== "SUBWAY" && value.type !== "BUS")) return null;
  const start = value.start_point === undefined ? undefined : coordinate(value.start_point);
  const end = value.end_point === undefined ? undefined : coordinate(value.end_point);
  const departure = value.departure_coords === undefined ? undefined : coordinate(value.departure_coords);
  const arrival = value.arrival_coords === undefined ? undefined : coordinate(value.arrival_coords);
  if ((value.start_point !== undefined && !start) || (value.end_point !== undefined && !end)
    || (value.departure_coords !== undefined && !departure) || (value.arrival_coords !== undefined && !arrival)
    || !optional(value.train_line, text) || !optional(value.line_color, text) || !optional(value.direction, text)
    || !optional(value.departure_stop, text) || !optional(value.arrival_stop, text) || !optional(value.route_id, text)
    || !optional(value.departure_time_iso, (item): item is string => text(item, 64)) || !optional(value.arrival_time_iso, (item): item is string => text(item, 64))
    || !optional(value.minutes_until_train_arrives, (item): item is number => bounded(item, -1_440, 1_440))
    || !optional(value.minutes_until_arrival, (item): item is number => bounded(item, -1_440, 1_440))
    || !optional(value.route_total_minutes, (item): item is number => bounded(item, 0, 1_440))
    || !optional(value.route_total_seconds, (item): item is number => bounded(item, 0, MAX_SECONDS))
    || !optional(value.duration_minutes, (item): item is number => bounded(item, 0, 1_440))
    || !optional(value.distance_meters, (item): item is number => bounded(item, 0, 1_000_000))
    || !optional(value.stop_count, (item): item is number => integer(item, 0, 256))
    || !optional(value.segment_index, (item): item is number => integer(item, 0, 64))) return null;
  const encodedPolyline = record(value.polyline) && nonEmptyText(value.polyline.encodedPolyline, 8_192) ? value.polyline.encodedPolyline : null;
  if (value.polyline !== undefined && !encodedPolyline) return null;
  if (value.intermediate_stops !== undefined && !textList(value.intermediate_stops, 64)) return null;
  if (value.intermediate_stop_locations !== undefined && (!Array.isArray(value.intermediate_stop_locations)
    || value.intermediate_stop_locations.length > 64 || !value.intermediate_stop_locations.every((stop) => record(stop)
      && nonEmptyText(stop.name) && bounded(stop.lat, 40.2, 41.2) && bounded(stop.lng, -74.6, -73.2)))) return null;
  return {
    type: value.type,
    ...(start ? { start_point: start } : {}), ...(end ? { end_point: end } : {}),
    ...(departure ? { departure_coords: departure } : {}), ...(arrival ? { arrival_coords: arrival } : {}),
    ...(encodedPolyline ? { polyline: { encodedPolyline } } : {}),
    ...(typeof value.train_line === "string" ? { train_line: value.train_line } : {}), ...(typeof value.line_color === "string" ? { line_color: value.line_color } : {}),
    ...(typeof value.direction === "string" ? { direction: value.direction } : {}), ...(typeof value.departure_stop === "string" ? { departure_stop: value.departure_stop } : {}),
    ...(typeof value.arrival_stop === "string" ? { arrival_stop: value.arrival_stop } : {}), ...(typeof value.route_id === "string" ? { route_id: value.route_id } : {}),
    ...(typeof value.departure_time_iso === "string" ? { departure_time_iso: value.departure_time_iso } : {}), ...(typeof value.arrival_time_iso === "string" ? { arrival_time_iso: value.arrival_time_iso } : {}),
    ...(finite(value.minutes_until_train_arrives) ? { minutes_until_train_arrives: value.minutes_until_train_arrives } : {}), ...(finite(value.minutes_until_arrival) ? { minutes_until_arrival: value.minutes_until_arrival } : {}),
    ...(finite(value.route_total_minutes) ? { route_total_minutes: value.route_total_minutes } : {}), ...(finite(value.stop_count) ? { stop_count: value.stop_count } : {}), ...(finite(value.segment_index) ? { segment_index: value.segment_index } : {}),
    ...(finite(value.route_total_seconds) ? { route_total_seconds: value.route_total_seconds } : {}), ...(finite(value.duration_minutes) ? { duration_minutes: value.duration_minutes } : {}), ...(finite(value.distance_meters) ? { distance_meters: value.distance_meters } : {}),
    ...(Array.isArray(value.intermediate_stops) ? { intermediate_stops: value.intermediate_stops } : {}),
    ...(Array.isArray(value.intermediate_stop_locations) ? { intermediate_stop_locations: value.intermediate_stop_locations.map((stop) => ({ name: stop.name, lat: stop.lat, lng: stop.lng })) } : {}),
  };
}

function alert(value: unknown): ServiceAlert | null {
  if (!record(value) || !nonEmptyText(value.header) || !optional(value.description, text)
    || (value.routeIds !== undefined && !textList(value.routeIds, 64)) || (value.route_ids !== undefined && !textList(value.route_ids, 64))) return null;
  return { header: value.header, ...(typeof value.description === "string" ? { description: value.description } : {}), ...(Array.isArray(value.routeIds) ? { routeIds: value.routeIds } : {}), ...(Array.isArray(value.route_ids) ? { route_ids: value.route_ids } : {}) };
}

function selection(value: unknown): RouteSelectionDecision | null {
  if (!record(value) || !integer(value.selected_candidate_index, 0, 64) || !nonEmptyText(value.selected_candidate_id)
    || !finite(value.base_score) || !finite(value.final_score) || !textList(value.hard_constraints_satisfied, 64)
    || !textList(value.evidence_ids, 64) || !Array.isArray(value.penalties) || value.penalties.length > 64
    || (value.selection_reason !== "lowest_final_score" && value.selection_reason !== "hard_constraint" && value.selection_reason !== "advisor_tiebreak")
    || !value.penalties.every((item) => record(item) && nonEmptyText(item.source) && finite(item.amount) && nonEmptyText(item.reason))) return null;
  return { selected_candidate_index: value.selected_candidate_index, selected_candidate_id: value.selected_candidate_id, base_score: value.base_score, final_score: value.final_score, hard_constraints_satisfied: value.hard_constraints_satisfied, penalties: value.penalties.map((item) => ({ source: item.source, amount: item.amount, reason: item.reason })), selection_reason: value.selection_reason, evidence_ids: value.evidence_ids };
}

function place(value: unknown): CanonicalItineraryPlace | null {
  if (!record(value)) return null;
  const fields = ["display_name", "label", "name", "address", "place_id", "dwell_source"];
  if (!fields.every((field) => nullable(value[field], text)) || !nullable(value.dwell_minutes, (item): item is number => bounded(item, 0, 1_440))
    || !nullable(value.lat, (item): item is number => bounded(item, 40.2, 41.2)) || !nullable(value.lng, (item): item is number => bounded(item, -74.6, -73.2))
    || !nullable(value.latitude, (item): item is number => bounded(item, 40.2, 41.2)) || !nullable(value.longitude, (item): item is number => bounded(item, -74.6, -73.2))) return null;
  return { ...(typeof value.display_name === "string" || value.display_name === null ? { display_name: value.display_name } : {}), ...(typeof value.label === "string" || value.label === null ? { label: value.label } : {}), ...(typeof value.name === "string" || value.name === null ? { name: value.name } : {}), ...(typeof value.address === "string" || value.address === null ? { address: value.address } : {}), ...(typeof value.place_id === "string" || value.place_id === null ? { place_id: value.place_id } : {}), ...(finite(value.lat) ? { lat: value.lat } : {}), ...(finite(value.lng) ? { lng: value.lng } : {}), ...(finite(value.latitude) ? { latitude: value.latitude } : {}), ...(finite(value.longitude) ? { longitude: value.longitude } : {}), ...(finite(value.dwell_minutes) ? { dwell_minutes: value.dwell_minutes } : {}), ...(typeof value.dwell_source === "string" || value.dwell_source === null ? { dwell_source: value.dwell_source } : {}) };
}

function legReference(value: unknown): boolean {
  if (value === undefined || value === null || nonEmptyText(value)) return true;
  if (!record(value)) return false;
  return nonEmptyText(value.name) || nonEmptyText(value.label) || nonEmptyText(value.display_name)
    || nonEmptyText(value.station_name);
}

function legGeometry(value: unknown): boolean {
  return value === undefined || value === null
    || (record(value) && Object.keys(value).length === 1 && nonEmptyText(value.encodedPolyline, 8_192));
}

function itineraryLeg(value: unknown): CanonicalItineraryLeg | null {
  if (!record(value) || !nonEmptyText(value.mode) || !nullable(value.service_id, text) || !nullable(value.departure_at, text) || !nullable(value.arrival_at, text) || !optional(value.service_data_basis, text) || !legReference(value.board) || !legReference(value.alight) || !legGeometry(value.geometry)) return null;
  const numeric = ["stop_count", "walk_seconds", "wait_seconds", "ride_seconds", "transfer_seconds", "segment_index"];
  if (!numeric.every((field) => optional(value[field], (item): item is number => integer(item, 0, field === "segment_index" ? 64 : MAX_SECONDS))) || (value.stops !== undefined && (!Array.isArray(value.stops) || value.stops.length > MAX_LIST || !value.stops.every((stop) => record(stop) && nonEmptyText(stop.name) && optional(stop.lat, finite) && optional(stop.lng, finite))))) return null;
  return { mode: value.mode, ...(typeof value.service_id === "string" || value.service_id === null ? { service_id: value.service_id } : {}), ...(value.board !== undefined ? { board: value.board } : {}), ...(value.alight !== undefined ? { alight: value.alight } : {}), ...(typeof value.departure_at === "string" || value.departure_at === null ? { departure_at: value.departure_at } : {}), ...(typeof value.arrival_at === "string" || value.arrival_at === null ? { arrival_at: value.arrival_at } : {}), ...(typeof value.service_data_basis === "string" ? { service_data_basis: value.service_data_basis } : {}), ...(finite(value.stop_count) ? { stop_count: value.stop_count } : {}), ...(finite(value.walk_seconds) ? { walk_seconds: value.walk_seconds } : {}), ...(finite(value.wait_seconds) ? { wait_seconds: value.wait_seconds } : {}), ...(finite(value.ride_seconds) ? { ride_seconds: value.ride_seconds } : {}), ...(finite(value.transfer_seconds) ? { transfer_seconds: value.transfer_seconds } : {}), ...(finite(value.segment_index) ? { segment_index: value.segment_index } : {}), ...(value.geometry !== undefined ? { geometry: value.geometry } : {}), ...(Array.isArray(value.stops) ? { stops: value.stops.map((stop): CanonicalItineraryStop => ({ name: stop.name, ...(finite(stop.lat) ? { lat: stop.lat } : {}), ...(finite(stop.lng) ? { lng: stop.lng } : {}) })) } : {}) };
}

function itinerary(value: unknown): CanonicalItinerary | null {
  if (!record(value) || !nonEmptyText(value.itinerary_id) || !integer(value.total_duration_seconds, 0, MAX_SECONDS) || !integer(value.transfer_count, 0, 64) || !Array.isArray(value.legs) || value.legs.length > MAX_LIST) return null;
  const legs = value.legs.map(itineraryLeg);
  if (legs.some((leg) => !leg)) return null;
  const origin = value.origin === undefined || typeof value.origin === "string" ? value.origin : place(value.origin);
  const destination = value.destination === undefined || typeof value.destination === "string" ? value.destination : place(value.destination);
  if (origin === null || destination === null || !optional(value.timezone, text) || !optional(value.planning_mode, text) || !nullable(value.requested_departure, text) || !nullable(value.requested_arrival, text) || !nullable(value.generated_at, text) || !optional(value.data_basis, text) || !nullable(value.data_freshness, text) || !nullable(value.departure_at, text) || !nullable(value.arrival_at, text)) return null;
  const totals = ["total_walk_seconds", "total_wait_seconds", "total_in_vehicle_seconds", "total_dwell_seconds"];
  if (!totals.every((field) => optional(value[field], (item): item is number => integer(item, 0, MAX_SECONDS)) || value[field] === null)) return null;
  if (value.waypoints !== undefined && (!Array.isArray(value.waypoints) || value.waypoints.length > 64 || value.waypoints.map(place).some((item) => !item))) return null;
  if (value.structured_recommendation_reasons !== undefined && (!Array.isArray(value.structured_recommendation_reasons) || value.structured_recommendation_reasons.length > 32 || !value.structured_recommendation_reasons.every(recommendationReason))) return null;
  const itinerarySelection = value.selection_decision === undefined ? undefined : selection(value.selection_decision);
  if (value.selection_decision !== undefined && !itinerarySelection) return null;
  // Segment/dwell records repeat the same typed leg/place constraints. Reject malformed records rather than silently ignoring them.
  if (value.segments !== undefined && (!Array.isArray(value.segments) || value.segments.length > 64 || value.segments.some((item) => segment(item) === null))) return null;
  if (value.dwell_events !== undefined && (!Array.isArray(value.dwell_events) || value.dwell_events.length > 64 || value.dwell_events.some((item) => dwellEvent(item) === null))) return null;
  return { itinerary_id: value.itinerary_id, total_duration_seconds: value.total_duration_seconds, transfer_count: value.transfer_count, legs: legs.filter((leg): leg is CanonicalItineraryLeg => leg !== null), ...(origin !== undefined ? { origin } : {}), ...(destination !== undefined ? { destination } : {}), ...(Array.isArray(value.waypoints) ? { waypoints: value.waypoints.map((item) => place(item)).filter((item): item is CanonicalItineraryPlace => item !== null) } : {}), ...(typeof value.timezone === "string" ? { timezone: value.timezone } : {}), ...(typeof value.planning_mode === "string" ? { planning_mode: value.planning_mode } : {}), ...(typeof value.requested_departure === "string" || value.requested_departure === null ? { requested_departure: value.requested_departure } : {}), ...(typeof value.requested_arrival === "string" || value.requested_arrival === null ? { requested_arrival: value.requested_arrival } : {}), ...(typeof value.generated_at === "string" || value.generated_at === null ? { generated_at: value.generated_at } : {}), ...(typeof value.data_basis === "string" ? { data_basis: value.data_basis } : {}), ...(typeof value.data_freshness === "string" || value.data_freshness === null ? { data_freshness: value.data_freshness } : {}), ...(typeof value.departure_at === "string" || value.departure_at === null ? { departure_at: value.departure_at } : {}), ...(typeof value.arrival_at === "string" || value.arrival_at === null ? { arrival_at: value.arrival_at } : {}), ...(finite(value.total_walk_seconds) ? { total_walk_seconds: value.total_walk_seconds } : {}), ...(finite(value.total_wait_seconds) ? { total_wait_seconds: value.total_wait_seconds } : {}), ...(finite(value.total_in_vehicle_seconds) ? { total_in_vehicle_seconds: value.total_in_vehicle_seconds } : {}), ...(finite(value.total_dwell_seconds) ? { total_dwell_seconds: value.total_dwell_seconds } : {}), ...(Array.isArray(value.segments) ? { segments: value.segments.map((item) => segment(item)).filter((item): item is CanonicalItinerarySegment => item !== null) } : {}), ...(Array.isArray(value.dwell_events) ? { dwell_events: value.dwell_events.map((item) => dwellEvent(item)).filter((item): item is CanonicalDwellEvent => item !== null) } : {}), ...(Array.isArray(value.structured_recommendation_reasons) ? { structured_recommendation_reasons: value.structured_recommendation_reasons } : {}), ...(itinerarySelection ? { selection_decision: itinerarySelection } : {}) };
}

function segment(value: unknown): CanonicalItinerarySegment | null {
  if (!record(value) || !integer(value.segment_index, 0, 64) || !Array.isArray(value.legs) || value.legs.length > MAX_LIST || !optional(value.duration_seconds, (item): item is number => integer(item, 0, MAX_SECONDS))) return null;
  const legs = value.legs.map(itineraryLeg);
  const origin = value.origin === undefined || value.origin === null || typeof value.origin === "string" ? value.origin : place(value.origin);
  const destination = value.destination === undefined || value.destination === null || typeof value.destination === "string" ? value.destination : place(value.destination);
  if (legs.some((leg) => !leg) || origin === null || destination === null) return null;
  return { segment_index: value.segment_index, legs: legs.filter((leg): leg is CanonicalItineraryLeg => leg !== null), ...(origin !== undefined ? { origin } : {}), ...(destination !== undefined ? { destination } : {}), ...(finite(value.duration_seconds) ? { duration_seconds: value.duration_seconds } : {}) };
}

function dwellEvent(value: unknown): CanonicalDwellEvent | null {
  const waypoint = record(value) ? place(value.waypoint) : null;
  if (!record(value) || value.event_type !== "dwell" || !integer(value.after_segment_index, 0, 64) || !integer(value.duration_seconds, 0, MAX_SECONDS) || !nonEmptyText(value.source) || !waypoint) return null;
  return { event_type: "dwell", after_segment_index: value.after_segment_index, waypoint, duration_seconds: value.duration_seconds, source: value.source };
}

function recommendationReason(value: unknown): value is RecommendationReason | string {
  if (nonEmptyText(value)) return true;
  if (!record(value)) return false;
  return (value.code === "fastest" && optional(value.difference_seconds, (item): item is number => integer(item, 0, MAX_SECONDS))) || (value.code === "fewer_transfers" && integer(value.transfer_difference, 0, 64)) || value.code === "avoids_active_disruption";
}

function prediction(value: unknown): ArrivalPrediction | null {
  if (!record(value) || !nonEmptyText(value.expected_at, 64) || !bounded(value.minutes, -1_440, 1_440) || typeof value.realtime !== "boolean" || !nullable(value.trip_id, text) || !nullable(value.vehicle_id, text)) return null;
  return { expected_at: value.expected_at, minutes: value.minutes, realtime: value.realtime, ...(typeof value.trip_id === "string" || value.trip_id === null ? { trip_id: value.trip_id } : {}), ...(typeof value.vehicle_id === "string" || value.vehicle_id === null ? { vehicle_id: value.vehicle_id } : {}) };
}

function direction(value: unknown): ArrivalDirection | null {
  if (!record(value) || !nonEmptyText(value.id) || !nonEmptyText(value.label) || !Array.isArray(value.arrivals) || value.arrivals.length > 64) return null;
  const arrivals = value.arrivals.map(prediction);
  return arrivals.some((item) => !item) ? null : { id: value.id, label: value.label, arrivals: arrivals.filter((item): item is ArrivalPrediction => item !== null) };
}

function evidence(value: unknown): ArrivalCardEvent["evidence"] | null {
  if (!record(value) || !nonEmptyText(value.source) || !nonEmptyText(value.observedAt, 64)
    || !optional(value.validUntil, text) || (value.status !== "current" && value.status !== "stale" && value.status !== "unavailable")
    || !record(value.payload) || !Array.isArray(value.payload.directions) || value.payload.directions.length > 32) return null;
  const directions = value.payload.directions.map(direction);
  return directions.some((item) => !item) ? null : {
    source: value.source,
    observedAt: value.observedAt,
    ...(typeof value.validUntil === "string" ? { validUntil: value.validUntil } : {}),
    status: value.status,
    payload: { directions: directions.filter((item): item is ArrivalDirection => item !== null) },
  };
}

function catchability(value: unknown): NonNullable<ArrivalCardEvent["catchability"]> | null {
  if (!record(value) || !bounded(value.walking_minutes, 0, 1_440) || !bounded(value.boarding_buffer_minutes, 0, 1_440)
    || !bounded(value.confidence, 0, 1) || !Array.isArray(value.arrival_minutes) || value.arrival_minutes.length > 64
    || !value.arrival_minutes.every((item) => bounded(item, -1_440, 1_440)) || !nullable(value.catchable_arrival_minutes, finite)) return null;
  return { walking_minutes: value.walking_minutes, boarding_buffer_minutes: value.boarding_buffer_minutes, confidence: value.confidence, arrival_minutes: value.arrival_minutes, ...(value.catchable_arrival_minutes === null || finite(value.catchable_arrival_minutes) ? { catchable_arrival_minutes: value.catchable_arrival_minutes } : {}) };
}

function ambiguity(value: unknown): NonNullable<ArrivalCardEvent["ambiguity"]> | null {
  if (!Array.isArray(value) || value.length > 64 || !value.every((item) => record(item) && optional(item.stop_id, text) && optional(item.stop_name, text) && (nonEmptyText(item.stop_id) || nonEmptyText(item.stop_name)))) return null;
  return value.map((item) => ({ ...(typeof item.stop_id === "string" ? { stop_id: item.stop_id } : {}), ...(typeof item.stop_name === "string" ? { stop_name: item.stop_name } : {}) }));
}

function arrival(value: RecordValue): ArrivalCardEvent | null {
  if (!nonEmptyText(value.turn_id) || !nonEmptyText(value.route_id) || !record(value.stop) || !Array.isArray(value.directions) || value.directions.length > 32 || !nonEmptyText(value.updated_at, 64) || !arrivalSourceStatus(value.source_status) || !arrivalResolutionStatus(value.resolution_status)) return null;
  const directions = value.directions.map(direction);
  const stop = value.stop;
  if (directions.some((item) => !item) || !optional(stop.id, text) || !optional(stop.name, text) || !nullable(stop.distance_meters, (item): item is number => bounded(item, 0, 1_000_000)) || !nullable(stop.latitude, (item): item is number => bounded(item, 40.2, 41.2)) || !nullable(stop.longitude, (item): item is number => bounded(item, -74.6, -73.2))) return null;
  const arrivalEvidence = value.evidence === undefined ? undefined : evidence(value.evidence);
  const arrivalCatchability = value.catchability === undefined ? undefined : catchability(value.catchability);
  const arrivalAmbiguity = value.ambiguity === undefined ? undefined : ambiguity(value.ambiguity);
  if ((value.evidence !== undefined && !arrivalEvidence) || (value.catchability !== undefined && !arrivalCatchability) || (value.ambiguity !== undefined && !arrivalAmbiguity)) return null;
  return { type: "arrival_card", turn_id: value.turn_id, route_id: value.route_id, stop: { ...(typeof stop.id === "string" ? { id: stop.id } : {}), ...(typeof stop.name === "string" ? { name: stop.name } : {}), ...(stop.distance_meters === null || finite(stop.distance_meters) ? { distance_meters: stop.distance_meters } : {}), ...(stop.latitude === null || finite(stop.latitude) ? { latitude: stop.latitude } : {}), ...(stop.longitude === null || finite(stop.longitude) ? { longitude: stop.longitude } : {}) }, directions: directions.filter((item): item is ArrivalDirection => item !== null), updated_at: value.updated_at, source_status: value.source_status, resolution_status: value.resolution_status, ...(arrivalEvidence ? { evidence: arrivalEvidence } : {}), ...(arrivalCatchability ? { catchability: arrivalCatchability } : {}), ...(arrivalAmbiguity ? { ambiguity: arrivalAmbiguity } : {}) };
}

export function parseAgentEvent(eventType: string, data: unknown): AgentEvent | null {
  if (!record(data)) return null;
  if (eventType === "meta" && nonEmptyText(data.session_id) && nonEmptyText(data.turn_id)) return { type: "meta", session_id: data.session_id, turn_id: data.turn_id };
  if (eventType === "token" && text(data.text, 32_768)) return { type: "token", text: data.text };
  if (eventType === "tool_start" && nonEmptyText(data.tool_call_id) && nonEmptyText(data.tool) && nonEmptyText(data.label)) return { type: "tool_start", tool_call_id: data.tool_call_id, tool: data.tool, label: data.label };
  if (eventType === "tool_end" && nonEmptyText(data.tool_call_id) && nonEmptyText(data.tool) && typeof data.ok === "boolean" && bounded(data.duration_ms, 0, 300_000) && optional(data.summary, text)) return { type: "tool_end", tool_call_id: data.tool_call_id, tool: data.tool, ok: data.ok, duration_ms: data.duration_ms, ...(typeof data.summary === "string" ? { summary: data.summary } : {}) };
  if (eventType === "route_card") {
    const origin = endpoint(data.origin); const destination = endpoint(data.destination); const cardSummary = summary(data.summary);
    const route = Array.isArray(data.route) && data.route.length <= MAX_LIST ? data.route.map(routeStep) : null;
    const alerts = Array.isArray(data.alerts) && data.alerts.length <= MAX_LIST ? data.alerts.map(alert) : null;
    const cardItinerary = data.itinerary === undefined ? undefined : itinerary(data.itinerary); const cardSelection = data.selection_decision === undefined ? undefined : selection(data.selection_decision);
    if (!nonEmptyText(data.card_id) || !nonEmptyText(data.turn_id) || (data.role !== "recommended" && data.role !== "alternative") || !origin || !destination || !cardSummary || !route || route.some((item) => !item) || !alerts || alerts.some((item) => !item) || (data.itinerary !== undefined && !cardItinerary) || (data.selection_decision !== undefined && !cardSelection) || !optional(data.leg_label, text) || !optional(data.depart_iso, text)) return null;
    return { type: "route_card", card_id: data.card_id, turn_id: data.turn_id, role: data.role, origin, destination, summary: cardSummary, route: route.filter((item): item is AgentRouteStep => item !== null), alerts: alerts.filter((item): item is ServiceAlert => item !== null), ...(typeof data.leg_label === "string" ? { leg_label: data.leg_label } : {}), ...(typeof data.depart_iso === "string" ? { depart_iso: data.depart_iso } : {}), ...(cardItinerary ? { itinerary: cardItinerary } : {}), ...(cardSelection ? { selection_decision: cardSelection } : {}) };
  }
  if (eventType === "arrival_card") return arrival(data);
  if (eventType === "error" && errorCode(data.code) && nonEmptyText(data.message) && typeof data.retryable === "boolean") return { type: "error", code: data.code, message: data.message, retryable: data.retryable };
  if (eventType === "done" && nonEmptyText(data.session_id) && nonEmptyText(data.turn_id) && stopReason(data.stop_reason) && record(data.usage) && optional(data.usage.input_tokens, (item): item is number => integer(item, 0, 10_000_000)) && optional(data.usage.output_tokens, (item): item is number => integer(item, 0, 10_000_000)) && (data.terminal_state === undefined || terminalState(data.terminal_state))) return { type: "done", session_id: data.session_id, turn_id: data.turn_id, stop_reason: data.stop_reason, ...(terminalState(data.terminal_state) ? { terminal_state: data.terminal_state } : {}), usage: data.usage };
  return null;
}
