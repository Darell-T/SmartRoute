import type { RouteStep, ServiceAlert } from "@/types/api";

export type ArrivalSourceStatus =
  | "live"
  | "scheduled"
  | "stale"
  | "provider_unavailable"
  | "no_predictions"
  | "stop_not_resolved";

export interface RouteCardEndpoint {
  label: string;
  lat: number;
  lng: number;
  name?: string;
  address?: string | null;
  place_id?: string | null;
  source?: "places" | "geocoder" | "user" | "fallback" | string;
}

export interface RouteCardSummary {
  eta_minutes: number;
  transfers: number;
  lines: string[];
  reason: string;
  first_leg_arrival?: {
    route_id?: string;
    stop_name?: string;
    source_status?: ArrivalSourceStatus;
    walking_minutes?: number;
    catchable_arrival_minutes?: number | null;
    arrival_minutes?: number[];
  } | null;
}

export type CanonicalTransferKind =
  | "same_platform"
  | "same_station"
  | "station_complex"
  | "street_transfer"
  | "ordinary_walk";

export type CanonicalAccessibility = "accessible" | "inaccessible" | "unknown";

export interface CanonicalTransferSemantics {
  group_id?: string | null;
  kind: CanonicalTransferKind;
  from_route_id?: string | null;
  to_route_id?: string | null;
  from_stop_id?: string | null;
  to_stop_id?: string | null;
  from_parent_station?: string | null;
  to_parent_station?: string | null;
  from_station_label?: string | null;
  to_station_label?: string | null;
  street_walking_seconds: number;
  in_station_transfer_seconds: number;
  total_seconds: number;
  fragment_count: number;
  accessibility: CanonicalAccessibility;
}

export interface CanonicalItineraryStop {
  name: string;
  lat?: number | null;
  lng?: number | null;
}

export interface CanonicalItineraryLeg {
  mode: string;
  service_id?: string | null;
  board?: unknown;
  alight?: unknown;
  stop_count?: number | null;
  stops?: CanonicalItineraryStop[];
  departure_at?: string | null;
  arrival_at?: string | null;
  walk_seconds?: number;
  wait_seconds?: number;
  ride_seconds?: number;
  transfer_seconds?: number;
  transfer_kind?: CanonicalTransferKind | null;
  transfer_semantics?: CanonicalTransferSemantics | null;
  accessibility?: CanonicalAccessibility | null;
  street_walking_seconds?: number;
  in_station_transfer_seconds?: number;
  geometry?: unknown;
  service_data_basis?: string;
  [key: string]: unknown;
}

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

export interface CanonicalItinerarySegment {
  segment_index: number;
  origin?: CanonicalItineraryPlace | RouteCardEndpoint | string | null;
  destination?: CanonicalItineraryPlace | RouteCardEndpoint | string | null;
  legs: CanonicalItineraryLeg[];
  duration_seconds?: number;
}

export interface CanonicalDwellEvent {
  event_type: "dwell";
  after_segment_index: number;
  waypoint: CanonicalItineraryPlace;
  duration_seconds: number;
  source: "default" | "user" | string;
}

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
  segments?: CanonicalItinerarySegment[];
  dwell_events?: CanonicalDwellEvent[];
  structured_recommendation_reasons?: Array<RecommendationReason | string>;
  selection_decision?: RouteSelectionDecision;
  [key: string]: unknown;
}

export interface RouteSelectionDecision {
  selected_candidate_index: number;
  selected_candidate_id: string;
  base_score: number;
  final_score: number;
  hard_constraints_satisfied: string[];
  penalties: Array<{ source: string; amount: number; reason: string }>;
  selection_reason:
    | "lowest_final_score"
    | "hard_constraint"
    | "advisor_tiebreak"
    | "outer_agent_selection";
  evidence_ids: string[];
}

export type RecommendationReason =
  | { code: "fastest"; difference_seconds?: number }
  | { code: "fewer_transfers"; transfer_difference: number }
  | { code: "avoids_active_disruption" }
  | {
      code: "lower_event_crowd_exposure";
      event_count: number;
      provider_status: string;
    };

export interface AgentRouteStep extends RouteStep {
  departure_time_iso?: string;
  arrival_time_iso?: string;
}

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
  itinerary?: CanonicalItinerary;
  selection_decision?: RouteSelectionDecision;
}

export type RouteCard = Omit<RouteCardEvent, "type">;
