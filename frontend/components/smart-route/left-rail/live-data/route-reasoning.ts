import type { LiveFeedIncident } from "@/types/api";
import type {
  Arrival,
  NearbyTransitGroup,
  RouteReasoningInsight,
  ServiceAlert,
} from "../types";

/* ── Route-evaluation insights ─────────────────────────────────────────
   Public reasoning lines for the planning state, each derived from a real
   fact the rail already holds: nearby station access, live arrivals,
   official service alerts, and (separately) reported live incidents.
   A line is omitted whenever its supporting fact is unavailable — the
   fallback comparison lines are the only unconditional entries. */
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

  // Station access: the closest known entrance and its primary line.
  const closest = groups
    .filter((group) => typeof group.walkMinutes === "number" && group.routeIds.length > 0)
    .sort((left, right) => (left.walkMinutes ?? 99) - (right.walkMinutes ?? 99))[0];
  if (closest) {
    insights.push({
      id: "nearby-access",
      source: "nearby-access",
      priority: 1,
      text: `The closest ${closest.routeIds[0]} entrance is about a ${closest.walkMinutes} min walk.`,
    });
  }

  // Live arrivals: which nearby line has the soonest live train.
  let soonestLine: string | undefined;
  let soonestMins = Number.POSITIVE_INFINITY;
  for (const group of groups) {
    for (const arrival of group.arrivals) {
      const first = arrival.arrivalMinutes[0];
      if (
        typeof first === "number"
        && first < soonestMins
        && arrival.predictionType !== "scheduled"
        && arrival.routeIds[0]
      ) {
        soonestMins = first;
        soonestLine = arrival.routeIds[0];
      }
    }
  }
  if (soonestLine && soonestMins <= 8) {
    insights.push({
      id: "live-arrival",
      source: "live-arrival",
      priority: 2,
      text: `Live arrivals favor the ${soonestLine} right now.`,
    });
  }

  // Official MTA service alerts on nearby lines (kept distinct from
  // reported incidents below).
  const nearbyLines = new Set(groups.flatMap((group) => group.routeIds));
  const alertedLines: string[] = [];
  for (const alert of alerts) {
    for (const line of alert.lines) {
      const normalized = line.trim().toUpperCase();
      if (nearbyLines.has(normalized) && !alertedLines.includes(normalized)) {
        alertedLines.push(normalized);
      }
    }
  }
  if (alertedLines.length > 0) {
    insights.push({
      id: "service-alert",
      source: "service-alert",
      priority: 3,
      text: `Active service alerts on the ${alertedLines.slice(0, 2).join(" and ")} lower confidence on the fastest option.`,
    });
  } else if (nearbyLines.size > 0) {
    insights.push({
      id: "service-alert-clear",
      source: "service-alert",
      priority: 3,
      text: "No service alerts on nearby lines right now.",
    });
  }

  // Reported live incidents: a reliability signal, never a confirmed fact —
  // the copy stays at "reported" and "may affect".
  const incident = incidents?.[0];
  if (incident?.title) {
    const [kind, place] = String(incident.title)
      .split("·")
      .map((part) => part.trim());
    insights.push({
      id: "incident",
      source: "incident",
      priority: 4,
      text:
        kind && place
          ? `${kind} was reported near ${place}, so reliability there is lower.`
          : "A reported incident nearby may affect reliability.",
    });
  }

  // Bus vs subway wait, when both are on the table.
  const soonestBus = busArrivals
    .map((arrival) => ({
      line: arrival.routeIds[0],
      mins: arrival.arrivalMinutes[0],
    }))
    .filter((entry) => entry.line && typeof entry.mins === "number")
    .sort((left, right) => left.mins - right.mins)[0];
  if (soonestBus && Number.isFinite(soonestMins)) {
    insights.push({
      id: "bus-comparison",
      source: "comparison",
      priority: 5,
      text:
        soonestBus.mins > soonestMins
          ? `The ${soonestBus.line} is available, but the wait is longer right now.`
          : `The ${soonestBus.line} arrives sooner than nearby trains right now.`,
    });
  }

  // Closing comparison lines — the only unconditional entries.
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
    text:
      alertedLines.length > 0 || incident
        ? "Prioritizing reliability over the fastest scheduled time."
        : "Prioritizing the fastest dependable option.",
  });

  return insights
    .sort((left, right) => left.priority - right.priority)
    .slice(0, 6);
}
