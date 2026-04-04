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
  stop_count?: number;
  route_id?: string;
  intermediate_stops?: string[];
}

export interface TransitRouteData {
  steps: RouteStep[];
}

export interface ServiceAlert {
  header: string;
  routeIds: string[];
}

export interface ThinkingResponse {
  text: string;
  audio: string;
}

export interface TripResponse {
  recommendation: string;
  audio: string;
  route: RouteStep[];
  alerts: ServiceAlert[];
}

