import type { LiveFeedIncident } from "@/types/api";
import type {
  Arrival,
  NearbyTransitGroup,
  RouteReasoningInsight,
  ServiceAlert,
} from "../types";

function closestAccessGroup(groups: NearbyTransitGroup[]): NearbyTransitGroup | undefined {
  return groups
    .filter((group) => group.walkMinutes != null && group.routeIds.length > 0)
    .sort((left, right) => (left.walkMinutes ?? 99) - (right.walkMinutes ?? 99))[0];
}

function soonestLiveTrain(
  groups: NearbyTransitGroup[],
): { line: string; mins: number } | undefined {
  let line: string | undefined;
  let mins = Number.POSITIVE_INFINITY;
  for (const group of groups) {
    for (const arrival of group.arrivals) {
      const first = arrival.arrivalMinutes[0];
      if (first == null || first >= mins) continue;
      if (arrival.predictionType === "scheduled" || !arrival.routeIds[0]) continue;
      mins = first;
      line = arrival.routeIds[0];
    }
  }
  return line === undefined ? undefined : { line, mins };
}

function nearbyAlertedLines(groups: NearbyTransitGroup[], alerts: ServiceAlert[]): string[] {
  const nearbyLines = new Set(groups.flatMap((group) => group.routeIds));
  const alerted: string[] = [];
  for (const alert of alerts) {
    for (const line of alert.lines) {
      const normalized = line.trim().toUpperCase();
      if (nearbyLines.has(normalized) && !alerted.includes(normalized)) {
        alerted.push(normalized);
      }
    }
  }
  return alerted;
}

function incidentReliabilityText(incident: LiveFeedIncident | undefined): string | undefined {
  if (!incident?.title) return undefined;
  const [kind, place] = String(incident.title).split("·").map((part) => part.trim());
  if (kind && place) return `${kind} was reported near ${place}, so reliability there is lower.`;
  return "A reported incident nearby may affect reliability.";
}

function soonestBusWait(busArrivals: Arrival[]): { line: string; mins: number } | undefined {
  return busArrivals
    .map((arrival) => ({
      line: arrival.routeIds[0],
      mins: arrival.arrivalMinutes[0],
    }))
    .filter((entry): entry is { line: string; mins: number } =>
      Boolean(entry.line) && entry.mins != null,
    )
    .sort((left, right) => left.mins - right.mins)[0];
}

function serviceAlertInsights(
  nearbyCount: number,
  alertedLines: string[],
): RouteReasoningInsight[] {
  if (alertedLines.length > 0) {
    return [{
      id: "service-alert",
      source: "service-alert",
      priority: 3,
      text: `Active service alerts on the ${alertedLines.slice(0, 2).join(" and ")} lower confidence on the fastest option.`,
    }];
  }
  if (nearbyCount > 0) {
    return [{
      id: "service-alert-clear",
      source: "service-alert",
      priority: 3,
      text: "No service alerts on nearby lines right now.",
    }];
  }
  return [];
}

function busComparisonInsight(
  soonestBus: { line: string; mins: number } | undefined,
  soonest: { line: string; mins: number } | undefined,
): RouteReasoningInsight | undefined {
  if (!soonestBus || !soonest || !Number.isFinite(soonest.mins)) return undefined;
  const longerWait = soonestBus.mins > soonest.mins;
  return {
    id: "bus-comparison",
    source: "comparison",
    priority: 5,
    text: longerWait
      ? `The ${soonestBus.line} is available, but the wait is longer right now.`
      : `The ${soonestBus.line} arrives sooner than nearby trains right now.`,
  };
}

export function buildRouteReasoningInsights({
  groups,
  busArrivals,
  alerts,
  incidents,
}: {
  groups: NearbyTransitGroup[];
  busArrivals: Arrival[];
  alerts: ServiceAlert[];
  incidents?: LiveFeedIncident[];
}): RouteReasoningInsight[] {
  const insights: RouteReasoningInsight[] = [];
  const closest = closestAccessGroup(groups);
  if (closest) {
    insights.push({
      id: "nearby-access",
      source: "nearby-access",
      priority: 1,
      text: `The closest ${closest.routeIds[0]} entrance is about a ${closest.walkMinutes} min walk.`,
    });
  }

  const soonest = soonestLiveTrain(groups);
  if (soonest && soonest.mins <= 8) {
    insights.push({
      id: "live-arrival",
      source: "live-arrival",
      priority: 2,
      text: `Live arrivals favor the ${soonest.line} right now.`,
    });
  }

  const nearbyLines = new Set(groups.flatMap((group) => group.routeIds));
  const alertedLines = nearbyAlertedLines(groups, alerts);
  insights.push(...serviceAlertInsights(nearbyLines.size, alertedLines));

  const incidentText = incidentReliabilityText(incidents?.[0]);
  if (incidentText) {
    insights.push({
      id: "incident",
      source: "incident",
      priority: 4,
      text: incidentText,
    });
  }

  const busInsight = busComparisonInsight(soonestBusWait(busArrivals), soonest);
  if (busInsight) insights.push(busInsight);

  insights.push({
    id: "comparison",
    source: "comparison",
    priority: 8,
    text: "Comparing total time, walking distance, and transfers.",
  });
  insights.push({
    id: "weighting",
    source: "comparison",
    priority: 9,
    text: alertedLines.length > 0 || incidentText
      ? "Prioritizing reliability over the fastest scheduled time."
      : "Prioritizing the fastest dependable option.",
  });

  return insights.sort((left, right) => left.priority - right.priority).slice(0, 6);
}
