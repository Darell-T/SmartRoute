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
} from "./alert-feed-copy";
import {
  normalizeAlertRoutes,
  serviceNameForRoutes,
} from "./alert-line-identities";
import { groupAlertThreads, sortAlertFeedItems } from "./alert-feed-threading";
import type {
  AlertFeedItem,
  AlertFeedSeverity,
  AlertLifecycle,
  AlertUpdateEntry,
  FeedEvent,
  ServiceAlert,
  Severity,
} from "./types";

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

export function normalizeRecentUpdates(
  updates: FeedEvent[],
): AlertFeedItem[] {
  return updates.map((event, index) => {
    const routeIds = normalizeAlertRoutes(event.line ? [event.line] : []);
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
    const title =
      event.src === "FEED"
        ? compactFeedTitle(event.title, routeIds)
        : compactAlertTitle(event.title, routeIds);
    const lifecycle = deriveLifecycle(`${event.title} ${event.detail}`);
    const currentStatus =
      leadSentences(compactAlertSummary(event.title, ""), 2, 200) ??
      summary ??
      severityStatusPhrase(severity, lifecycle);
    const timestampLabel = compactAlertTimestamp(event.time);
    const thread =
      timestampLabel && timestampLabel !== "now" && timestampLabel !== "live"
        ? [
            {
              time: timestampLabel,
              title: lifecycle === "resolved" ? "Service resolved" : "First reported",
              tone: lifecycle === "resolved" ? ("resolved" as const) : ("muted" as const),
            },
          ]
        : [];

    return {
      id: `update-${index}-${routeIds.join("-") || "system"}-${alertSlug(event.title)}`,
      routeIds,
      serviceName:
        serviceNameForRoutes(routeIds) ??
        (event.src === "FEED" ? "Nearby incident" : "Service update"),
      title,
      summary,
      context: sourceContext,
      timestampLabel,
      severity,
      lifecycle,
      statusLabel: statusLabelFor(lifecycle, severity),
      source: sourceInfo.source,
      sourceLabel: sourceInfo.sourceLabel,
      isLive:
        event.time.toLowerCase() === "live" || event.time.toLowerCase() === "now",
      expandable: Boolean(currentStatus || summary || sourceContext),
      details: {
        currentStatus,
        impact: summary,
        source: sourceContext,
        updatedAt: timestampLabel,
        updates: thread,
      },
    };
  });
}

function normalizeServiceAlert(
  alert: ServiceAlert,
  index: number,
): AlertFeedItem {
  const routeIds = normalizeAlertRoutes(alert.lines);
  const severity = alertSeverity(alert);
  const summary = leadSentences(
    compactAlertSummary(alert.sub, alert.title),
    2,
    200,
  );
  const impact = leadSentences(
    compactAlertSummary(
      alert.aiContext ?? alert.fullText ?? alert.sub,
      alert.title,
    ),
    3,
    320,
  );
  const affectedStops = alert.affectedStops
    ?.map(cleanPassengerAlertText)
    .filter(Boolean);
  const context = affectedStops?.length
    ? `Affected: ${affectedStops.slice(0, 3).join(", ")}${
        affectedStops.length > 3 ? ` +${affectedStops.length - 3}` : ""
      }`
    : alert.direction || undefined;
  const timestampLabel = compactAlertTimestamp(alert.lastUpdate || alert.startedAgo);
  const lifecycle = deriveLifecycle(
    [
      alert.title,
      alert.sub,
      alert.aiContext ?? "",
      alert.fullText ?? "",
      alert.activity?.map((entry) => entry.e).join(" ") ?? "",
    ].join(" "),
  );
  const updates = buildUpdateThread(alert, routeIds);
  const alternatives = parseAlertAlternatives(
    alert.aiContext ?? alert.fullText ?? alert.sub,
    alert.estClear,
  );

  return {
    id: `alert-${index}-${routeIds.join("-") || "system"}-${alertSlug(alert.title)}`,
    routeIds,
    serviceName: serviceNameForRoutes(routeIds) ?? "Service alert",
    title: compactAlertTitle(alert.title, routeIds),
    summary,
    context,
    timestampLabel,
    severity,
    lifecycle,
    statusLabel: statusLabelFor(lifecycle, severity),
    source: "mta",
    affectedStops,
    isLive:
      timestampLabel === "live" ||
      timestampLabel === "now" ||
      /just now/i.test(alert.lastUpdate),
    expandable: Boolean(
      impact ||
        summary ||
        affectedStops?.length ||
        alert.estClear ||
        alternatives ||
        updates.length,
    ),
    details: {
      currentStatus: impact ?? summary ?? severityStatusPhrase(severity, lifecycle),
      impact,
      whatHappened: summary,
      alternatives,
      direction: alert.direction
        ? cleanPassengerAlertText(alert.direction)
        : undefined,
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

function sourceFromFeedEvent(event: FeedEvent): {
  source: "mta" | "nyc-alert" | "social" | "internal";
  sourceLabel?: string;
} {
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

function normalizeIssueText(value: string): string {
  return cleanPassengerAlertText(value)
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, "")
    .replace(/\b(?:college|station|av|avenue|st|street)\b/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 40);
}
