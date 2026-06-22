import type { ServiceAlertDetail } from "@/types";
import {
  classifyServiceAlerts,
  relativeTimeSince,
  type ClassifiedServiceAlert,
  type ServiceAlertBucketId,
  type ServiceAlertTone,
} from "@/components/smart-route/service-alert-classification";

export type AlertFilterMode = "all" | "train" | "bus" | "planned" | "active";
export type AlertSortMode = "severity" | "newest" | "line";
export type LineAlertState = "none" | "some" | "major";

export interface DashboardAlertRow extends ClassifiedServiceAlert {
  primaryRoute: string | null;
  displayTitle: string;
  displaySubtitle: string;
  lastUpdatedLabel: string;
  estimatedClearLabel: string;
  affectedStops: string[];
  activityItems: AlertActivityItem[];
  searchText: string;
}

export interface DashboardAlertGroup {
  id: ServiceAlertBucketId;
  label: string;
  tone: ServiceAlertTone;
  items: DashboardAlertRow[];
}

export interface AlertActivityItem {
  id: string;
  label: string;
  time: string;
  tone: "live" | "started" | "window";
}

export interface AlertDashboardMetrics {
  activeCount: number;
  affectedRouteCount: number;
  majorCount: number;
  plannedCount: number;
  minorCount: number;
  networkState: LineAlertState;
}

export interface LineAlertStatus {
  routeId: string;
  state: LineAlertState;
  count: number;
}

const MTA_LINE_ORDER = [
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "7",
  "A",
  "C",
  "E",
  "B",
  "D",
  "F",
  "M",
  "G",
  "J",
  "Z",
  "L",
  "N",
  "Q",
  "R",
  "W",
  "S",
  "SI",
];

const ROUTE_TERMINALS: Record<string, string> = {
  "1": "Van Cortlandt Park - South Ferry",
  "2": "Flatbush Av - Wakefield/241 St",
  "3": "Harlem - New Lots Av",
  "4": "Woodlawn - New Lots Av",
  "5": "Eastchester-Dyre Av - Flatbush Av",
  "6": "Pelham Bay Park - Brooklyn Bridge",
  "6X": "Pelham Bay Park - Brooklyn Bridge",
  "7": "Flushing-Main St - Hudson Yards",
  "7X": "Flushing-Main St - Hudson Yards",
  A: "Inwood - Ozone Park",
  B: "Brighton Beach",
  C: "168 St - Euclid Av",
  D: "Norwood-205 St - Coney Island",
  E: "Jamaica Center - World Trade Center",
  F: "Jamaica-179 St - Coney Island",
  FX: "Jamaica-179 St - Coney Island",
  G: "Court Sq - Church Av",
  J: "Broad St - Jamaica Center",
  L: "8 Av - Canarsie-Rockaway Pkwy",
  M: "Forest Hills-71 Av - Middle Village",
  N: "Astoria-Ditmars Blvd - Coney Island",
  Q: "All Weekday",
  R: "Forest Hills-71 Av - Bay Ridge/95 St",
  W: "Astoria-Ditmars Blvd - Whitehall St",
  Z: "Jamaica Center - Broad St",
  S: "Shuttle service",
  SI: "St George - Tottenville",
};

const ROUTE_FAMILY: Record<string, string> = {
  "6X": "6",
  "7X": "7",
  FX: "F",
  FS: "S",
  GS: "S",
  H: "S",
  SIR: "SI",
};

const GROUP_META: Record<
  ServiceAlertBucketId,
  { label: string; tone: ServiceAlertTone }
> = {
  ruiners: { label: "Major Disruptions", tone: "major" },
  detours: { label: "Service Changes / Planned", tone: "detour" },
  pain: { label: "Minor Delays / Advisories", tone: "watch" },
};

const ROUTE_TOKEN_PATTERN = /\[(?:[A-Z]{1,3}|[0-9][0-9A-Z]?|SIR|SI)\]/gi;
const ACCESSIBILITY_ARTIFACT_PATTERN = /\[\s*accessibility\s+icon\s*\]/gi;

export function cleanAlertText(value: string) {
  return value
    .replace(ACCESSIBILITY_ARTIFACT_PATTERN, " ")
    .replace(ROUTE_TOKEN_PATTERN, " ")
    .replace(/\[\s*\]/g, " ")
    .replace(/\s*\|\s*/g, ". ")
    .replace(/\s+/g, " ")
    .replace(/\s+([.,;:!?])/g, "$1")
    .replace(/([.!?])(?=\S)/g, "$1 ")
    .trim();
}

function routeFamily(routeId: string) {
  const normalized = routeId.trim().toUpperCase();
  return ROUTE_FAMILY[normalized] ?? normalized;
}

function formatClock(unix?: number | null) {
  if (!unix) return "Not published";
  return new Date(unix * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function normalizeDirectionLabel(value: string) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  return normalized[0].toUpperCase() + normalized.slice(1);
}

function extractDirection(header: string) {
  const text = header.replace(/\[[^\]]+\]/g, " ").replace(/\s+/g, " ");
  const plainDirection = text.match(/\b(Uptown|Downtown)\b/i)?.[1];
  if (plainDirection) return normalizeDirectionLabel(plainDirection);
  const boundDirection = text.match(/\b([A-Z][A-Za-z0-9 ./&'-]+-bound)\b/)?.[1];
  return boundDirection ? normalizeDirectionLabel(boundDirection) : "";
}

function stripMtaRouteTokens(value: string) {
  return value
    .replace(/\[[A-Z0-9]+\]/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function sentenceCase(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed[0].toUpperCase() + trimmed.slice(1);
}

function trimSummary(value: string, maxLength = 92) {
  const compact = value
    .replace(/\s+/g, " ")
    .replace(/\s+([.,;:])/g, "$1")
    .trim();
  if (compact.length <= maxLength) return compact;
  return `${compact.slice(0, maxLength - 1).trimEnd()}...`;
}

function trainLabel(routes: string[]) {
  if (routes.length === 0) return "MTA service";
  const visibleRoutes = routes.slice(0, 4).join(" / ");
  return `${visibleRoutes} ${routes.length === 1 ? "train" : "trains"}`;
}

function routeLabel(alert: ClassifiedServiceAlert) {
  if (alert.routes.length === 0) return "System advisory";
  if (alert.routes.length === 1) return `${alert.routes[0]} train`;
  return `${alert.routes.slice(0, 4).join(", ")} trains`;
}

function conciseIssueSummary(item: ClassifiedServiceAlert) {
  const source = cleanAlertText(`${item.title}. ${item.detail}`);
  const issuePatterns: Array<[RegExp, (match: RegExpMatchArray) => string]> = [
    [/signal problems?\s+(?:at|near|after)\s+([^.;]+)/i, (match) => `Signal problems at ${match[1]}`],
    [/track condition(?: issues?)?\s+(?:at|near)\s+([^.;]+)/i, (match) => `Track condition near ${match[1]}`],
    [/skips?\s+([^.;]+)/i, (match) => `Skips ${match[1]}`],
    [/ends early/i, () => "Ends early"],
    [/planned track maintenance/i, () => "Planned track maintenance"],
    [/modified schedule/i, () => "Operating on a modified schedule"],
    [/running with delays/i, () => "Running with delays"],
    [/rerout(?:ed|e|ing)?/i, () => "Rerouted service"],
  ];

  for (const [pattern, format] of issuePatterns) {
    const match = source.match(pattern);
    if (match) return trimSummary(format(match));
  }

  const withoutContext = cleanAlertText(stripMtaRouteTokens(item.title))
    .replace(/^In [^,]+,\s*/i, "")
    .replace(/^(?:Uptown|Downtown|[A-Za-z0-9 ./&'-]+-bound)\s+/i, "")
    .replace(/^[A-Z0-9 /,]+ trains?\s+(?:are|is|will be|were|was|runs?|run)\s+/i, "")
    .replace(/^trains?\s+(?:are|is|will be|were|was)\s+/i, "");

  if (withoutContext && withoutContext !== item.title) {
    return trimSummary(sentenceCase(withoutContext));
  }

  return trimSummary(item.detail || "Active MTA service alert");
}

function alertDisplayCopy(item: ClassifiedServiceAlert) {
  const direction = extractDirection(item.title);
  const primaryRoute = item.routes[0];
  const title = direction
    ? `${direction} ${trainLabel(item.routes)}`
    : primaryRoute && ROUTE_TERMINALS[primaryRoute]
      ? ROUTE_TERMINALS[primaryRoute]
      : routeLabel(item);

  return {
    title,
    subtitle: conciseIssueSummary(item),
  };
}

function activityItemsForAlert(
  alert: ServiceAlertDetail,
  updatedAt: number | null,
): AlertActivityItem[] {
  const items: AlertActivityItem[] = [];
  if (updatedAt) {
    items.push({
      id: "feed-updated",
      label: "Feed updated",
      time: relativeTimeSince(updatedAt),
      tone: "live",
    });
  }
  if (alert.start) {
    items.push({
      id: "alert-started",
      label: "Alert started",
      time: relativeTimeSince(alert.start),
      tone: "started",
    });
  }
  if (alert.end) {
    items.push({
      id: "window-ends",
      label: "Published window ends",
      time: formatClock(alert.end),
      tone: "window",
    });
  }
  return items;
}

export function buildDashboardRows(
  alerts: ServiceAlertDetail[],
  updatedAt: number | null,
): DashboardAlertRow[] {
  return classifyServiceAlerts(alerts).map((item) => {
    const display = alertDisplayCopy(item);
    const primaryRoute = item.routes[0] ?? null;
    const affectedStops = item.alert.stop_names?.filter(Boolean) ?? [];
    const searchText = [
      display.title,
      display.subtitle,
      cleanAlertText(item.title),
      cleanAlertText(item.detail),
      item.statusLabel,
      item.affecting,
      ...item.routes,
      ...affectedStops,
    ]
      .join(" ")
      .toLowerCase();

    return {
      ...item,
      primaryRoute,
      displayTitle: display.title,
      displaySubtitle: display.subtitle,
      lastUpdatedLabel: relativeTimeSince(updatedAt),
      estimatedClearLabel: item.alert.end ? formatClock(item.alert.end) : "Not published",
      affectedStops,
      activityItems: activityItemsForAlert(item.alert, updatedAt),
      searchText,
    };
  });
}

export function buildDashboardMetrics(
  rows: DashboardAlertRow[],
  fallbackActiveCount: number,
  fallbackAffectedRouteCount: number,
): AlertDashboardMetrics {
  const affected = new Set<string>();
  for (const row of rows) {
    row.routes.forEach((route) => affected.add(routeFamily(route)));
  }
  const majorCount = rows.filter((row) => row.tone === "major").length;
  const plannedCount = rows.filter((row) => row.tone === "detour").length;
  const minorCount = rows.filter((row) => row.tone === "watch").length;
  return {
    activeCount: fallbackActiveCount || rows.length,
    affectedRouteCount: fallbackAffectedRouteCount || affected.size,
    majorCount,
    plannedCount,
    minorCount,
    networkState: majorCount > 0 ? "major" : rows.length > 0 ? "some" : "none",
  };
}

export function buildLineStates(rows: DashboardAlertRow[]): LineAlertStatus[] {
  const counts = new Map<string, { count: number; state: LineAlertState }>();
  for (const row of rows) {
    const rowState: LineAlertState = row.tone === "major" ? "major" : "some";
    for (const route of row.routes) {
      const family = routeFamily(route);
      const current = counts.get(family) ?? { count: 0, state: "none" as LineAlertState };
      counts.set(family, {
        count: current.count + 1,
        state: current.state === "major" || rowState === "major" ? "major" : "some",
      });
    }
  }

  return MTA_LINE_ORDER.map((routeId) => ({
    routeId,
    state: counts.get(routeId)?.state ?? "none",
    count: counts.get(routeId)?.count ?? 0,
  }));
}

export function filterDashboardRows(
  rows: DashboardAlertRow[],
  filterMode: AlertFilterMode,
  query: string,
  selectedLine: string | null,
) {
  const normalizedQuery = query.trim().toLowerCase();
  return rows.filter((row) => {
    if (filterMode === "bus") return false;
    if (filterMode === "planned" && row.tone !== "detour") return false;
    if (filterMode === "active" && row.tone === "detour") return false;
    if (selectedLine && !row.routes.some((route) => routeFamily(route) === selectedLine)) {
      return false;
    }
    if (normalizedQuery && !row.searchText.includes(normalizedQuery)) return false;
    return true;
  });
}

export function sortDashboardRows(
  rows: DashboardAlertRow[],
  sortMode: AlertSortMode,
) {
  return [...rows].sort((a, b) => {
    if (sortMode === "newest") {
      return (b.alert.start ?? 0) - (a.alert.start ?? 0);
    }
    if (sortMode === "line") {
      return (a.primaryRoute ?? "ZZZ").localeCompare(b.primaryRoute ?? "ZZZ");
    }
    const severity = a.severityRank - b.severityRank;
    if (severity !== 0) return severity;
    return (a.primaryRoute ?? "ZZZ").localeCompare(b.primaryRoute ?? "ZZZ");
  });
}

export function groupDashboardRows(rows: DashboardAlertRow[]): DashboardAlertGroup[] {
  return (["ruiners", "detours", "pain"] as ServiceAlertBucketId[])
    .map((id) => {
      const items = rows.filter((row) => row.bucket === id);
      if (items.length === 0) return null;
      return {
        id,
        label: GROUP_META[id].label,
        tone: GROUP_META[id].tone,
        items,
      };
    })
    .filter((group): group is DashboardAlertGroup => Boolean(group));
}

export function feedFreshness(updatedAt: number | null) {
  if (!updatedAt) {
    return {
      label: "Awaiting feed",
      stale: true,
    };
  }
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - updatedAt);
  return {
    label: relativeTimeSince(updatedAt),
    stale: delta > 180,
  };
}
