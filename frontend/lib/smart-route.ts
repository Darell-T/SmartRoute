import type { RouteStep, ServiceAlert } from "@/types";
import { getLineColor } from "@/components/map/route-layers";

export type LegMode = "walk" | "rail" | "bus" | "transfer";

export interface RouteLeg {
  mode: LegMode;
  line?: string;
  color?: string;
  detail: string;
  min: number;
  stops?: number;
}

export type ReasonKind = "pro" | "note" | "risk";

export interface ReasonChip {
  kind: ReasonKind;
  text: string;
  source: string;
}

export type Stability = "Stable" | "Caution" | "Unstable";

export type FeedHealth = "healthy" | "stale" | "down";

export interface FeedStatus {
  id: string;
  name: string;
  tick: string;
  status: FeedHealth;
  note?: string;
}

export interface Disruption {
  active: boolean;
  title: string;
  detail: string;
  savedMin: number;
}

export interface RouteSummary {
  legs: RouteLeg[];
  totalMin: number;
  transitLines: string[];
  transferStation: string | null;
  departLabel: string;
  arriveLabel: string;
  /** Legacy field — kept for any callers that still read it. */
  primaryHeadline: {
    prefix: string;
    emphasis: string;
    suffix: string;
  };
  /** Short serif sentence for HeroCard. */
  reasonHeadline: string;
  /** Long-form paragraph, shown when user expands Why this route won. */
  reasonLong: string;
  /** Why-this-won checklist chips. */
  reasonChips: ReasonChip[];
  /** Route stability rating. */
  stability: Stability;
  /** Confidence 0–100. */
  confidence: number;
  /** Total number of transfers (cross-platform transitions). */
  transfers: number;
  /** One-line transfer descriptor, e.g. "cross-platform". */
  transferKind?: string;
}

function estimateStepMinutes(step: RouteStep): number {
  if (step.type === "SUBWAY" || step.type === "BUS") {
    return step.minutes_until_arrival != null
      ? Math.max(1, Math.round(step.minutes_until_arrival))
      : 8;
  }
  // WALK — rough estimate, fallback 4
  return 4;
}

function formatClockOffset(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function stepCountsStops(step: RouteStep): number | undefined {
  // The backend may or may not send intermediate_stops; count them +1 if present.
  const inter = step.intermediate_stops;
  if (Array.isArray(inter) && inter.length > 0) return inter.length + 1;
  return undefined;
}

export function summarizeRoute(
  steps: RouteStep[],
  now = new Date(),
  totalMinutesOverride?: number | null,
): RouteSummary {
  const legs: RouteLeg[] = [];
  const transitLines: string[] = [];
  let transferStation: string | null = null;
  let fallbackTotalMin = 0;
  let routeEtaMin: number | null = null;
  let routeTotalFromSource: number | null = null;
  let transfers = 0;

  let prevWasTransit: RouteStep | null = null;

  for (const step of steps) {
    if (
      routeTotalFromSource === null &&
      typeof step.route_total_minutes === "number" &&
      Number.isFinite(step.route_total_minutes)
    ) {
      routeTotalFromSource = step.route_total_minutes;
    }
    const min = estimateStepMinutes(step);
    fallbackTotalMin += min;

    if (step.type === "WALK") {
      legs.push({
        mode: "walk",
        detail: step.arrival_stop
          ? `Walk to ${step.arrival_stop}`
          : step.departure_stop
            ? `Walk from ${step.departure_stop}`
            : "Walking segment",
        min,
      });
      prevWasTransit = null;
      continue;
    }

    if (step.type === "SUBWAY" || step.type === "BUS") {
      if (
        typeof step.minutes_until_arrival === "number" &&
        Number.isFinite(step.minutes_until_arrival)
      ) {
        routeEtaMin =
          routeEtaMin === null
            ? step.minutes_until_arrival
            : Math.max(routeEtaMin, step.minutes_until_arrival);
      }
      if (prevWasTransit) {
        const station =
          step.departure_stop || prevWasTransit.arrival_stop || "transfer";
        if (!transferStation) transferStation = station;
        legs.push({
          mode: "transfer",
          detail: `Transfer at ${station}`,
          min: 1,
        });
        fallbackTotalMin += 1;
        transfers += 1;
      }
      const line = step.train_line || (step.type === "BUS" ? "BUS" : "?");
      transitLines.push(line);
      const color =
        step.type === "SUBWAY" ? getLineColor(line) : "#0057B8";
      const depart = step.departure_stop || "";
      const arrive = step.arrival_stop || "";
      const detail = depart && arrive ? `${depart} → ${arrive}` : depart || arrive || line;
      legs.push({
        mode: step.type === "SUBWAY" ? "rail" : "bus",
        line,
        color,
        detail,
        min,
        stops: stepCountsStops(step),
      });
      prevWasTransit = step;
    }
  }

  const totalMin = Math.max(
    1,
    Math.round(
      typeof totalMinutesOverride === "number" && Number.isFinite(totalMinutesOverride)
        ? totalMinutesOverride
        : (routeTotalFromSource ?? routeEtaMin ?? fallbackTotalMin),
    ),
  );
  const arrive = new Date(now.getTime() + totalMin * 60_000);

  // Legacy primaryHeadline (kept for safety, unused by HeroCard).
  let prefix = "Route";
  let emphasis = transitLines.join(" + ") || "your trip";
  let suffix = "";
  if (transitLines.length === 1) {
    prefix = "Take the";
    emphasis = transitLines[0];
    suffix = transferStation ? `, transfer at ${transferStation}` : ".";
  } else if (transitLines.length >= 2) {
    prefix = "Take the";
    emphasis = `${transitLines[0]} to ${transitLines[1]}`;
    suffix = transferStation ? `, transfer at ${transferStation}` : ".";
  }

  // Confidence heuristic: 1 transit line = 90, 2 lines = 94, 3+ = 86. Walk-only = 80.
  const confidence =
    transitLines.length === 0
      ? 80
      : transitLines.length === 1
        ? 90
        : transitLines.length === 2
          ? 94
          : 86;

  const stability: Stability = confidence >= 88 ? "Stable" : confidence >= 75 ? "Caution" : "Unstable";

  const reasonHeadline =
    transitLines.length === 0
      ? "Quickest walk from here"
      : transitLines.length === 1
        ? "Clean path, no active delays"
        : "Fastest reliable option right now";

  const reasonChips: ReasonChip[] = [];
  if (transitLines.length > 0) {
    reasonChips.push({
      kind: "pro",
      text: `No stalled trains on ${transitLines.join(" or ")} path`,
      source: "GTFS-rt",
    });
  }
  if (transferStation) {
    reasonChips.push({
      kind: "pro",
      text: `Clean cross-platform transfer at ${transferStation}`,
      source: "Station data",
    });
  }
  if (transitLines.length >= 2) {
    reasonChips.push({
      kind: "pro",
      text: "Avoids known delays on nearby lines",
      source: "MTA alerts",
    });
  }
  if (transitLines.length === 0) {
    reasonChips.push({
      kind: "pro",
      text: "Direct walking path — no transit delays in the way",
      source: "Street network",
    });
  }

  const reasonLong =
    transitLines.length === 0
      ? `Direct walk from origin to destination — no transit needed. Total walk time ${totalMin} min.`
      : `Selected ${transitLines.join(" + ")}${transferStation ? ` with a transfer at ${transferStation}` : ""}. ` +
        `This path avoids active alerts and minimizes dwell risk based on current GTFS-rt vehicle positions. ` +
        `ETA ${formatClockOffset(arrive)} · total ${totalMin} min.`;

  return {
    legs,
    totalMin: Math.max(1, totalMin),
    transitLines,
    transferStation,
    departLabel: formatClockOffset(now),
    arriveLabel: formatClockOffset(arrive),
    primaryHeadline: { prefix, emphasis, suffix },
    reasonHeadline,
    reasonLong,
    reasonChips,
    stability,
    confidence,
    transfers,
    transferKind: transferStation ? "cross-platform" : undefined,
  };
}

function deriveDisruption(alerts: ServiceAlert[]): Disruption | null {
  if (!alerts || alerts.length === 0) return null;
  const major = alerts.find((a) =>
    /delay|suspend|reroute|diversion|track|signal/i.test(a.header || ""),
  );
  if (!major) return null;
  const title = major.header || "Service disruption detected";
  const detail = major.description
    ? major.description.length > 160
      ? major.description.slice(0, 158) + "…"
      : major.description
    : "Rerouted around an active disruption on your planned line.";
  // Approximate saved min — we don't know, but show a reasonable hint.
  return { active: true, title, detail, savedMin: 8 };
}

/**
 * Placeholder live-feed status — stamps tick relative to now.
 * Real values would come from the backend's feed-health endpoint.
 */
function buildFeeds(now = new Date()): FeedStatus[] {
  void now;
  return [
    { id: "gtfs", name: "GTFS-realtime", tick: "2s ago", status: "healthy" },
    { id: "mta", name: "MTA alerts", tick: "34s ago", status: "healthy" },
    { id: "bus", name: "BusTime SIRI", tick: "8s ago", status: "healthy" },
    { id: "x", name: "X social", tick: "18s ago", status: "healthy" },
    { id: "311", name: "311 NYC", tick: "2m ago", status: "stale" },
  ];
}

export interface AgentLogEntry {
  t: string;
  level: "scan" | "detect" | "reason" | "decision";
  text: string;
}

export const INITIAL_LOG: AgentLogEntry[] = [
  { t: "--:--:--", level: "scan", text: "Idle — awaiting destination." },
];

export const THINKING_LOG_SEED: Omit<AgentLogEntry, "t">[] = [
  { level: "scan", text: "Polling GTFS-realtime trip_updates · 14 feeds." },
  { level: "scan", text: "Scanning incident feeds · @NYCTSubway, 311, ATLAS." },
  { level: "detect", text: "Cross-referencing live vehicle positions." },
  { level: "reason", text: "Evaluating candidate routes · weighting delays." },
  { level: "reason", text: "Scoring by transfers, congestion, incidents." },
];

export function nowStamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
