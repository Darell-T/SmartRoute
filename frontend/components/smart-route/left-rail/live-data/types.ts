import type {
  LiveFeedIncident,
  LiveFeedResponse,
  RouteCandidate,
  RouteStep as ApiRouteStep,
  ServiceAlertDetail,
} from "@/types/api";
import type {
  Arrival,
  FeedEvent,
  NearbyTransitGroup,
  NetworkHealth,
  RoutePlan,
  ServiceAlert,
  Station,
} from "../types";

export type LineState = Record<string, "major" | "minor" | "planned">;

export interface BuildLeftRailDataInput {
  liveFeed?: Partial<LiveFeedResponse> | null;
  routeSteps?: ApiRouteStep[];
  routeCandidates?: RouteCandidate[];
  activeRouteCandidate?: RouteCandidate | null;
  switchHeadline?: string | null;
  recommendationText?: string | null;
  routeEta?: string | null;
  routeTotalTime?: string | null;
  serviceAlerts?: ServiceAlertDetail[];
  incidents?: LiveFeedIncident[];
  nowMs?: number;
}

export interface LeftRailLiveData {
  station: Station;
  health: NetworkHealth;
  arrivals: Arrival[];
  nearbyTransitGroups: NearbyTransitGroup[];
  nearbyBusArrivals: Arrival[];
  plan: RoutePlan;
  feed: FeedEvent[];
  lineState: LineState;
  alerts: ServiceAlert[];
}

export interface ArrivalRows {
  serviceRows: Arrival[];
  stationRows: Arrival[];
}

export type CandidateSeverity = "high" | "medium" | "low";

export interface CandidateDelta {
  delta: string;
  sev: CandidateSeverity;
}
