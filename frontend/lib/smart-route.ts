import type { RouteStep } from "@/types";
import { getLineColor } from "@/components/map/route-layers";

export type LegMode = "walk" | "rail" | "bus" | "transfer";

export interface RouteLeg {
  mode: LegMode;
  line?: string;
  color?: string;
  detail: string;
  min: number;
}

export interface RouteSummary {
  legs: RouteLeg[];
  totalMin: number;
  transitLines: string[];
  transferStation: string | null;
  departLabel: string;
  arriveLabel: string;
  primaryHeadline: {
    prefix: string;
    emphasis: string;
    suffix: string;
  };
}

function estimateStepMinutes(step: RouteStep): number {
  if (step.type === "SUBWAY" || step.type === "BUS") {
    return step.minutes_until_arrival != null
      ? Math.max(1, Math.round(step.minutes_until_arrival))
      : 8;
  }
  // WALK — rough 1 min per 80m from polyline length, fallback 4
  return 4;
}

function formatClockOffset(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function summarizeRoute(steps: RouteStep[], now = new Date()): RouteSummary {
  const legs: RouteLeg[] = [];
  const transitLines: string[] = [];
  let transferStation: string | null = null;
  let totalMin = 0;

  let prevWasTransit: RouteStep | null = null;

  for (const step of steps) {
    const min = estimateStepMinutes(step);
    totalMin += min;

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
      if (prevWasTransit) {
        const station =
          step.departure_stop || prevWasTransit.arrival_stop || "transfer";
        if (!transferStation) transferStation = station;
        legs.push({
          mode: "transfer",
          detail: `Transfer at ${station}`,
          min: 1,
        });
        totalMin += 1;
      }
      const line = step.train_line || (step.type === "BUS" ? "BUS" : "?");
      transitLines.push(line);
      const color =
        step.type === "SUBWAY" ? getLineColor(line) : "#0057B8";
      const depart = step.departure_stop || "";
      const arrive = step.arrival_stop || "";
      const detail = depart && arrive ? `${depart} → ${arrive}` : (depart || arrive || line);
      legs.push({
        mode: step.type === "SUBWAY" ? "rail" : "bus",
        line,
        color,
        detail,
        min,
      });
      prevWasTransit = step;
    }
  }

  const arrive = new Date(now.getTime() + totalMin * 60_000);

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

  return {
    legs,
    totalMin: Math.max(1, totalMin),
    transitLines,
    transferStation,
    departLabel: formatClockOffset(now),
    arriveLabel: formatClockOffset(arrive),
    primaryHeadline: { prefix, emphasis, suffix },
  };
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
  { level: "scan", text: "Scanning incident feeds · @NYCTSubway, 311, Grok." },
  { level: "detect", text: "Cross-referencing live vehicle positions." },
  { level: "reason", text: "Evaluating candidate routes · weighting delays." },
  { level: "reason", text: "Scoring by transfers, congestion, incidents." },
];

export function nowStamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
