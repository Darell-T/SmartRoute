import {
  buildAlerts,
  buildFeed,
  buildHealth,
  buildLineState,
  buildStation,
} from "./live-data/alerts-feed";
import {
  buildArrivalRows,
  buildNearbyBusArrivals,
  buildNearbySubwayGroups,
} from "./live-data/nearby-arrivals";
import { buildPlan } from "./live-data/route-plan";
import type { BuildLeftRailDataInput, LeftRailLiveData } from "./live-data/types";

export { HALF_MILE_METERS } from "./live-data/constants";
export { buildRouteReasoningInsights } from "./live-data/route-reasoning";
export type { BuildLeftRailDataInput, LeftRailLiveData } from "./live-data/types";

export function buildLeftRailData({
  liveFeed,
  routeSteps,
  routeCandidates,
  activeRouteCandidate,
  switchHeadline,
  routeEntryContext,
  routeEta,
  routeTotalTime,
  serviceAlerts,
  incidents,
  nowMs = Date.now(),
}: BuildLeftRailDataInput): LeftRailLiveData {
  const alerts = buildAlerts(liveFeed?.alerts, serviceAlerts, nowMs);
  const arrivalRows = buildArrivalRows(liveFeed, nowMs);

  return {
    station: buildStation(liveFeed, nowMs),
    health: buildHealth(liveFeed),
    arrivals: arrivalRows.serviceRows,
    nearbyTransitGroups: buildNearbySubwayGroups(arrivalRows.stationRows),
    nearbyBusArrivals: buildNearbyBusArrivals(arrivalRows.serviceRows),
    plan: buildPlan(
      routeSteps,
      activeRouteCandidate,
      routeCandidates,
      switchHeadline,
      routeEta,
      routeTotalTime,
      nowMs,
      routeEntryContext,
    ),
    feed: buildFeed(alerts, incidents, nowMs),
    lineState: buildLineState(alerts),
    alerts,
  };
}
