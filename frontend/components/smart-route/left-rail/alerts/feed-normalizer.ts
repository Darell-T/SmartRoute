import {
  alertSlug,
  cleanPassengerAlertText,
  compactAlertSummary,
  compactAlertTimestamp,
  compactAlertTitle,
  compactFeedTitle,
  deriveLifecycle,
  leadSentences,
  parseAlertAlternatives,
  sentenceCase,
} from "./feed-copy";
import {
  normalizeAlertRoutes,
  serviceNameForRoutes,
} from "./line-identities";
import { groupAlertThreads, sortAlertFeedItems, normalizeIssueText } from "./feed-threading";
import type {
  AlertFeedItem,
  AlertFeedSeverity,
  AlertFeedSource,
  AlertLifecycle,
  AlertUpdateEntry,
  FeedEvent,
  ServiceAlert,
  Severity,
} from "../types";

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
    if (seen.has(key)) {
      return false;
    }

    seen.add(key);
    return true;
  });
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
      existing.routeIds = normalizeAlertRoutes([
        ...existing.routeIds,
        ...item.routeIds,
      ]);
      existing.serviceName =
        serviceNameForRoutes(existing.routeIds) ?? existing.serviceName;
      continue;
    }

    if (isSpecific) {
      byText.set(textKey, item);
    }
    merged.push(item);
  }

  return sortAlertFeedItems(groupAlertThreads(merged));
}

function feedSourceContext(
  sourceLabel: string | undefined,
  src: FeedEvent["src"],
): string | undefined {
  if (sourceLabel) return `Source: ${sourceLabel}`;
  if (src === "FEED") return "Reported nearby";
  return undefined;
}

function feedItemTitle(event: FeedEvent, routeIds: string[]): string {
  if (event.src === "FEED") return compactFeedTitle(event.title, routeIds);
  return compactAlertTitle(event.title, routeIds);
}

function feedServiceName(routeIds: string[], src: FeedEvent["src"]): string {
  return serviceNameForRoutes(routeIds) ?? (src === "FEED" ? "Nearby incident" : "Service update");
}

function isLiveFeedTime(time: string): boolean {
  const lower = time.toLowerCase();
  return lower === "live" || lower === "now";
}

function feedThread(
  timestampLabel: string | undefined,
  lifecycle: ReturnType<typeof deriveLifecycle>,
): AlertUpdateEntry[] {
  if (!timestampLabel || timestampLabel === "now" || timestampLabel === "live") return [];
  const resolved = lifecycle === "resolved";
  return [
    {
      time: timestampLabel,
      title: resolved ? "Service resolved" : "First reported",
      tone: resolved ? "resolved" : "muted",
    },
  ];
}

function feedCurrentStatus(
  event: FeedEvent,
  summary: string | undefined,
  severity: ReturnType<typeof feedSeverity>,
  lifecycle: ReturnType<typeof deriveLifecycle>,
): string | undefined {
  return (
    leadSentences(compactAlertSummary(event.title, ""), 2, 200)
    ?? summary
    ?? severityStatusPhrase(severity, lifecycle)
  );
}

function feedEventToAlertItem(event: FeedEvent, index: number): AlertFeedItem {
  const routeIds = normalizeAlertRoutes(event.line ? [event.line] : []);
  const sourceInfo = sourceFromFeedEvent(event);
  const summary = leadSentences(compactAlertSummary(event.detail, event.title), 2, 200);
  const sourceContext = feedSourceContext(sourceInfo.sourceLabel, event.src);
  const severity = feedSeverity(event.sev, event.src);
  const lifecycle = deriveLifecycle(`${event.title} ${event.detail}`);
  const currentStatus = feedCurrentStatus(event, summary, severity, lifecycle);
  const timestampLabel = compactAlertTimestamp(event.time);

  return {
    id: `update-${index}-${routeIds.join("-") || "system"}-${alertSlug(event.title)}`,
    routeIds,
    serviceName: feedServiceName(routeIds, event.src),
    title: feedItemTitle(event, routeIds),
    summary,
    context: sourceContext,
    timestampLabel,
    severity,
    lifecycle,
    statusLabel: statusLabelFor(lifecycle, severity),
    source: sourceInfo.source,
    sourceLabel: sourceInfo.sourceLabel,
    isLive: isLiveFeedTime(event.time),
    expandable: Boolean(currentStatus || summary || sourceContext),
    details: {
      currentStatus,
      impact: summary,
      source: sourceContext,
      updatedAt: timestampLabel,
      updates: feedThread(timestampLabel, lifecycle),
    },
  };
}

export function normalizeRecentUpdates(
  updates: FeedEvent[],
): AlertFeedItem[] {
  return updates.map((event, index) => feedEventToAlertItem(event, index));
}

function affectedStopsContext(
  stops: string[] | undefined,
  direction: string | undefined,
): string | undefined {
  if (stops?.length) {
    const extra = stops.length > 3 ? ` +${stops.length - 3}` : "";
    return `Affected: ${stops.slice(0, 3).join(", ")}${extra}`;
  }
  return direction || undefined;
}

function alertLifecycleText(alert: ServiceAlert): string {
  return [
    alert.title,
    alert.sub,
    alert.aiContext ?? "",
    alert.fullText ?? "",
    alert.activity?.map((entry) => entry.e).join(" ") ?? "",
  ].join(" ");
}

function isLiveTimestamp(timestampLabel: string, lastUpdate: string): boolean {
  return timestampLabel === "live" || timestampLabel === "now" || /just now/i.test(lastUpdate);
}

function isExpandableAlert(input: {
  impact?: string;
  summary?: string;
  stopCount?: number;
  estClear?: string;
  alternatives?: string;
  updateCount: number;
}): boolean {
  return Boolean(
    input.impact
    || input.summary
    || input.stopCount
    || input.estClear
    || input.alternatives
    || input.updateCount,
  );
}

function alertNarrative(alert: ServiceAlert): string | undefined {
  return alert.aiContext ?? alert.fullText ?? alert.sub;
}

function normalizeServiceAlert(
  alert: ServiceAlert,
  index: number,
): AlertFeedItem {
  const routeIds = normalizeAlertRoutes(alert.lines);
  const severity = alertSeverity(alert);
  const summary = leadSentences(compactAlertSummary(alert.sub, alert.title), 2, 200);
  const narrative = alertNarrative(alert);
  const impact = leadSentences(compactAlertSummary(narrative, alert.title), 3, 320);
  const affectedStops = alert.affectedStops?.map(cleanPassengerAlertText).filter(Boolean);
  const timestampLabel = compactAlertTimestamp(alert.lastUpdate || alert.startedAgo);
  const lifecycle = deriveLifecycle(alertLifecycleText(alert));
  const updates = buildUpdateThread(alert, routeIds);
  const alternatives = parseAlertAlternatives(narrative, alert.estClear);

  return {
    id: `alert-${index}-${routeIds.join("-") || "system"}-${alertSlug(alert.title)}`,
    routeIds,
    serviceName: serviceNameForRoutes(routeIds) ?? "Service alert",
    title: compactAlertTitle(alert.title, routeIds),
    summary,
    context: affectedStopsContext(affectedStops, alert.direction),
    timestampLabel,
    severity,
    lifecycle,
    statusLabel: statusLabelFor(lifecycle, severity),
    source: "mta",
    affectedStops,
    isLive: isLiveTimestamp(timestampLabel, alert.lastUpdate),
    expandable: isExpandableAlert({
      impact,
      summary,
      stopCount: affectedStops?.length,
      estClear: alert.estClear,
      alternatives,
      updateCount: updates.length,
    }),
    details: {
      currentStatus: impact ?? summary ?? severityStatusPhrase(severity, lifecycle),
      impact,
      whatHappened: summary,
      alternatives,
      direction: alert.direction ? cleanPassengerAlertText(alert.direction) : undefined,
      affectedStops,
      source: "MTA service alert",
      updatedAt: timestampLabel,
      updates,
    },
  };
}

function buildUpdateThread(
  alert: ServiceAlert,
  routeIds: string[],
): AlertUpdateEntry[] {
  if (alert.activity?.length) {
    return alert.activity.map((entry) => {
      const title = sentenceCase(cleanPassengerAlertText(entry.e));
      return {
        time: compactAlertTimestamp(entry.t),
        title,
        tone: /resolved|resumed|returned to normal|restored|cleared/i.test(title)
          ? ("resolved" as const)
          : ("muted" as const),
      };
    });
  }

  const timestamp = compactAlertTimestamp(alert.startedAgo);
  return timestamp && timestamp !== "now" && timestamp !== "live"
    ? [
        {
          time: timestamp,
          title: "First reported",
          summary: compactAlertTitle(alert.title, routeIds),
          tone: "muted",
        },
      ]
    : [];
}

function alertSeverity(alert: ServiceAlert): AlertFeedSeverity {
  const text = `${alert.title} ${alert.sub} ${alert.fullText ?? ""}`.toLowerCase();
  if (/suspend|suspension|no trains|no .* service|bypass/.test(text)) {
    return "suspension";
  }
  if (alert.sev === "major") {
    return "major";
  }
  if (alert.sev === "planned") {
    return "planned";
  }

  return /delay|delayed|slow|running with delays/.test(text)
    ? "minor"
    : "notice";
}

function feedSeverity(
  severity: Severity,
  source: FeedEvent["src"],
): AlertFeedSeverity {
  if (source === "FEED") {
    return "incident";
  }
  if (severity === "major") {
    return "major";
  }
  if (severity === "planned") {
    return "planned";
  }

  return severity === "minor" ? "minor" : "notice";
}

type FeedEventSource = {
  source: AlertFeedSource;
  sourceLabel?: string;
};

function sourceFromFeedEvent(event: FeedEvent): FeedEventSource {
  const sourceLabel = event.detail.match(/@[\w_]+/)?.[0];
  if (sourceLabel) {
    return { source: "social", sourceLabel };
  }
  if (event.src === "MTA") {
    return { source: "mta" };
  }

  return event.src === "SYSTEM"
    ? { source: "internal" }
    : { source: "nyc-alert" };
}

function statusLabelFor(
  lifecycle: AlertLifecycle,
  severity: AlertFeedSeverity,
): string {
  if (lifecycle === "resolved") {
    return "Resolved";
  }
  if (severity === "planned") {
    return "Planned";
  }
  if (severity === "suspension" || severity === "major") {
    return "Major";
  }
  if (severity === "minor") {
    return "Delay";
  }

  return severity === "incident" ? "Incident" : "";
}

function severityStatusPhrase(
  severity: AlertFeedSeverity,
  lifecycle: AlertLifecycle,
): string {
  if (lifecycle === "resolved") {
    return "Service has returned to normal.";
  }
  if (severity === "suspension") {
    return "Service is suspended on the affected segment.";
  }
  if (severity === "major") {
    return "Major service disruption in effect.";
  }
  if (severity === "planned") {
    return "Planned service change in effect.";
  }

  return severity === "minor"
    ? "Trains are running with delays."
    : "Service change in effect.";
}
