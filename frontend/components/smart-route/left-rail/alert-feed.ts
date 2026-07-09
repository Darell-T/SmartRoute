import type {
  AlertFeedItem,
  AlertFeedSeverity,
  AlertFeedSource,
  AlertLifecycle,
  AlertUpdateEntry,
  FeedEvent,
  ServiceAlert,
  Severity,
} from "./types";
import { ALL_LINES } from "./types";

const SUBWAY_ORDER = new Map(ALL_LINES.map((line, index) => [line, index]));
const BUS_ROUTE_PATTERN = /^(?:BXM|BM|BX|B|QM|Q|SIM|S|M|X)\d{1,3}[A-Z]?(?:-?SBS)?$/;
const ROUTE_TOKEN_PATTERN = /\[([A-Za-z0-9+-]{1,8})\]/g;
const INLINE_ICON_PLACEHOLDER_PATTERN =
  /\[(?:free\s+)?(?:shuttle\s+bus|bus|subway|train|shuttle)\s+icon\]|\[(?:shuttle\s+bus|bus|subway|train|shuttle)\]/gi;

/* Static NYCTA line-family and trunk/service names. These are display
   identities, not per-alert inference, so alerts and the UI share one table
   instead of separately parsing messy MTA copy. */
type AlertLineRouteDefinition = {
  routeId: string;
  serviceName: string;
  aliases?: string[];
};

export type AlertLineFamily = {
  id: string;
  name: string;
  routeIds: string[];
  rank: number;
  routes: AlertLineRouteDefinition[];
};

export const ALERT_LINE_FAMILIES: AlertLineFamily[] = [
  {
    id: "7-avenue",
    name: "7 Avenue",
    routeIds: ["1", "2", "3"],
    rank: 10,
    routes: [
      { routeId: "1", serviceName: "7 Avenue Local" },
      { routeId: "2", serviceName: "7 Avenue Express" },
      { routeId: "3", serviceName: "7 Avenue Express" },
    ],
  },
  {
    id: "lexington-avenue",
    name: "Lexington Avenue",
    routeIds: ["4", "5", "6"],
    rank: 20,
    routes: [
      { routeId: "4", serviceName: "Lexington Av Express" },
      { routeId: "5", serviceName: "Lexington Av Express" },
      { routeId: "6", serviceName: "Lexington Av Local" },
      { routeId: "6X", serviceName: "Lexington Av Express" },
    ],
  },
  {
    id: "flushing",
    name: "Flushing",
    routeIds: ["7"],
    rank: 30,
    routes: [{ routeId: "7", serviceName: "Flushing Line", aliases: ["7X"] }],
  },
  {
    id: "8-avenue",
    name: "8 Avenue",
    routeIds: ["A", "C", "E"],
    rank: 40,
    routes: [
      { routeId: "A", serviceName: "8 Avenue Express" },
      { routeId: "C", serviceName: "8 Avenue Local" },
      { routeId: "E", serviceName: "8 Avenue Local" },
    ],
  },
  {
    id: "6-avenue",
    name: "6 Avenue",
    routeIds: ["B", "D", "F", "M"],
    rank: 50,
    routes: [
      { routeId: "B", serviceName: "6 Avenue Express" },
      { routeId: "D", serviceName: "6 Avenue Express" },
      { routeId: "F", serviceName: "6 Avenue Local", aliases: ["FX"] },
      { routeId: "M", serviceName: "6 Avenue Local" },
    ],
  },
  {
    id: "crosstown",
    name: "Crosstown",
    routeIds: ["G"],
    rank: 60,
    routes: [{ routeId: "G", serviceName: "Crosstown Line" }],
  },
  {
    id: "nassau-st",
    name: "Nassau St",
    routeIds: ["J", "Z"],
    rank: 70,
    routes: [
      { routeId: "J", serviceName: "Nassau St Line" },
      { routeId: "Z", serviceName: "Nassau St Line" },
    ],
  },
  {
    id: "canarsie",
    name: "Canarsie",
    routeIds: ["L"],
    rank: 80,
    routes: [{ routeId: "L", serviceName: "14 St-Canarsie" }],
  },
  {
    id: "broadway",
    name: "Broadway",
    routeIds: ["N", "Q", "R", "W"],
    rank: 90,
    routes: [
      { routeId: "N", serviceName: "Broadway Express" },
      { routeId: "Q", serviceName: "Broadway Express" },
      { routeId: "R", serviceName: "Broadway Local" },
      { routeId: "W", serviceName: "Broadway Local" },
    ],
  },
  {
    id: "shuttles",
    name: "Shuttles",
    routeIds: ["S"],
    rank: 100,
    routes: [
      {
        routeId: "S",
        serviceName: "42 St Shuttle",
        aliases: ["FS", "GS", "H"],
      },
    ],
  },
  {
    id: "staten-island-railway",
    name: "Staten Island Railway",
    routeIds: ["SIR"],
    rank: 110,
    routes: [
      {
        routeId: "SIR",
        serviceName: "Staten Island Railway",
        aliases: ["SI"],
      },
    ],
  },
];

export const ALERT_ROUTE_TO_FAMILY = new Map<string, AlertLineFamily>(
  ALERT_LINE_FAMILIES.flatMap((family) =>
    family.routes.flatMap((route) =>
      [route.routeId, ...(route.aliases ?? [])].map(
        (routeId) => [routeId.toUpperCase(), family] as const,
      ),
    ),
  ),
);

const TRUNK_NAMES: Record<string, string> = Object.fromEntries(
  ALERT_LINE_FAMILIES.flatMap((family) =>
    family.routes.flatMap((route) =>
      [route.routeId, ...(route.aliases ?? [])].map(
        (routeId) => [routeId.toUpperCase(), route.serviceName] as const,
      ),
    ),
  ),
);

export function normalizeAlertFeedItems(
  alerts: ServiceAlert[],
  recentUpdates: FeedEvent[],
): AlertFeedItem[] {
  const items = [
    ...alerts.map(normalizeServiceAlert),
    ...normalizeRecentUpdates(recentUpdates),
  ];
  const seen = new Set<string>();
  const deduped = items.filter((item) => {
    const key = [
      item.routeIds.join(","),
      item.title.toLowerCase(),
      item.summary?.toLowerCase() ?? "",
    ].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // One incident, many tagged lines: the MTA copies the same alert text onto
  // every connected route, so a ferry suspension arrives as separate SIR/R/1/
  // 4/5 items. Merge identical-text items into a single row and union their
  // route badges. Short generic titles with no summary stay separate — two
  // unrelated "Delays" on different lines are not one issue.
  const merged: AlertFeedItem[] = [];
  const byText = new Map<string, AlertFeedItem>();
  for (const item of deduped) {
    const textKey = [
      normalizeIssueText(item.title),
      normalizeIssueText(item.summary ?? ""),
    ].join("|");
    const isSpecific =
      Boolean(item.summary) || normalizeIssueText(item.title).length >= 24;
    const existing = isSpecific ? byText.get(textKey) : undefined;
    if (existing) {
      existing.routeIds = normalizeRoutes([
        ...existing.routeIds,
        ...item.routeIds,
      ]);
      existing.serviceName =
        serviceNameForRoutes(existing.routeIds) ?? existing.serviceName;
      continue;
    }
    if (isSpecific) byText.set(textKey, item);
    merged.push(item);
  }

  // Group same-issue items into one thread, then order the day: active/
  // monitoring first (severity, then recency), resolved muted at the bottom.
  return sortAlertFeedItems(groupAlertThreads(merged));
}

/* Group only when items are the same underlying issue: overlapping routes AND
   the same place/corridor. Same route family alone never merges (a Q delay at
   Prospect Park stays separate from an R planned closure at 59 St). The
   freshest item becomes the visible head; the rest become earlier updates. */
export function groupAlertThreads(items: AlertFeedItem[]): AlertFeedItem[] {
  const groups = new Map<string, AlertFeedItem[]>();
  const order: string[] = [];
  for (const item of items) {
    const key = issueSignature(item);
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)!.push(item);
  }

  return order.map((key) => {
    const members = groups.get(key)!;
    if (members.length === 1) return members[0];
    const sorted = [...members].sort(
      (left, right) => timeRank(left.timestampLabel) - timeRank(right.timestampLabel),
    );
    const [head, ...rest] = sorted;
    const mergedRoutes = normalizeRoutes(members.flatMap((item) => item.routeIds));
    const threadUpdates: AlertUpdateEntry[] = [
      ...(head.details?.updates ?? []),
      ...rest.map((item) => ({
        time: item.timestampLabel,
        title: item.title,
        summary: item.summary,
        tone: "muted" as const,
      })),
    ];

    return {
      ...head,
      routeIds: mergedRoutes,
      serviceName: serviceNameForRoutes(mergedRoutes) ?? head.serviceName,
      expandable: true,
      details: {
        ...head.details,
        currentStatus: head.details?.currentStatus ?? head.summary,
        updates: dedupeUpdates(threadUpdates),
      },
    };
  });
}

/* Same-issue key: sorted routes + the corridor/place the alert is about.
   Falls back to a normalized title so placeless items don't over-merge. */
function issueSignature(item: AlertFeedItem): string {
  const place = corridorKey(item);
  const routes = item.routeIds.join(",");
  if (place) return `${routes}|${place}`;
  return `${routes}|${normalizeIssueText(item.title)}`;
}

function corridorKey(item: AlertFeedItem): string {
  const source = `${item.summary ?? ""} ${item.title} ${item.context ?? ""}`;
  const near = source.match(/\b(?:near|at|between)\s+([A-Za-z0-9][A-Za-z0-9 .'\-\/]{3,40})/i);
  if (near) return normalizeIssueText(near[1]);
  const stop = item.affectedStops?.[0];
  return stop ? normalizeIssueText(stop) : "";
}

function normalizeIssueText(value: string): string {
  return cleanPassengerText(value)
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, "")
    .replace(/\b(?:college|station|av|avenue|st|street)\b/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 40);
}

function dedupeUpdates(updates: AlertUpdateEntry[]): AlertUpdateEntry[] {
  const seen = new Set<string>();
  return updates.filter((entry) => {
    const key = `${entry.time}|${entry.title.toLowerCase()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function normalizeRecentUpdates(updates: FeedEvent[]): AlertFeedItem[] {
  return updates.map((event, index) => {
    const routeIds = normalizeRoutes(event.line ? [event.line] : []);
    const sourceInfo = sourceFromFeedEvent(event);
    const summary = leadSentences(
      compactAlertSummary(event.detail, event.title),
      2,
      200,
    );
    const sourceContext = sourceInfo.sourceLabel
      ? `Source: ${sourceInfo.sourceLabel}`
      : event.src === "FEED"
        ? "Reported nearby"
        : undefined;

    const severity = feedSeverity(event.sev, event.src);
    // MTA service updates get the full title sanitizer ("Downtown 4 trains
    // are running express…" → "Running express"); FEED incidents keep their
    // place-based compaction ("Fire response at Canal St").
    const title =
      event.src === "FEED"
        ? compactFeedTitle(event.title, routeIds)
        : compactAlertTitle(event.title, routeIds);
    const lifecycle = deriveLifecycle(`${event.title} ${event.detail}`);
    // Current status is the fuller (bracket-free) condition sentence; the
    // collapsed summary stays short.
    const currentStatus =
      leadSentences(compactAlertSummary(event.title, ""), 2, 200) ??
      summary ??
      severityStatusPhrase(severity, lifecycle);
    const started = compactTimestamp(event.time);
    const updates: AlertUpdateEntry[] =
      started && started !== "now" && started !== "live"
        ? [
            {
              time: started,
              title: lifecycle === "resolved" ? "Service resolved" : "First reported",
              tone: lifecycle === "resolved" ? "resolved" : "muted",
            },
          ]
        : [];

    return {
      id: `update-${index}-${routeIds.join("-") || "system"}-${slug(event.title)}`,
      routeIds,
      serviceName: serviceNameForRoutes(routeIds) ?? sourceServiceName(event.src),
      title,
      summary,
      context: sourceContext,
      timestampLabel: compactTimestamp(event.time),
      severity,
      lifecycle,
      statusLabel: statusLabelFor(lifecycle, severity),
      source: sourceInfo.source,
      sourceLabel: sourceInfo.sourceLabel,
      isLive: event.time.toLowerCase() === "live" || event.time.toLowerCase() === "now",
      expandable: Boolean(currentStatus || summary || sourceContext),
      details: {
        currentStatus,
        impact: summary,
        source: sourceContext,
        updatedAt: compactTimestamp(event.time),
        updates,
      },
    };
  });
}

/* Lifecycle from the alert text — the only status signal in the payload.
   Conservative: only clear "resolved" / "monitoring" phrasing flips it, so a
   plain active alert never mislabels itself. */
export function deriveLifecycle(text: string): AlertLifecycle {
  const value = cleanPassengerText(text).toLowerCase();
  if (/resolved|resumed|returned to normal|back to normal|restored|cleared|no longer|has ended|good service/.test(value)) {
    return "resolved";
  }
  if (/investigat|monitoring|being addressed|we are addressing|crews are|response en route|on scene|awaiting/.test(value)) {
    return "monitoring";
  }
  return "active";
}

/* Status renders as quiet dot + word metadata ("Major", "Delay", "Planned",
   "Resolved") — direct classifications of real fields. A plain notice gets no
   status word at all; the view omits the metadata entirely. */
function statusLabelFor(
  lifecycle: AlertLifecycle,
  severity: AlertFeedSeverity,
): string {
  if (lifecycle === "resolved") return "Resolved";
  if (severity === "planned") return "Planned";
  if (severity === "suspension" || severity === "major") return "Major";
  if (severity === "minor") return "Delay";
  if (severity === "incident") return "Incident";
  return "";
}

function severityStatusPhrase(
  severity: AlertFeedSeverity,
  lifecycle: AlertLifecycle,
): string {
  if (lifecycle === "resolved") return "Service has returned to normal.";
  if (severity === "suspension") return "Service is suspended on the affected segment.";
  if (severity === "major") return "Major service disruption in effect.";
  if (severity === "planned") return "Planned service change in effect.";
  if (severity === "minor") return "Trains are running with delays.";
  return "Service change in effect.";
}

/* Sentence-safe splitting for raw MTA paragraphs. Periods after street and
   station abbreviations ("St. George", "Av.") and single initials do not end
   a sentence. */
const NON_TERMINAL_TAIL =
  /(?:\b(?:St|Av|Ave|Avs|Rd|Blvd|Sq|Pkwy|Hwy|Ct|Ft|Mt|Jct|Dr|Ln|Pl|Terr|No|approx|vs)|\b[A-Z])\.$/;

export function splitSentences(value: string | undefined): string[] {
  const text = cleanPassengerText(value);
  if (!text) return [];
  const sentences: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch !== "." && ch !== "!" && ch !== "?") continue;
    const next = text[i + 1];
    if (next !== undefined && next !== " ") continue;
    const chunk = text.slice(start, i + 1).trim();
    if (ch === "." && NON_TERMINAL_TAIL.test(chunk)) continue;
    if (chunk) sentences.push(chunk);
    start = i + 1;
  }
  const tail = text.slice(start).trim();
  if (tail) sentences.push(tail);
  return sentences;
}

/* Truncate official alert paragraphs to their lead sentences. The rail never
   renders a raw MTA text wall — the fuller body stays honest but bounded. */
export function leadSentences(
  value: string | undefined,
  count = 2,
  maxChars = 240,
): string | undefined {
  if (!value) return undefined;
  const sentences = splitSentences(value);
  if (sentences.length === 0) return undefined;
  let out = "";
  for (const sentence of sentences.slice(0, count)) {
    const candidate = out ? `${out} ${sentence}` : sentence;
    if (out && candidate.length > maxChars) break;
    out = candidate;
  }
  if (out.length > maxChars) {
    out = `${out.slice(0, maxChars).replace(/\s+\S*$/, "").trim()}…`;
  }
  return out || undefined;
}

/* Freshest update across the feed, for the header status row. Renders only
   what the timestamps actually say — unparseable labels yield nothing. */
export function latestAlertUpdateLabel(
  items: AlertFeedItem[],
): string | undefined {
  let best = Infinity;
  for (const item of items) {
    best = Math.min(best, timeRank(item.timestampLabel));
  }
  if (!Number.isFinite(best) || best >= 999) return undefined;
  if (best === 0) return "Updated just now";
  if (best < 60) return `Updated ${best} min ago`;
  return `Updated ${Math.round(best / 60)} hr ago`;
}

export function serviceNameForRoutes(routeIds: string[]): string | undefined {
  const primary = routeIds[0];
  if (!primary) return undefined;
  const trunks = routeIds.map((route) => TRUNK_NAMES[route]);
  if (trunks.length > 0 && trunks.every((name) => name && name === trunks[0])) {
    return trunks[0];
  }
  // A merged multi-trunk alert (one incident tagged to SIR/R/1/4/5) has no
  // single honest service name — the bullets carry the specifics.
  if (routeIds.length > 1) return "Multiple lines";
  if (BUS_ROUTE_PATTERN.test(primary)) return `${primary} bus`;
  return `${primary} service`;
}

function sourceServiceName(src: FeedEvent["src"]): string {
  if (src === "FEED") return "Nearby incident";
  return "Service update";
}

/* Day timeline order: everything still active/monitoring first (severity,
   then most recent), then resolved items muted at the bottom. */
export function sortAlertFeedItems(items: AlertFeedItem[]): AlertFeedItem[] {
  return [...items].sort((left, right) => {
    const lifecycleDelta = lifecycleRank(left.lifecycle) - lifecycleRank(right.lifecycle);
    if (lifecycleDelta !== 0) return lifecycleDelta;
    const severityDelta = severityRank(right.severity) - severityRank(left.severity);
    if (severityDelta !== 0) return severityDelta;
    return timeRank(left.timestampLabel) - timeRank(right.timestampLabel);
  });
}

function lifecycleRank(lifecycle: AlertLifecycle): number {
  return lifecycle === "resolved" ? 1 : 0;
}

export function compactAlertTitle(
  title: string,
  routeIds: string[] = [],
  fallback = "Service alert",
): string {
  const cleaned = cleanPassengerText(title);
  if (!cleaned) return fallback;

  const withoutSource = cleaned.replace(/^MTA\s+/i, "").trim();
  const withoutTokens = withoutSource.replace(ROUTE_TOKEN_PATTERN, "$1").trim();
  const routePattern = routeIds.length
    ? new RegExp(`\\b(?:${routeIds.map(escapeRegExp).join("|")})\\b\\s*(?:\\/\\s*\\b(?:${routeIds.map(escapeRegExp).join("|")})\\b\\s*)?trains?$`, "i")
    : null;

  if (/person needed medical attention|medical assistance/i.test(withoutTokens)) {
    return titleWithAt(withoutTokens.replace(/person needed medical attention/i, "Medical assistance"));
  }
  if (/partial suspension/i.test(withoutTokens)) {
    return withoutTokens.replace(/\s*-\s*/g, " between ").replace(/\s+and\s+and\s+/i, " and ");
  }
  // Drop the leading "2 / 3 trains are running with delays…" route-name echo,
  // keeping the passenger-relevant tail ("Delays in both directions").
  const runningDelays = withoutTokens.match(
    /\btrains?\s+(?:are\s+)?running with delays\b(.*)$/i,
  );
  if (runningDelays) return sentenceCase(`Delays${runningDelays[1]}`.trim());
  // "There is no [4] service in either direction between Woodlawn and 149
  // St-Grand Concourse. Take nearby…" → "No service between Woodlawn and
  // 149 St-Grand Concourse" — editorial headline, place preserved.
  const noService = withoutTokens.match(
    /\bthere is no (?:[A-Za-z0-9/ ]{1,12}\s)?service (?:in either direction )?(between [A-Za-z0-9 .'\-\/]+?)(?:[.,]|$)/i,
  );
  if (noService) return sentenceCase(`No service ${noService[1].trim()}`);
  // "[5] runs every 12 minutes", "[6] runs about every 8 minutes in Manhattan"
  // → "Runs every 12 minutes" (keeps a trailing location clause).
  const everyMinutes = withoutTokens.match(
    /\bruns?\s+(?:about\s+)?every\s+(\d+)\s+minutes?\b([^.,]*)/i,
  );
  if (everyMinutes) {
    const tail = everyMinutes[2].replace(/\s+/g, " ").trimEnd();
    return `Runs every ${everyMinutes[1]} minutes${tail}`;
  }
  // "Downtown 4 trains are running express…" / "[4] runs express" → editorial.
  if (/\b(?:trains?\s+(?:are\s+)?running|runs?)\s+express\b/i.test(withoutTokens)) {
    return "Running express";
  }
  if (/\b(?:trains?\s+(?:are\s+)?running|runs?)\s+local\b/i.test(withoutTokens)) {
    return "Running local";
  }
  if (/\bskip(?:s|ping)?\b/i.test(withoutTokens) && !/suspension/i.test(withoutTokens)) {
    return "Skipping stations";
  }
  // "Additional [C] service operates between…" → "Additional service".
  if (/\badditional\b[^.]*\bservice\b/i.test(withoutTokens)) {
    return "Additional service";
  }
  if (routePattern?.test(withoutTokens)) return "Trains running with delays";
  // Fallthrough keeps bracket route tokens intact — the view renders them as
  // real MTA bullets (TransitText) — EXCEPT a leading route echo ("[5] runs…",
  // "[A][C] trains…"): the row's own bullet + trunk group already name the
  // line, so a duplicate bullet opening the headline is just noise.
  const leadStripped = withoutSource
    .replace(/^(?:\[[A-Za-z0-9+-]{1,4}\]\s*)+/, "")
    .trim();
  if (
    leadStripped !== withoutSource &&
    /^(?:runs?|trains?|service|is|are|will|has|have|no)\b/i.test(leadStripped) &&
    leadStripped.split(/\s+/).length >= 3
  ) {
    return sentenceCase(leadStripped);
  }
  return sentenceCase(withoutSource);
}

export function compactAlertSummary(summary: string | undefined, title = ""): string | undefined {
  const cleaned = cleanPassengerText(summary);
  if (!cleaned || cleaned.toLowerCase() === cleanPassengerText(title).toLowerCase()) {
    return undefined;
  }
  // Bracket route tokens stay in the text — TransitText renders them as real
  // bullets in the view.
  return cleaned
    .replace(/^MTA\s+/i, "")
    .replace(/\bALL trains\b/g, "All trains")
    .replace(/btwn/gi, "between")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeServiceAlert(alert: ServiceAlert, index: number): AlertFeedItem {
  const routeIds = normalizeRoutes(alert.lines);
  const severity = alertSeverity(alert);
  // Lead sentences only — long official paragraphs never render verbatim.
  const summary = leadSentences(compactAlertSummary(alert.sub, alert.title), 2, 200);
  const impact = leadSentences(
    compactAlertSummary(alert.aiContext ?? alert.fullText ?? alert.sub, alert.title),
    3,
    320,
  );
  const affectedStops = alert.affectedStops?.map(cleanPassengerText).filter(Boolean);
  const context = affectedStops?.length
    ? `Affected: ${affectedStops.slice(0, 3).join(", ")}${affectedStops.length > 3 ? ` +${affectedStops.length - 3}` : ""}`
    : alert.direction
      ? alert.direction
      : undefined;
  const updatedAt = compactTimestamp(alert.lastUpdate || alert.startedAgo);
  const lifecycle = deriveLifecycle(
    [
      alert.title,
      alert.sub,
      alert.aiContext ?? "",
      alert.fullText ?? "",
      alert.activity?.map((entry) => entry.e).join(" ") ?? "",
    ].join(" "),
  );
  const currentStatus =
    impact ?? summary ?? severityStatusPhrase(severity, lifecycle);
  const updates = buildUpdateThread(alert, routeIds);
  const alternatives = parseAlternatives(alert);

  return {
    id: `alert-${index}-${routeIds.join("-") || "system"}-${slug(alert.title)}`,
    routeIds,
    serviceName: serviceNameForRoutes(routeIds) ?? "Service alert",
    title: compactAlertTitle(alert.title, routeIds),
    summary,
    context,
    timestampLabel: updatedAt,
    severity,
    lifecycle,
    statusLabel: statusLabelFor(lifecycle, severity),
    source: "mta",
    affectedStops,
    isLive: updatedAt === "live" || updatedAt === "now" || /just now/i.test(alert.lastUpdate),
    expandable: Boolean(
      impact ||
        summary ||
        affectedStops?.length ||
        alert.estClear ||
        alternatives ||
        updates.length,
    ),
    details: {
      currentStatus,
      impact,
      whatHappened: summary,
      alternatives,
      direction: alert.direction ? cleanPassengerText(alert.direction) : undefined,
      affectedStops,
      source: "MTA service alert",
      updatedAt,
      updates,
    },
  };
}

/* Earlier-updates thread — real entries only. Prefer the backend's activity
   log; otherwise anchor a single honest "first reported" entry from the
   alert's own start time. No inferred middle steps. */
function buildUpdateThread(
  alert: ServiceAlert,
  routeIds: string[],
): AlertUpdateEntry[] {
  if (alert.activity?.length) {
    return alert.activity.map((entry) => {
      const title = sentenceCase(cleanPassengerText(entry.e));
      return {
        time: compactTimestamp(entry.t),
        title,
        // A resumed/resolved step gets the green dot; everything else is
        // muted history so only the current status stays strong.
        tone: /resolved|resumed|returned to normal|restored|cleared/i.test(title)
          ? ("resolved" as const)
          : ("muted" as const),
      };
    });
  }
  const started = compactTimestamp(alert.startedAgo);
  if (started && started !== "now" && started !== "live") {
    return [
      {
        time: started,
        title: "First reported",
        summary: compactAlertTitle(alert.title, routeIds),
        tone: "muted" as const,
      },
    ];
  }
  return [];
}

/* Surface alternatives only when the alert text itself states them — the
   MTA's own "take the SIR to Grasmere…" instructions, verbatim, capped at
   two sentences — or a real expected-clearance time. Never invented. */
function parseAlternatives(alert: ServiceAlert): string | undefined {
  const text = cleanPassengerText(alert.aiContext ?? alert.fullText ?? alert.sub);
  const picks = splitSentences(text)
    .filter(
      (sentence) =>
        /^for alternative service/i.test(sentence) ||
        // Route letters must stay uppercase in the match, so the verb is
        // spelled both cases instead of using the /i flag.
        /\b(?:[Uu]se|[Tt]ake)\s+(?:the\s+)?(?:\[?[A-Z0-9]|nearby|free|shuttle)/.test(sentence),
    )
    .slice(0, 2);
  if (picks.length > 0) return picks.join(" ");
  const estClear = cleanPassengerText(alert.estClear).replace(/^~\s*/, "");
  if (estClear && estClear !== "-") {
    return `Expected to clear around ${estClear}.`;
  }
  return undefined;
}

function alertSeverity(alert: ServiceAlert): AlertFeedSeverity {
  const text = `${alert.title} ${alert.sub} ${alert.fullText ?? ""}`.toLowerCase();
  if (/suspend|suspension|no trains|no .* service|bypass/.test(text)) return "suspension";
  if (alert.sev === "major") return "major";
  if (alert.sev === "planned") return "planned";
  if (/delay|delayed|slow|running with delays/.test(text)) return "minor";
  return "notice";
}

function feedSeverity(severity: Severity, source: FeedEvent["src"]): AlertFeedSeverity {
  if (source === "FEED") return "incident";
  if (severity === "major") return "major";
  if (severity === "planned") return "planned";
  if (severity === "minor") return "minor";
  return "notice";
}

function sourceFromFeedEvent(event: FeedEvent): {
  source: AlertFeedSource;
  sourceLabel?: string;
} {
  const handle = event.detail.match(/@[\w_]+/)?.[0];
  if (handle) return { source: "social", sourceLabel: handle };
  if (event.src === "MTA") return { source: "mta" };
  if (event.src === "SYSTEM") return { source: "internal" };
  return { source: "nyc-alert" };
}

function compactFeedTitle(title: string, routeIds: string[]): string {
  const cleaned = cleanPassengerText(title);
  const [kindRaw, placeRaw] = cleaned.split(/\s+-\s+/);
  const kind = sentenceCase(kindRaw || cleaned);
  const place = cleanPassengerText(placeRaw);

  if (/medical/i.test(kind) && place) return `Medical assistance at ${place}`;
  if (/fire response/i.test(kind) && place) return `Fire response at ${place}`;
  if (/police activity/i.test(kind) && place) return `Police activity near ${place}`;
  if (/stalled/i.test(kind)) {
    const route = routeIds[0];
    return route ? `Stalled ${route} train` : "Stalled train";
  }
  if (/partial suspension/i.test(kind) && place) return `Partial suspension between ${place}`;
  return place ? `${kind} at ${place}` : kind;
}

function titleWithAt(text: string): string {
  return cleanPassengerText(text)
    .replace(/\s+at\s+/i, " at ")
    .replace(/\s+near\s+/i, " near ");
}

function cleanPassengerText(value: string | undefined): string {
  return (
    String(value ?? "")
      .replace(INLINE_ICON_PLACEHOLDER_PATTERN, " ")
      .replace(/[·•]/g, " - ")
      .replace(/[→↔]/g, " and ")
      .replace(/–|—/g, "-")
      .replace(/≈/g, "about")
      // MTA copy boilerplate headers never reach the rail.
      .replace(/what'?s happening\??:?/gi, " ")
      .replace(/planned work reminder:?/gi, " ")
      .replace(/what to expect:?/gi, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\s+-\s+$/g, "")
  );
}

function compactTimestamp(value: string | undefined): string {
  const text = cleanPassengerText(value).toLowerCase();
  if (!text || text === "just now") return "now";
  if (text === "live") return "live";
  return text.replace(/\s+ago$/, "");
}

function normalizeRoutes(routes: Array<string | null | undefined>): string[] {
  return Array.from(
    new Set(
      routes
        .map(normalizeRoute)
        .filter((route): route is string => Boolean(route)),
    ),
  ).sort(lineSort);
}

function normalizeRoute(route: string | null | undefined): string | null {
  const normalized = String(route ?? "").trim().toUpperCase();
  return normalized || null;
}

function lineSort(left: string, right: string) {
  const leftIndex = SUBWAY_ORDER.get(left);
  const rightIndex = SUBWAY_ORDER.get(right);
  if (leftIndex !== undefined || rightIndex !== undefined) {
    return (leftIndex ?? 999) - (rightIndex ?? 999);
  }
  const leftIsBus = BUS_ROUTE_PATTERN.test(left);
  const rightIsBus = BUS_ROUTE_PATTERN.test(right);
  if (leftIsBus !== rightIsBus) return leftIsBus ? 1 : -1;
  return left.localeCompare(right);
}

function severityRank(severity: AlertFeedSeverity): number {
  if (severity === "incident") return 6;
  if (severity === "suspension") return 5;
  if (severity === "major") return 4;
  if (severity === "minor") return 3;
  if (severity === "planned") return 2;
  return 1;
}

function timeRank(label: string): number {
  if (label === "live" || label === "now") return 0;
  const minutes = label.match(/^(\d+)m$/);
  if (minutes) return Number(minutes[1]);
  const hours = label.match(/^(\d+)h$/);
  if (hours) return Number(hours[1]) * 60;
  return 999;
}

function sentenceCase(value: string): string {
  const text = value.trim();
  if (!text) return text;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function slug(value: string): string {
  return cleanPassengerText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
