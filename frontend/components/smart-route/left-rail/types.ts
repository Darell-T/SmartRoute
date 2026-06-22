/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail data shapes

   Mirrors the canonical interfaces in the design handoff README. Keep these
   stable — server actions, route handlers, and the agent pipeline should
   normalize their payloads to these shapes before they hit the rail.
   ════════════════════════════════════════════════════════════════════════ */

import { MTA_ROUTE_COLORS } from "@/lib/mta-colors";

export type Severity = "major" | "minor" | "watch" | "planned";
export type Direction = "uptown" | "downtown";
export type JarvisState = "standby" | "thinking" | "result" | "error";
export type TabId = "route" | "hub" | "alerts";

export interface Station {
  name: string;
  walk: string;
  dist: string;
  updatedSec: number;
}

export interface Arrival {
  line: string;
  // "both" = crosstown/unknown-heading bus rows that belong under either
  // tab. The toggle itself stays two-state (Direction).
  way: Direction | "both";
  dest: string;
  label: string; // "Now" | "1 min" | "9 min"
  mins: number;
  status: "On Time" | "Delayed";
  stale: boolean;
  mode?: "subway" | "bus";
  stationName?: string;
  stationDistanceM?: number;
  nextArrivals?: Next5Entry[];
}

export interface Next5Entry {
  label: string;
  mins: number;
  track?: string;
  cars?: number;
  crowd?: "light" | "moderate" | "heavy";
  stale: boolean;
}

export interface FeedEvent {
  src: "MTA" | "ATLAS" | "FEED";
  sev: Severity;
  line: string | null;
  title: string;
  time: string;
  detail: string;
}

export interface ServiceAlert {
  sev: Severity;
  kind: "train" | "bus";
  lines: string[];
  title: string;
  sub: string;
  fullText?: string;
  aiContext?: string;
  confidence?: "high" | "medium" | "low";
  affectedStops?: string[];
  direction?: string;
  estClear?: string;
  startedAgo: string;
  lastUpdate: string;
  activity?: { t: string; e: string }[];
}

export interface RouteStep {
  type: "walk" | "board" | "ride" | "exit" | "destination" | "arrive";
  action: string;
  line?: string;
  title: string;
  detail: string;
  duration: string;
}

export interface Alternative {
  line: string;
  dest: string;
  delta: string;
  reason: string;
  sev: "high" | "medium" | "low";
  // Wired alternatives carry the candidate id so a click can activate the
  // route; demo rows omit it and stay inert.
  id?: string;
  status?: "rejected" | "recommended";
}

export interface RouteNote {
  tone: "cyan" | "amber" | "coral" | "sage";
  t: string;
  v: string;
}

export interface RoutePlan {
  headline: string;
  rationale: string;
  eta: string;
  totalTime: string;
  pickedLine: string;
  steps: RouteStep[];
  alternatives: Alternative[];
  notes: RouteNote[];
}

export interface NetworkHealth {
  status: "clear" | "minor" | "disrupted";
  alerts: number;
  lines: number;
  major: number;
  stale: number;
  summary: string;
  affected: string[];
}

export interface IssueItem {
  id: string;
  title: string;
  detail: string;
}

/* ── Tone helpers (pinned to the design token palette) ─────────── */
export type RailToneKey =
  | "muted"
  | "ink"
  | "cyan"
  | "amber"
  | "coral"
  | "sage";

export const RAIL_TONE_COLORS: Record<RailToneKey, string> = {
  muted: "var(--sr-muted)",
  ink: "var(--sr-fg)",
  cyan: "var(--sr-cyan)",
  amber: "var(--sr-amber)",
  coral: "var(--sr-coral)",
  sage: "var(--sr-sage)",
};

/* ── MTA line palette (canonical brand colors) ─────────────────── */
// Single source of truth lives in lib/mta-colors.json.
export const LINE_COLORS: Record<string, string> = MTA_ROUTE_COLORS;

/* Lines whose bullets render with dark ink (white fill would disappear). */
export const LINE_INK_DARK = new Set(["N", "Q", "R", "W", "L"]);

/* Mapping used by the Network Pulse swatch grid (preserves official MTA
   grouping order: IRT → IND → BMT → shuttles). */
export const ALL_LINES: string[] = [
  "1", "2", "3",
  "4", "5", "6",
  "7",
  "A", "C", "E",
  "B", "D", "F", "M",
  "G",
  "J", "Z",
  "L",
  "N", "Q", "R", "W",
  "S",
];

export function sevColor(s: Severity): string {
  if (s === "major") return "var(--sr-coral)";
  if (s === "minor") return "var(--sr-amber)";
  if (s === "planned") return "var(--sr-cyan)";
  return "var(--sr-muted)";
}
