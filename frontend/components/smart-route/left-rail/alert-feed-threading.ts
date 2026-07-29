import { cleanPassengerAlertText } from "./alert-feed-copy";
import {
  normalizeAlertRoutes,
  serviceNameForRoutes,
} from "./alert-line-identities";
import type {
  AlertFeedItem,
  AlertFeedSeverity,
  AlertLifecycle,
  AlertUpdateEntry,
} from "./types";

export function groupAlertThreads(items: AlertFeedItem[]): AlertFeedItem[] {
  const groups = new Map<string, AlertFeedItem[]>();
  const order: string[] = [];
  for (const item of items) {
    const key = issueSignature(item);
    const group = groups.get(key);
    if (group) {
      group.push(item);
    } else {
      groups.set(key, [item]);
      order.push(key);
    }
  }

  return order.flatMap((key) => {
    const members = groups.get(key);
    if (!members) {
      return [];
    }
    if (members.length === 1) {
      return members;
    }

    const sorted = [...members].sort(
      (left, right) =>
        timeRank(left.timestampLabel) - timeRank(right.timestampLabel),
    );
    const [head, ...rest] = sorted;
    const routeIds = normalizeAlertRoutes(
      members.flatMap((item) => item.routeIds),
    );
    const updates: AlertUpdateEntry[] = [
      ...(head.details?.updates ?? []),
      ...rest.map((item) => ({
        time: item.timestampLabel,
        title: item.title,
        summary: item.summary,
        tone: "muted" as const,
      })),
    ];

    return [
      {
        ...head,
        routeIds,
        serviceName: serviceNameForRoutes(routeIds) ?? head.serviceName,
        expandable: true,
        details: {
          ...head.details,
          currentStatus: head.details?.currentStatus ?? head.summary,
          updates: dedupeUpdates(updates),
        },
      },
    ];
  });
}

export function sortAlertFeedItems(items: AlertFeedItem[]): AlertFeedItem[] {
  return [...items].sort(
    (left, right) =>
      lifecycleRank(left.lifecycle) - lifecycleRank(right.lifecycle) ||
      severityRank(right.severity) - severityRank(left.severity) ||
      timeRank(left.timestampLabel) - timeRank(right.timestampLabel),
  );
}

export function latestAlertUpdateLabel(
  items: AlertFeedItem[],
): string | undefined {
  let best = Infinity;
  for (const item of items) {
    best = Math.min(best, timeRank(item.timestampLabel));
  }
  if (!Number.isFinite(best) || best >= 999) {
    return undefined;
  }
  if (best === 0) {
    return "Updated just now";
  }

  return best < 60
    ? `Updated ${best} min ago`
    : `Updated ${Math.round(best / 60)} hr ago`;
}

function issueSignature(item: AlertFeedItem): string {
  const source = `${item.summary ?? ""} ${item.title} ${item.context ?? ""}`;
  const near = source.match(
    /\b(?:near|at|between)\s+([A-Za-z0-9][A-Za-z0-9 .'\-\/]{3,40})/i,
  );
  const place = near
    ? normalizeIssueText(near[1])
    : item.affectedStops?.[0]
      ? normalizeIssueText(item.affectedStops[0])
      : "";

  return place
    ? `${item.routeIds.join(",")}|${place}`
    : `${item.routeIds.join(",")}|${normalizeIssueText(item.title)}`;
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

function dedupeUpdates(updates: AlertUpdateEntry[]): AlertUpdateEntry[] {
  const seen = new Set<string>();
  return updates.filter((entry) => {
    const key = `${entry.time}|${entry.title.toLowerCase()}`;
    if (seen.has(key)) {
      return false;
    }

    seen.add(key);
    return true;
  });
}

function lifecycleRank(lifecycle: AlertLifecycle): number {
  return lifecycle === "resolved" ? 1 : 0;
}

function severityRank(severity: AlertFeedSeverity): number {
  return {
    incident: 6,
    suspension: 5,
    major: 4,
    minor: 3,
    planned: 2,
    notice: 1,
  }[severity];
}

function timeRank(label: string): number {
  if (label === "live" || label === "now") {
    return 0;
  }

  const minutes = label.match(/^(\d+)m$/);
  if (minutes) {
    return Number(minutes[1]);
  }

  const hours = label.match(/^(\d+)h$/);
  return hours ? Number(hours[1]) * 60 : 999;
}
