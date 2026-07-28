import type { CanonicalItinerary } from "@/lib/agent-chat-stream";

export interface Coordinates {
  latitude: number;
  longitude: number;
}

export interface RouteStep {
  type: "WALK" | "SUBWAY" | "BUS";
  start_point?: Coordinates;
  end_point?: Coordinates;
  polyline?: { encodedPolyline: string };
  train_line?: string;
  line_color?: string;
  direction?: string;
  departure_stop?: string;
  arrival_stop?: string;
  departure_coords?: Coordinates;
  arrival_coords?: Coordinates;
  minutes_until_train_arrives?: number;
  minutes_until_arrival?: number;
  route_total_minutes?: number;
  route_total_seconds?: number;
  duration_minutes?: number;
  distance_meters?: number;
  stop_count?: number;
  route_id?: string;
  intermediate_stops?: string[];
  intermediate_stop_locations?: IntermediateStopLocation[];
  /** Server-owned chained-itinerary boundary; absent for legacy direct routes. */
  segment_index?: number;
}

export interface IntermediateStopLocation {
  name: string;
  lat: number;
  lng: number;
}

export interface TransitRouteData {
  steps: RouteStep[];
  /** Canonical agent itinerary, when this route entered the map from chat. */
  itinerary?: CanonicalItinerary;
}

export interface RouteCandidate {
  id: string;
  index: number;
  steps: RouteStep[];
  is_recommended: boolean;
  total_minutes?: number;
  /**
   * Canonical itinerary arrival wall-clock (ISO). When present, map/rail must
   * format this for arrive labels instead of inventing now+eta.
   */
  arrival_at?: string;
  selection_score?: number;
  selection_rank?: number;
  score_breakdown?: {
    duration_minutes?: number;
    transfers?: number;
    active_alerts?: number;
    transit_lines?: string[];
  };
  // Only the chosen route is enriched (intermediate stops) on the initial trip
  // response. Alternates come back enriched=false / can_enrich_on_select=true
  // and are filled in lazily via enrichRoute() when selected.
  enriched?: boolean;
  can_enrich_on_select?: boolean;
  recommendation_reason?: string;
  rejection_reason?: string;
  /** The original agent itinerary. Never reconstruct multi-stop state from steps. */
  itinerary?: CanonicalItinerary;
  itinerary_id?: string;
  origin?: {
    label: string;
    lat: number;
    lng: number;
    name?: string;
    address?: string | null;
  };
  destination?: {
    label: string;
    lat: number;
    lng: number;
    name?: string;
    address?: string | null;
  };
}

export interface DestinationSelection {
  label: string;
  address?: string;
  coordinates: {
    lat: number;
    lng: number;
  };
}

export interface ServiceAlert {
  header: string;
  routeIds?: string[];
  route_ids?: string[];
  description?: string;
}

export interface TripResponse {
  recommendation: string;
  route: RouteStep[];
  selected_route_index?: number;
  route_candidates?: RouteCandidate[];
  alerts: ServiceAlert[];
}

export interface NearestStop {
  stop_id: string;
  stop_name: string;
  distance_m: number;
  route_ids: string[];
  stop_lat?: number;
  stop_lon?: number;
}

export interface LiveArrival {
  route_id: string;
  trip_id?: string;
  stop_id?: string;
  arrival_time: number | null;
  delay?: number;
  direction?: "UPTOWN" | "DOWNTOWN" | "UNKNOWN" | string;
  terminal_stop_id?: string;
  terminal_stop_name?: string;
  parent_stop_id?: string;
  parent_stop_name?: string;
  station_name?: string;
  distance_m?: number;
  stop_lat?: number;
  stop_lon?: number;
  mode?: "subway" | "bus";
}

export interface LiveVehicle {
  id: string;
  trip_id?: string;
  route_id: string;
  route_name?: string;
  lat: number;
  lng: number;
  stop_id?: string;
  stop_name?: string;
  status?: string;
  timestamp?: number;
  age_seconds?: number;
  stale: boolean;
  color: string;
  terminal_stop_id?: string;
  terminal_stop_name?: string;
  current_stop_sequence?: number;
  position_source?: "vehicle_position" | "polyline_estimate" | "stop_id" | "stop_id_pending_coords" | string;
  segment?: {
    from_stop_id?: string;
    from_stop_name?: string;
    from_lat?: number;
    from_lng?: number;
    to_stop_id?: string;
    to_stop_name?: string;
    to_lat?: number;
    to_lng?: number;
    progress: number;
  };
}

export interface LiveSystemSignals {
  network_status: "healthy" | "caution" | "disrupted";
  active_alert_count: number;
  major_alert_count: number;
  affected_route_count: number;
  tracked_vehicle_count: number;
  stale_vehicle_count: number;
  routes_reporting_count: number;
  feed_failures: number;
  vehicles_with_position: number;
  vehicles_without_position: number;
  updated_at: number;
}

export interface LiveFeedIncident {
  id: string;
  type:
    | "fire"
    | "police"
    | "assault"
    | "stabbing"
    | "weapon"
    | "passenger"
    | "medical"
    | "general"
    | "hazard"
    | "incident";
  lat: number;
  lng: number;
  radius_m?: number;
  title: string;
  detail?: string;
  severity?: "low" | "medium" | "high" | "critical";
  source?: string;
  routeIds?: string[];
  updated_at?: number;
}

export interface LiveFeedResponse {
  nearest_stop: NearestStop | null;
  stops: NearestStop[];
  arrivals: LiveArrival[];
  alerts: ServiceAlertDetail[];
  vehicles?: LiveVehicle[];
  signals?: LiveSystemSignals | null;
  incidents?: LiveFeedIncident[];
  updated_at: number;
  degraded?: boolean;
  debug?: {
    route_ids: string[];
    nearest_route_ids?: string[];
    selected_route_ids?: string[];
    vehicle_route_ids?: string[];
    nearby_route_ids?: string[];
    arrival_radius_m?: number;
    nearby_stop_count?: number;
    nearby_child_stop_count?: number;
    bus_arrivals_supported?: boolean;
    nearby_bus_stop_count?: number;
    bus_arrival_count?: number;
    bus_stop_monitoring_failures?: number;
    bus_arrivals_reason?: string;
    feed_count: number;
    vehicle_count: number;
    vehicle_scope?: "nearest_routes" | "all_subway" | "all_subway_with_stop_id_fallback" | "nearest_plus_selected" | string;
  };
}

export interface ServiceAlertDetail extends ServiceAlert {
  alert_id?: string;
  stop_ids?: string[];
  stop_names?: string[];
  start?: number | null;
  end?: number | null;
}

export interface ServiceAlertsResponse {
  alerts: ServiceAlertDetail[];
  updated_at: number;
  active_count: number;
  affected_route_count: number;
  source: "mta";
}

export interface Incident {
  location?: string;
  nearby_station?: string;
  severity?: "low" | "medium" | "high" | string;
  description?: string;
  source?: string;
}
