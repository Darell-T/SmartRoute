/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail data shapes

   Mirrors the canonical interfaces in the design handoff README. Keep these
   stable — server actions, route handlers, and the agent pipeline should
   normalize their payloads to these shapes before they hit the rail.
   ════════════════════════════════════════════════════════════════════════ */

import { MTA_ROUTE_COLORS } from "@/lib/mta-colors";

export type Severity = "major" | "minor" | "watch" | "planned";
export type Direction = "uptown" | "downtown";
export type NearbyTransitDirection = Direction | "unknown";
export type RouteRailStatus = "standby" | "thinking" | "result" | "error";
export type TabId = "route" | "alerts";

export interface Station {
  name: string;
  walk: string;
  dist: string;
  updatedSec: number;
}

export interface NearbyTransitDisplayItem {
  id: string;
  mode: "subway" | "bus";
  routeIds: string[];
  destination: string;
  servicePattern?: string;
  stopName?: string;
  walkMinutes?: number;
  distanceMiles?: number;
  arrivalMinutes: number[];
  direction: NearbyTransitDirection;
  predictionType?: "live" | "scheduled";
  predictionFreshness?: "fresh" | "stale" | "scheduled";
  alertSeverity?: "none" | "minor" | "major" | "planned";
}

export interface NearbyGroupedArrival extends NearbyTransitDisplayItem {
  via?: string;
}

export interface NearbyTransitGroup {
  id: string;
  name: string;
  mode: "subway" | "bus" | "mixed";
  routeIds: string[];
  walkMinutes?: number;
  distanceMiles?: number;
  arrivals: NearbyGroupedArrival[];
}

export interface Arrival extends NearbyTransitDisplayItem {
  line: string;
  // Compatibility mirror for older callers. New UI code should read
  // `direction` and the display fields above.
  way: NearbyTransitDirection;
  dest: string;
  label: string; // "Now" | "1 min" | "9 min"
  mins: number;
  status: "On Time" | "Delayed";
  stale: boolean;
  mode: "subway" | "bus";
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
  src: "MTA" | "SYSTEM" | "FEED";
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

export type AlertFeedSeverity =
  | "notice"
  | "planned"
  | "minor"
  | "major"
  | "suspension"
  | "incident";

export type AlertFeedSource =
  | "mta"
  | "grok"
  | "nyc-alert"
  | "social"
  | "internal";

/* Lifecycle derived from the alert text — the only status signal available
   (the backend has no explicit status field). Drives the row's status pill
   and the active-vs-resolved split in the day's timeline. */
export type AlertLifecycle = "active" | "monitoring" | "resolved";

/* One entry in a grouped alert's update thread. `tone` marks whether it is
   the live/current condition or an older/muted step. */
export interface AlertUpdateEntry {
  time: string;
  title: string;
  summary?: string;
  tone: "active" | "muted" | "resolved";
}

export interface AlertFeedDetail {
  currentStatus?: string;
  impact?: string;
  whatHappened?: string;
  alternatives?: string;
  /** Direction string straight from the alert payload ("Southbound"). */
  direction?: string;
  affectedStops?: string[];
  source?: string;
  updatedAt?: string;
  /** Earlier updates, newest first — real thread entries only, never invented. */
  updates?: AlertUpdateEntry[];
}

export interface AlertFeedItem {
  id: string;
  routeIds: string[];
  /** Short service/line identity next to the bullets ("7 Avenue Express"). */
  serviceName: string;
  title: string;
  summary?: string;
  context?: string;
  timestampLabel: string;
  severity: AlertFeedSeverity;
  lifecycle: AlertLifecycle;
  statusLabel: string;
  source?: AlertFeedSource;
  sourceLabel?: string;
  affectedStops?: string[];
  isLive?: boolean;
  expandable?: boolean;
  details?: AlertFeedDetail;
}

export interface RouteStep {
  type: "walk" | "board" | "ride" | "exit" | "destination" | "arrive";
  action: string;
  line?: string;
  title: string;
  detail: string;
  duration: string;
  // Live boarding note ("Departs in 6 min") rendered with the signal icon.
  note?: string;
  live?: boolean;
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
  // Apple Maps-style option card fields, derived from the candidate's own
  // precomputed steps — no replanning needed to render or select them.
  lines?: string[];
  totalMinutes?: number;
  departsInMinutes?: number;
  leavesLabel?: string;
  arriveLabel?: string;
  fromStop?: string;
  toStop?: string;
  strip?: RouteStripSegment[];
}

/* One segment of the compact visual route strip:
   [walk 2 min] › [Q][train] › [5][train] › [walk 5 min] */
export type RouteStripSegment =
  | { kind: "walk"; minutes?: number }
  | { kind: "ride"; routeId: string; mode: "subway" | "bus" };

/* One row of the full Apple Maps-style details chain. Walking/boarding/
   riding steps come from the candidate's precomputed steps; the UI adds
   the Start and Arrive endpoints. */
export interface RouteDetailStep {
  kind: "walk" | "board" | "ride";
  title: string;
  subtitle?: string;
  routeId?: string;
  mode?: "subway" | "bus";
  // Live departure line on board steps ("Departs in 7 min").
  note?: string;
  live?: boolean;
  // Ride-segment fields: boarding stop → alighting stop with a
  // route-colored connector.
  fromStop?: string;
  toStop?: string;
  rideMeta?: string;
  transferTo?: string;
  transferMode?: "subway" | "bus";
}

/* One public route-evaluation line shown in the planning Reasoning block.
   Always derived from a real fact (nearby access, live arrivals, alerts,
   reported incidents) — never invented, never raw model output. */
export interface RouteReasoningInsight {
  id: string;
  text: string;
  source:
    | "nearby-access"
    | "live-arrival"
    | "service-alert"
    | "incident"
    | "comparison"
    | "recommendation"
    | "fallback";
  priority: number;
}

export interface RouteNote {
  tone: "cyan" | "amber" | "coral" | "sage";
  t: string;
  v: string;
}

export interface RoutePlan {
  headline: string;
  // Short, derived, passenger-facing reason line — never raw model output.
  rationale: string;
  // Passenger-facing headsign for the recommended route's main leg
  // ("Coney Island-Stillwell Av"); the card title.
  headsign?: string;
  // True when the shown route is a user-selected alternative, not the
  // top-ranked pick — the card badge must not say "Recommended" then.
  isAlternativeRoute?: boolean;
  eta: string;
  totalTime: string;
  // "Leave by 4:37 PM" — transit departure minus the approach walk.
  leaveByLabel?: string;
  // Transit-vehicle boardings minus one; walking never counts.
  transferCount?: number;
  strip?: RouteStripSegment[];
  detailSteps?: RouteDetailStep[];
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

/* Mapping used by route/alert bullet grids; preserves official MTA grouping
   order: IRT -> IND -> BMT -> shuttles. */
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
