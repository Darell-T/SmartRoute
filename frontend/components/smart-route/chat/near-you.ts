import type { ServiceAlert } from "@/components/smart-route/left-rail/types";
import type { LeftRailLiveData } from "@/components/smart-route/left-rail/live-data";
import type { NearbyTransitIssue } from "@/types/api";
import {
  selectHomeNearbyIssue,
  type HomeNearbyIssue,
} from "./nearby-issue";

export interface HomeNearbyArrival {
  id: string;
  routeId: string;
  destination: string;
  minutes: number[];
}

export interface HomeNearbyModel {
  locationState:
    | "pending"
    | "precise_nyc"
    | "fallback_nyc"
    | "outside_service_area";
  locationLabel: string;
  locationNotice: string | null;
  stationName: string | null;
  arrivals: HomeNearbyArrival[];
  arrivalsState: "loading" | "ready" | "unavailable" | "outside_service_area";
  condition: {
    state: "clear" | "alert" | "loading" | "unavailable";
    label: string;
  };
  issue: HomeNearbyIssue | null;
}

interface BuildHomeNearbyModelInput {
  data: LeftRailLiveData;
  nearestStopName?: string | null;
  nearestRouteIds?: string[];
  arrivalsLoading: boolean;
  arrivalsUnavailable: boolean;
  serviceAlertsLoading: boolean;
  serviceAlertsUnavailable: boolean;
  nearbyIssues?: NearbyTransitIssue[];
  hasPlannedRoute?: boolean;
  locationState?: HomeNearbyModel["locationState"];
  nowMs?: number;
}

const MAX_HOME_ARRIVALS = 3;
const MAX_ALERT_SUMMARY_LENGTH = 72;
const ALERT_DIRECTION_PATTERN =
  /\b(downtown|uptown|northbound|southbound|eastbound|westbound|manhattan-bound|brooklyn-bound|queens-bound|bronx-bound)\b/i;

function normalizedRouteIds(values: string[]) {
  return new Set(values.map((value) => value.toUpperCase()));
}

function relevantAlert(
  alerts: ServiceAlert[],
  nearbyRouteIds: Set<string>,
): ServiceAlert | undefined {
  if (nearbyRouteIds.size === 0) return undefined;
  return alerts.find((alert) =>
    alert.lines.some((line) => nearbyRouteIds.has(line.toUpperCase())),
  );
}

function formatRouteList(routeIds: readonly string[]): string {
  const routes = Array.from(
    new Set(routeIds.map((line) => line.trim().toUpperCase()).filter(Boolean)),
  ).slice(0, 3);
  if (routes.length < 2) return routes[0] ?? "";
  if (routes.length === 2) return routes.join(" and ");
  return `${routes[0]}, ${routes[1]}, and ${routes[2]}`;
}

function conciseAlertSummary(alert: ServiceAlert): string {
  const normalized = (alert.title || alert.sub)
    .replace(/\s+/g, " ")
    .trim();
  if (normalized.length <= MAX_ALERT_SUMMARY_LENGTH) return normalized;

  const routeLabel = formatRouteList(alert.lines);
  const direction = normalized.match(ALERT_DIRECTION_PATTERN)?.[1];
  const subject = [direction, routeLabel]
    .filter(Boolean)
    .join(" ")
    .replace(/^./, (character) => character.toUpperCase());
  const prefix = subject ? `${subject} ` : "";
  const lower = normalized.toLowerCase();

  if (/\b(delay|delays|delayed)\b/.test(lower)) {
    return `${prefix}trains running with delays`;
  }
  if (/\b(suspend|suspended|no trains|no service)\b/.test(lower)) {
    return `${prefix}service suspended nearby`;
  }
  if (/\b(skip|skips|skipping|bypass)\b/.test(lower)) {
    return `${prefix}trains skipping nearby stops`;
  }
  if (/\b(reroute|rerouted|rerouting)\b/.test(lower)) {
    return `${prefix}trains rerouted nearby`;
  }
  if (alert.sev === "planned") {
    return `${prefix}planned service change nearby`;
  }
  return `${prefix}service change nearby`;
}

function collectHomeArrivals(
  groups: BuildHomeNearbyModelInput["data"]["nearbyTransitGroups"],
): HomeNearbyArrival[] {
  const arrivals: HomeNearbyArrival[] = [];
  const seen = new Set<string>();
  for (const group of groups) {
    for (const arrival of group.arrivals) {
      const routeId = arrival.routeIds[0]?.toUpperCase();
      const minutes = arrival.arrivalMinutes
        .filter((minute) => Number.isFinite(minute) && minute >= 0)
        .sort((a, b) => a - b);
      if (!routeId || minutes.length === 0) continue;
      const key = `${routeId}:${arrival.destination}:${arrival.direction}`;
      if (seen.has(key)) continue;
      seen.add(key);
      arrivals.push({
        id: arrival.id,
        routeId,
        destination: arrival.destination,
        minutes: minutes.slice(0, 1),
      });
      if (arrivals.length === MAX_HOME_ARRIVALS) return arrivals;
    }
  }
  return arrivals;
}

function nearbyCondition(
  alert: ReturnType<typeof relevantAlert>,
  serviceAlertsLoading: boolean | undefined,
  serviceAlertsUnavailable: boolean | undefined,
): HomeNearbyModel["condition"] {
  if (alert) return { state: "alert", label: conciseAlertSummary(alert) };
  if (serviceAlertsLoading) return { state: "loading", label: "Checking nearby service status" };
  if (serviceAlertsUnavailable) {
    return { state: "unavailable", label: "Service status unavailable" };
  }
  return { state: "clear", label: "No active service changes nearby" };
}

function nearbyStationName(
  locationState: BuildHomeNearbyModelInput["locationState"],
  groupName: string | undefined,
  nearestStopName: string | null | undefined,
): string | null {
  if (locationState === "fallback_nyc") return "34 St–Herald Sq";
  if (locationState === "pending") return "Locating you…";
  return groupName ?? nearestStopName?.trim() ?? null;
}

function nearbyArrivalsState(
  arrivalCount: number,
  arrivalsLoading: boolean | undefined,
  arrivalsUnavailable: boolean | undefined,
): HomeNearbyModel["arrivalsState"] {
  if (arrivalCount > 0) return "ready";
  if (arrivalsLoading) return "loading";
  if (arrivalsUnavailable) return "unavailable";
  return "loading";
}

export function buildHomeNearbyModel({
  data,
  nearestStopName,
  nearestRouteIds = [],
  arrivalsLoading,
  arrivalsUnavailable,
  serviceAlertsLoading,
  serviceAlertsUnavailable,
  nearbyIssues = [],
  hasPlannedRoute = false,
  locationState = "precise_nyc",
  nowMs,
}: BuildHomeNearbyModelInput): HomeNearbyModel {
  if (locationState === "outside_service_area") {
    return {
      locationState,
      locationLabel: "NYC transit only",
      locationNotice:
        "SmartRoute currently covers NYC transit. Tell me an NYC starting point to plan a trip.",
      stationName: null,
      arrivals: [],
      arrivalsState: "outside_service_area",
      condition: { state: "unavailable", label: "NYC service area" },
      issue: null,
    };
  }

  const arrivals = collectHomeArrivals(data.nearbyTransitGroups);
  const nearbyRoutes = normalizedRouteIds([
    ...nearestRouteIds,
    ...data.nearbyTransitGroups.flatMap((group) => group.routeIds),
  ]);
  return {
    locationState,
    locationLabel: locationState === "fallback_nyc" ? "Starting area" : "Near you",
    locationNotice: null,
    stationName: nearbyStationName(
      locationState,
      data.nearbyTransitGroups[0]?.name,
      nearestStopName,
    ),
    arrivals,
    arrivalsState: nearbyArrivalsState(arrivals.length, arrivalsLoading, arrivalsUnavailable),
    condition: nearbyCondition(
      relevantAlert(data.alerts, nearbyRoutes),
      serviceAlertsLoading,
      serviceAlertsUnavailable,
    ),
    issue: selectHomeNearbyIssue({
      issues: nearbyIssues,
      nearbyRouteIds: Array.from(nearbyRoutes),
      hasPlannedRoute,
      nowMs,
    }),
  };
}
