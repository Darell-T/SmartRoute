import type {
  LiveFeedIncident,
  LiveFeedResponse,
  ServiceAlertDetail,
} from "@/types/api";
import type { FeedEvent, NetworkHealth, ServiceAlert, Station } from "../types";
import { formatDistance, formatWalk, minutesAgo, secondsSince } from "./formatters";
import type { LineState } from "./types";

export function buildStation(liveFeed: Partial<LiveFeedResponse> | null | undefined, nowMs: number): Station {
  const nearest = liveFeed?.nearest_stop ?? null;
  return {
    name: "Nearby transit",
    walk: formatWalk(nearest?.distance_m),
    dist: formatDistance(nearest?.distance_m),
    updatedSec: secondsSince(liveFeed?.updated_at, nowMs),
  };
}

export function buildHealth(liveFeed: Partial<LiveFeedResponse> | null | undefined): NetworkHealth {
  const signals = liveFeed?.signals;
  const rawStatus = signals?.network_status ?? (liveFeed?.degraded ? "caution" : "healthy");
  const status: NetworkHealth["status"] =
    rawStatus === "disrupted" ? "disrupted" : rawStatus === "caution" ? "minor" : "clear";
  const affected = Array.from(
    new Set(
      (liveFeed?.stops ?? [])
        .flatMap((stop) => stop.route_ids ?? [])
        .map((routeId) => String(routeId).toUpperCase()),
    ),
  ).sort();

  return {
    status,
    alerts: signals?.active_alert_count ?? liveFeed?.alerts?.length ?? 0,
    lines: signals?.affected_route_count ?? affected.length,
    major: signals?.major_alert_count ?? 0,
    stale: signals?.stale_vehicle_count ?? 0,
    summary: `${affected.length || "Nearby"} subway routes are being monitored inside a half-mile radius.`,
    affected: affected.slice(0, 12),
  };
}

function severityFromAlert(alert: ServiceAlertDetail): "major" | "minor" | "planned" {
  const text = `${alert.header ?? ""} ${alert.description ?? ""}`.toLowerCase();
  if (text.includes("suspend") || text.includes("no ") || text.includes("bypass")) {
    return "major";
  }
  if (text.includes("planned") || text.includes("weekend")) return "planned";
  return "minor";
}

export function buildAlerts(
  liveAlerts: ServiceAlertDetail[] | undefined,
  extraAlerts: ServiceAlertDetail[] | undefined,
  nowMs: number,
): ServiceAlert[] {
  const allAlerts = [...(liveAlerts ?? []), ...(extraAlerts ?? [])];
  const seen = new Set<string>();
  return allAlerts
    .filter((alert) => {
      const key = alert.alert_id ?? `${alert.header}:${alert.description}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 12)
    .map((alert) => {
      const routes = (alert.route_ids ?? alert.routeIds ?? []).map((routeId) => String(routeId));
      const severity = severityFromAlert(alert);
      return {
        sev: severity,
        kind: "train",
        lines: routes,
        title: alert.header || "MTA service alert",
        sub: alert.description || "Active service notice",
        fullText: [alert.header, alert.description]
          .map((part) => String(part ?? "").trim())
          .filter(Boolean)
          .join("\n\n"),
        confidence: "high",
        affectedStops: alert.stop_names?.length ? alert.stop_names : undefined,
        startedAgo: minutesAgo(alert.start, nowMs),
        lastUpdate: minutesAgo(alert.end ?? alert.start, nowMs),
      };
    });
}

export function buildLineState(alerts: ServiceAlert[]): LineState {
  const state: LineState = {};
  for (const alert of alerts) {
    for (const line of alert.lines) {
      const current = state[line];
      if (current === "major" || alert.sev === "watch") continue;
      state[line] = alert.sev === "major" ? "major" : alert.sev === "planned" ? "planned" : "minor";
    }
  }
  return state;
}

export function buildFeed(alerts: ServiceAlert[], incidents: LiveFeedIncident[] | undefined, nowMs: number): FeedEvent[] {
  const alertEvents = alerts.slice(0, 4).map((alert) => ({
    src: "MTA" as const,
    sev: alert.sev,
    line: alert.lines[0] ?? null,
    title: alert.title,
    time: alert.startedAgo,
    detail: alert.sub,
  }));
  const incidentEvents = (incidents ?? []).slice(0, 4).map((incident) => ({
    src: "SYSTEM" as const,
    sev: incident.severity === "critical" || incident.severity === "high" ? "major" as const : "minor" as const,
    line: incident.routeIds?.[0] ?? null,
    title: incident.title,
    time: minutesAgo(incident.updated_at, nowMs),
    detail: incident.detail ?? "Nearby incident",
  }));
  return [...alertEvents, ...incidentEvents].slice(0, 8);
}
