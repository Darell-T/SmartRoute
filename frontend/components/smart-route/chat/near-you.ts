/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — "Near You" derivation

   Pure helpers only (no React, no fetching). The route-id list mirrors the
   left rail's own `nearbyRouteIds` derivation (left-rail.tsx lines ~94-106:
   nearbyTransitGroups + arrivals + nearbyBusArrivals, deduped, uppercased)
   so the chat top bar's bullets are "the same derivation the left rail
   uses" per the design spec, without importing rail internals or standing
   up a second WebSocket — the caller passes down the `LeftRailLiveData`
   page.tsx already computes from the one shared `useLiveFeed` connection.
   ════════════════════════════════════════════════════════════════════════ */

import type {
  Arrival,
  NearbyTransitGroup,
  ServiceAlert,
} from "@/components/smart-route/left-rail/types";
import type { LeftRailLiveData } from "@/components/smart-route/left-rail/live-data";
import type { ArrivalsTurnDirectionGroup, ArrivalsTurnPayload } from "@/lib/use-agent-chat";
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
  const arrivals: HomeNearbyArrival[] = [];
  const seen = new Set<string>();

  for (const group of data.nearbyTransitGroups) {
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
      if (arrivals.length === MAX_HOME_ARRIVALS) break;
    }
    if (arrivals.length === MAX_HOME_ARRIVALS) break;
  }

  const nearbyRoutes = normalizedRouteIds([
    ...nearestRouteIds,
    ...data.nearbyTransitGroups.flatMap((group) => group.routeIds),
  ]);
  const alert = relevantAlert(data.alerts, nearbyRoutes);

  let condition: HomeNearbyModel["condition"];
  if (alert) {
    condition = { state: "alert", label: conciseAlertSummary(alert) };
  } else if (serviceAlertsLoading) {
    condition = { state: "loading", label: "Checking nearby service status" };
  } else if (serviceAlertsUnavailable) {
    condition = { state: "unavailable", label: "Service status unavailable" };
  } else {
    condition = { state: "clear", label: "No active service changes nearby" };
  }
  const issue = selectHomeNearbyIssue({
    issues: nearbyIssues,
    nearbyRouteIds: Array.from(nearbyRoutes),
    hasPlannedRoute,
    nowMs,
  });

  let stationName: string | null;
  if (locationState === "fallback_nyc") {
    stationName = "34 St–Herald Sq";
  } else if (locationState === "pending") {
    stationName = "Locating you…";
  } else {
    stationName = data.nearbyTransitGroups[0]?.name ?? nearestStopName?.trim() ?? null;
  }

  let arrivalsState: HomeNearbyModel["arrivalsState"];
  if (arrivals.length > 0) {
    arrivalsState = "ready";
  } else if (arrivalsLoading) {
    arrivalsState = "loading";
  } else if (arrivalsUnavailable) {
    arrivalsState = "unavailable";
  } else {
    arrivalsState = "loading";
  }

  return {
    locationState,
    locationLabel: locationState === "fallback_nyc" ? "Starting area" : "Near you",
    locationNotice: null,
    stationName,
    arrivals,
    arrivalsState,
    condition,
    issue,
  };
}

/** Up to `limit` route ids the rider is standing near, nearest first (the
 *  inputs are already proximity-sorted by `buildLeftRailData`). */
export function deriveNearbyRouteIds(data: {
  nearbyTransitGroups?: NearbyTransitGroup[];
  arrivals?: Arrival[];
  nearbyBusArrivals?: Arrival[];
}): string[] {
  const seen = new Set<string>();
  for (const group of data.nearbyTransitGroups ?? []) {
    for (const routeId of group.routeIds) seen.add(routeId.toUpperCase());
  }
  for (const arrival of data.arrivals ?? []) {
    for (const routeId of arrival.routeIds) seen.add(routeId.toUpperCase());
  }
  for (const arrival of data.nearbyBusArrivals ?? []) {
    for (const routeId of arrival.routeIds) seen.add(routeId.toUpperCase());
  }
  return Array.from(seen);
}

/** The nearest nearby-transit-group's station name that actually serves
 *  `routeId`, falling back to the rider's overall nearest stop. Used as the
 *  ArrivalsCard header ("125 St") when a Near You bullet is tapped. */
export function stationNameForRoute(
  routeId: string,
  nearbyTransitGroups: NearbyTransitGroup[],
  fallback: string,
): string {
  const normalized = routeId.toUpperCase();
  const group = nearbyTransitGroups.find((candidate) =>
    candidate.routeIds.some((id) => id.toUpperCase() === normalized),
  );
  return group?.name ?? fallback;
}

const DIRECTION_LABEL: Record<"uptown" | "downtown", string> = {
  uptown: "Uptown",
  downtown: "Downtown",
};

/** Groups the rider's live arrivals for one route into the two direction
 *  buckets an `ArrivalsCard` renders ("Uptown · 2, 7, 12 min"). Arrivals
 *  with an unresolved direction ("unknown" — mostly buses on an E/W street)
 *  are omitted rather than guessed at. */
export function buildArrivalsPayloadForRoute(
  routeId: string,
  arrivals: Arrival[],
  stationName: string,
  station?: {
    walkMinutes?: number;
    distanceMiles?: number;
    coordinates?: { lat: number; lng: number };
  },
): ArrivalsTurnPayload {
  const normalized = routeId.toUpperCase();
  const minutesByDirection = new Map<"uptown" | "downtown", number[]>();

  for (const arrival of arrivals) {
    if (!arrival.routeIds.some((id) => id.toUpperCase() === normalized)) continue;
    if (arrival.direction !== "uptown" && arrival.direction !== "downtown") continue;
    const bucket = minutesByDirection.get(arrival.direction) ?? [];
    bucket.push(...arrival.arrivalMinutes.filter((minutes) => minutes > 0));
    minutesByDirection.set(arrival.direction, bucket);
  }

  const groups: ArrivalsTurnDirectionGroup[] = [];
  for (const direction of ["uptown", "downtown"] as const) {
    const minutes = minutesByDirection.get(direction);
    if (!minutes || minutes.length === 0) continue;
    const sorted = Array.from(new Set(minutes)).sort((a, b) => a - b).slice(0, 3);
    groups.push({ direction, label: DIRECTION_LABEL[direction], minutes: sorted });
  }

  const guidanceParts: string[] = [];
  if (station?.walkMinutes !== undefined) {
    guidanceParts.push(`${station.walkMinutes} min walk`);
  }
  if (station?.distanceMiles !== undefined) {
    guidanceParts.push(`${station.distanceMiles.toFixed(1)} mi away`);
  }

  return {
    routeId: normalized,
    stationName,
    stationGuidance: guidanceParts.length > 0 ? guidanceParts.join(" · ") : undefined,
    stationCoordinates: station?.coordinates,
    groups,
  };
}
