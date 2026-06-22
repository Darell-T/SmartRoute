import type {
  LiveFeedIncident,
  LiveFeedResponse,
  RouteCandidate,
  RouteStep as ApiRouteStep,
  ServiceAlertDetail,
} from "../../../types/api";
import type {
  Alternative,
  Arrival,
  FeedEvent,
  NetworkHealth,
  Next5Entry,
  RoutePlan,
  ServiceAlert,
  Station,
} from "./types";

export const HALF_MILE_METERS = 804.672;

type LineState = Record<string, "major" | "minor" | "planned">;

export interface BuildLeftRailDataInput {
  liveFeed?: Partial<LiveFeedResponse> | null;
  routeSteps?: ApiRouteStep[];
  routeCandidates?: RouteCandidate[];
  activeRouteCandidate?: RouteCandidate | null;
  // Canned line shown after the user switches to an alternative; overrides
  // the default plan headline until the next trip/clear.
  switchHeadline?: string | null;
  // What ATLAS actually says about the picked route (the spoken narration);
  // shown as the plan rationale so the card reflects the recommendation.
  recommendationText?: string | null;
  // Real ETA for the picked route: arrival clock time + trip duration. Replaces
  // the placeholder "Live · Calculated".
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
  plan: RoutePlan;
  feed: FeedEvent[];
  lineState: LineState;
  alerts: ServiceAlert[];
}

function secondsSince(epochSeconds: number | null | undefined, nowMs: number): number {
  if (!epochSeconds) return 0;
  return Math.max(0, Math.round(nowMs / 1000 - epochSeconds));
}

function formatDistance(meters: number | null | undefined): string {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return "nearby";
  if (meters < 160) return `${Math.round(meters)} m`;
  return `${Math.max(0.1, meters / 1609.344).toFixed(1)} mi`;
}

function formatWalk(meters: number | null | undefined): string {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return "nearby";
  return `${Math.max(1, Math.round(meters / 84))} min walk`;
}

function normalizeWay(direction: unknown, stopId?: string): "uptown" | "downtown" {
  const label = String(direction ?? "").toLowerCase();
  if (label.includes("downtown") || label.includes("south")) return "downtown";
  if (label.includes("uptown") || label.includes("north")) return "uptown";
  const suffix = String(stopId ?? "").slice(-1).toUpperCase();
  if (suffix === "S") return "downtown";
  return "uptown";
}

function busWay(stopCompass: unknown): "uptown" | "downtown" | "both" {
  // OBA bus stops carry a compass heading (NE, SW, E, ...). Manhattan's
  // grid tilt means uptown service reads as N/NE/NW and downtown as
  // S/SE/SW. Pure E/W is true crosstown -- and anything unknown degrades
  // the same way: visible in both tabs, never silently dropped.
  const compass = String(stopCompass ?? "").toUpperCase();
  if (compass.includes("N")) return "uptown";
  if (compass.includes("S")) return "downtown";
  return "both";
}

function labelForMinutes(mins: number): string {
  if (mins <= 0) return "Now";
  if (mins === 1) return "1 min";
  return `${mins} min`;
}

function stationNameForArrival(arrival: Record<string, unknown>): string | undefined {
  return (
    String(arrival.station_name ?? arrival.parent_stop_name ?? "").trim()
    || undefined
  );
}

function stationDistanceForArrival(arrival: Record<string, unknown>): number | undefined {
  const distance = Number(arrival.distance_m);
  return Number.isFinite(distance) ? distance : undefined;
}

function isInsideHalfMile(distanceM: number | undefined): boolean {
  return typeof distanceM !== "number" || distanceM <= HALF_MILE_METERS;
}

function directionKey(arrival: Arrival): string {
  // SIRI bus DirectionRef is an opaque 0/1, so uptown/downtown normalization
  // collapses both directions of a route. The destination name is the only
  // stable direction signal for buses; subway keeps the uptown/downtown axis.
  return arrival.mode === "bus" ? arrival.dest : arrival.way;
}

function arrivalKey(arrival: Arrival): string {
  return `${arrival.mode ?? "subway"}|${arrival.line}|${directionKey(arrival)}|${arrival.stationName ?? ""}`;
}

function serviceKey(arrival: Arrival): string {
  return `${arrival.mode ?? "subway"}|${arrival.line}|${directionKey(arrival)}`;
}

function compareArrivalTime(left: Arrival, right: Arrival): number {
  return left.mins - right.mins;
}

function compareStationPriority(left: Arrival, right: Arrival): number {
  const leftDistance = left.stationDistanceM ?? Number.POSITIVE_INFINITY;
  const rightDistance = right.stationDistanceM ?? Number.POSITIVE_INFINITY;
  if (leftDistance !== rightDistance) return leftDistance - rightDistance;
  return compareArrivalTime(left, right);
}

function nextEntriesForBucket(bucket: Arrival[]): Next5Entry[] {
  return bucket.slice(0, 5).map((arrival) => ({
    label: arrival.label,
    mins: arrival.mins,
    stale: arrival.stale,
  }));
}

function buildStationBuckets(arrivals: Arrival[]): Arrival[] {
  const groupedByStation = new Map<string, Arrival[]>();
  for (const arrival of arrivals) {
    const key = arrivalKey(arrival);
    const bucket = groupedByStation.get(key) ?? [];
    bucket.push(arrival);
    groupedByStation.set(key, bucket);
  }

  const stationBuckets: Arrival[] = [];
  for (const bucket of groupedByStation.values()) {
    bucket.sort(compareArrivalTime);
    stationBuckets.push({
      ...bucket[0],
      nextArrivals: nextEntriesForBucket(bucket),
    });
  }
  return stationBuckets;
}

function collapseToServiceRows(arrivals: Arrival[]): Arrival[] {
  const stationBuckets = buildStationBuckets(arrivals);
  const groupedByService = new Map<string, Arrival[]>();
  for (const arrival of stationBuckets) {
    const key = serviceKey(arrival);
    const bucket = groupedByService.get(key) ?? [];
    bucket.push(arrival);
    groupedByService.set(key, bucket);
  }

  const serviceRows: Arrival[] = [];
  for (const bucket of groupedByService.values()) {
    bucket.sort(compareStationPriority);
    serviceRows.push(bucket[0]);
  }

  serviceRows.sort((left, right) => {
    const time = compareArrivalTime(left, right);
    if (time !== 0) return time;
    return compareStationPriority(left, right);
  });
  return serviceRows;
}

function buildArrivalList(
  liveFeed: Partial<LiveFeedResponse> | null | undefined,
  nowMs = Date.now(),
): Arrival[] {
  const arrivals: Arrival[] = [];
  for (const raw of liveFeed?.arrivals ?? []) {
    const arrival = raw as unknown as Record<string, unknown>;
    const arrivalTime = Number(arrival.arrival_time);
    if (!Number.isFinite(arrivalTime)) continue;
    const line = String(arrival.route_id ?? "").trim().toUpperCase();
    if (!line) continue;
    const mins = Math.max(0, Math.round((arrivalTime - nowMs / 1000) / 60));
    const distanceM = stationDistanceForArrival(arrival);
    if (!isInsideHalfMile(distanceM)) continue;
    const delay = Number(arrival.delay);
    const stopId = String(arrival.stop_id ?? "");
    const mode: "subway" | "bus" =
      String(arrival.mode ?? "subway") === "bus" ? "bus" : "subway";
    arrivals.push({
      line,
      way: mode === "bus" ? busWay(arrival.stop_compass) : normalizeWay(arrival.direction, stopId),
      dest: String(arrival.terminal_stop_name ?? arrival.direction ?? "Terminal").trim(),
      label: labelForMinutes(mins),
      mins,
      status: Number.isFinite(delay) && delay >= 300 ? "Delayed" : "On Time",
      stale: arrivalTime < nowMs / 1000 - 60,
      mode,
      stationName: stationNameForArrival(arrival),
      stationDistanceM: distanceM,
    });
  }

  arrivals.sort((left, right) => left.mins - right.mins);
  return collapseToServiceRows(arrivals).slice(0, 48);
}

function buildStation(liveFeed: Partial<LiveFeedResponse> | null | undefined, nowMs: number): Station {
  const nearest = liveFeed?.nearest_stop ?? null;
  return {
    name: "Nearby transit",
    walk: formatWalk(nearest?.distance_m),
    dist: formatDistance(nearest?.distance_m),
    updatedSec: secondsSince(liveFeed?.updated_at, nowMs),
  };
}

function buildHealth(liveFeed: Partial<LiveFeedResponse> | null | undefined): NetworkHealth {
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
    summary:
      liveFeed?.summary?.body
      ?? `${affected.length || "Nearby"} subway routes are being monitored inside a half-mile radius.`,
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

function minutesAgo(epochSeconds: number | null | undefined, nowMs: number): string {
  if (!epochSeconds) return "live";
  const minutes = Math.max(0, Math.round((nowMs / 1000 - epochSeconds) / 60));
  if (minutes < 1) return "now";
  if (minutes === 1) return "1m";
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round(minutes / 60)}h`;
}

function buildAlerts(
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

function buildLineState(alerts: ServiceAlert[]): LineState {
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

function buildFeed(alerts: ServiceAlert[], incidents: LiveFeedIncident[] | undefined, nowMs: number): FeedEvent[] {
  const alertEvents = alerts.slice(0, 4).map((alert) => ({
    src: "MTA" as const,
    sev: alert.sev,
    line: alert.lines[0] ?? null,
    title: alert.title,
    time: alert.startedAgo,
    detail: alert.sub,
  }));
  const incidentEvents = (incidents ?? []).slice(0, 4).map((incident) => ({
    src: "ATLAS" as const,
    sev: incident.severity === "critical" || incident.severity === "high" ? "major" as const : "minor" as const,
    line: incident.routeIds?.[0] ?? null,
    title: incident.title,
    time: minutesAgo(incident.updated_at, nowMs),
    detail: incident.detail ?? "Nearby incident",
  }));
  return [...alertEvents, ...incidentEvents].slice(0, 8);
}

function routeStepToRailStep(step: ApiRouteStep, index: number) {
  if (step.type === "WALK") {
    return {
      type: index === 0 ? "walk" as const : "exit" as const,
      action: "Walk",
      title: step.departure_stop || step.arrival_stop || "Walk",
      detail: step.arrival_stop ? `To ${step.arrival_stop}` : "Continue on foot",
      duration: typeof step.minutes_until_arrival === "number" ? `${Math.round(step.minutes_until_arrival)} min` : "walk",
    };
  }

  const line = step.train_line || step.route_id || (step.type === "BUS" ? "BUS" : "");
  return {
    type: index === 0 ? "board" as const : "ride" as const,
    action: index === 0 ? "Board" : "Ride",
    line,
    title: `${line} ${step.type === "BUS" ? "bus" : "train"}`,
    detail: step.direction || step.arrival_stop || "Transit segment",
    duration: typeof step.minutes_until_arrival === "number" ? `${Math.round(step.minutes_until_arrival)} min` : "live",
  };
}

function routeEtaMinutes(steps: ApiRouteStep[] | undefined): number | null {
  const sourceTotal = steps?.find(
    (step) =>
      typeof step.route_total_minutes === "number" &&
      Number.isFinite(step.route_total_minutes),
  )?.route_total_minutes;
  if (typeof sourceTotal === "number" && Number.isFinite(sourceTotal)) {
    return Math.max(1, Math.round(sourceTotal));
  }
  // Trip steps carry minutes relative to now; the largest arrival figure is
  // the trip's ETA. Good enough for candidate-vs-candidate deltas.
  let max: number | null = null;
  for (const step of steps ?? []) {
    const minutes = step.minutes_until_arrival;
    if (typeof minutes === "number" && Number.isFinite(minutes)) {
      max = max === null ? minutes : Math.max(max, minutes);
    }
  }
  return max;
}

function candidateEtaMinutes(candidate: RouteCandidate | null | undefined): number | null {
  if (
    typeof candidate?.total_minutes === "number" &&
    Number.isFinite(candidate.total_minutes)
  ) {
    return Math.max(1, Math.round(candidate.total_minutes));
  }
  return routeEtaMinutes(candidate?.steps);
}

function formatClockAt(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function candidateDelta(candidate: RouteCandidate, active: RouteCandidate | null | undefined): { delta: string; sev: "high" | "medium" | "low" } {
  const candidateEta = candidateEtaMinutes(candidate);
  const activeEta = candidateEtaMinutes(active);
  if (candidateEta === null || activeEta === null) {
    return { delta: "n/a", sev: "low" };
  }
  const diff = Math.round(candidateEta - activeEta);
  const delta = diff === 0 ? "same time" : `${diff > 0 ? "+" : ""}${diff} min`;
  const magnitude = Math.abs(diff);
  const sev = magnitude >= 8 ? "high" : magnitude >= 3 ? "medium" : "low";
  return { delta, sev };
}

function firstTransitStep(steps: ApiRouteStep[] | undefined): ApiRouteStep | undefined {
  // Local rather than importing lib/route-planning: this file is exercised
  // by the node --test runner, which cannot resolve extensionless VALUE
  // imports from .ts (type-only imports are erased and fine).
  return steps?.find((step) => step.type === "SUBWAY" || step.type === "BUS");
}

function buildAlternatives(
  routeCandidates: RouteCandidate[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
): Alternative[] {
  if (!routeCandidates?.length || !activeRouteCandidate) return [];
  return routeCandidates
    .filter((candidate) => candidate.id !== activeRouteCandidate.id)
    .map((candidate) => {
      const transit = firstTransitStep(candidate.steps);
      const { delta, sev } = candidateDelta(candidate, activeRouteCandidate);
      const breakdown = candidate.score_breakdown;
      const generatedReason =
        typeof breakdown?.duration_minutes === "number"
          ? `${Math.round(breakdown.duration_minutes)} min, ${breakdown.transfers ?? 0} transfer(s), ${breakdown.active_alerts ?? 0} active alert(s).`
          : "";
      return {
        id: candidate.id,
        line: (transit?.route_id || transit?.train_line || "WALK").toUpperCase(),
        dest: transit?.direction || transit?.arrival_stop || "Alternate routing",
        delta,
        sev,
        reason: candidate.rejection_reason ?? candidate.recommendation_reason ?? generatedReason,
        status: candidate.is_recommended ? ("recommended" as const) : ("rejected" as const),
      };
    });
}

function mergeConsecutiveWalks(steps: ApiRouteStep[]): ApiRouteStep[] {
  // Google Routes can split walking into back-to-back legs (approach + final
  // walk). The rail should show one "Walk" row per continuous walk, not a
  // repeated "Continue on foot". Fold a run of WALK steps into the first one,
  // extending its end to the run's last stop and keeping the later arrival ETA.
  const out: ApiRouteStep[] = [];
  for (const step of steps) {
    const prev = out[out.length - 1];
    if (step.type === "WALK" && prev?.type === "WALK") {
      out[out.length - 1] = {
        ...prev,
        arrival_stop: step.arrival_stop || prev.arrival_stop,
        minutes_until_arrival: step.minutes_until_arrival ?? prev.minutes_until_arrival,
      };
    } else {
      out.push(step);
    }
  }
  return out;
}

function buildPlan(
  routeSteps: ApiRouteStep[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
  routeCandidates?: RouteCandidate[],
  switchHeadline?: string | null,
  recommendationText?: string | null,
  routeEta?: string | null,
  routeTotalTime?: string | null,
  nowMs = Date.now(),
): RoutePlan {
  const transitStep = routeSteps?.find((step) => step.type === "SUBWAY" || step.type === "BUS");
  const line = transitStep?.train_line || transitStep?.route_id || "";
  const merged = mergeConsecutiveWalks(routeSteps ?? []);
  const steps: RoutePlan["steps"] = merged.map(routeStepToRailStep);
  // The final step of any plan is the destination: relabel it "Arrive" with
  // the celebratory checkered-flag icon, naming where you end up.
  if (steps.length > 0) {
    const lastRaw = merged[merged.length - 1];
    const dest = lastRaw.arrival_stop || lastRaw.departure_stop || steps[steps.length - 1].title || "Destination";
    steps[steps.length - 1] = {
      ...steps[steps.length - 1],
      type: "arrive",
      action: "Arrive",
      title: dest,
      detail: "You've arrived",
      duration: "",
    };
  }

  const defaultHeadline = activeRouteCandidate
    ? activeRouteCandidate.is_recommended === false
      ? "Alternative route engaged."
      : "Route plan is live."
    : "Choose a destination for route guidance.";

  // What ATLAS says about the picked route leads the card; fall back to the
  // candidate's reason, then the live-feed default.
  const spoken = activeRouteCandidate ? recommendationText?.trim() : "";
  const selectedEtaMinutes = candidateEtaMinutes(activeRouteCandidate);
  const selectedEta =
    selectedEtaMinutes !== null && nowMs > 0
      ? formatClockAt(nowMs + selectedEtaMinutes * 60_000)
      : null;
  const selectedTotalTime =
    selectedEtaMinutes !== null ? `${selectedEtaMinutes} min` : null;
  return {
    headline: switchHeadline ?? defaultHeadline,
    rationale: (spoken || undefined)
      ?? activeRouteCandidate?.recommendation_reason
      ?? activeRouteCandidate?.rejection_reason
      ?? "Nearby arrivals are live within a half-mile radius.",
    eta: (activeRouteCandidate && (routeEta || selectedEta)) || "Live",
    totalTime: (activeRouteCandidate && (routeTotalTime || selectedTotalTime)) || (steps.length ? "Calculated" : "Pending"),
    pickedLine: line,
    steps,
    alternatives: buildAlternatives(routeCandidates, activeRouteCandidate),
    notes: [],
  };
}

export function buildLeftRailData({
  liveFeed,
  routeSteps,
  routeCandidates,
  activeRouteCandidate,
  switchHeadline,
  recommendationText,
  routeEta,
  routeTotalTime,
  serviceAlerts,
  incidents,
  nowMs = Date.now(),
}: BuildLeftRailDataInput): LeftRailLiveData {
  const alerts = buildAlerts(liveFeed?.alerts, serviceAlerts, nowMs);
  return {
    station: buildStation(liveFeed, nowMs),
    health: buildHealth(liveFeed),
    arrivals: buildArrivalList(liveFeed, nowMs),
    plan: buildPlan(routeSteps, activeRouteCandidate, routeCandidates, switchHeadline, recommendationText, routeEta, routeTotalTime, nowMs),
    feed: buildFeed(alerts, incidents, nowMs),
    lineState: buildLineState(alerts),
    alerts,
  };
}
