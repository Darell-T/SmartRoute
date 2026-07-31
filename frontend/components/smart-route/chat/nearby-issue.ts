import type { NearbyTransitIssue } from "@/types/api";

const MAX_ISSUE_AGE_MS = 10 * 60 * 1000;
const CONFIDENCE_PRIORITY = {
  confirmed: 2,
  strong_inference: 1,
} as const;
const STATUS_PRIORITY = {
  stalled: 3,
  service_delay: 2,
  delays_developing: 1,
} as const;

export interface HomeNearbyIssue {
  id: string;
  confidence: NearbyTransitIssue["confidence"];
  label: string;
  interactive: boolean;
}

function normalizedRoutes(routeIds: readonly string[]): Set<string> {
  return new Set(
    routeIds
      .map((routeId) => routeId.trim().toUpperCase())
      .filter(Boolean),
  );
}

function isFresh(issue: NearbyTransitIssue, nowMs: number): boolean {
  const observedAt = Date.parse(issue.observed_at);
  return Number.isFinite(observedAt) && nowMs - observedAt <= MAX_ISSUE_AGE_MS;
}

function issueIsRelevant(
  issue: NearbyTransitIssue,
  nearbyRoutes: Set<string>,
  hasPlannedRoute: boolean,
): boolean {
  if (
    issue.relevance === "planned_route" &&
    hasPlannedRoute
  ) {
    return true;
  }
  return issue.route_ids.some((routeId) =>
    nearbyRoutes.has(routeId.toUpperCase()),
  );
}

function confidenceAwareLabel(issue: NearbyTransitIssue): string {
  const summary = issue.summary.replace(/\s+/g, " ").trim();
  if (issue.confidence === "confirmed") return summary;
  if (/^(possible|delay may be developing)\b/i.test(summary)) return summary;
  if (issue.status === "delays_developing") {
    return `Delay may be developing${summary ? ` · ${summary}` : ""}`;
  }
  return `Possible ${summary.replace(/^(a\s+)?/i, "").toLowerCase()}`;
}

export function selectHomeNearbyIssue({
  issues,
  nearbyRouteIds,
  hasPlannedRoute,
  nowMs = Date.now(),
}: {
  issues: readonly NearbyTransitIssue[];
  nearbyRouteIds: readonly string[];
  hasPlannedRoute: boolean;
  nowMs?: number;
}): HomeNearbyIssue | null {
  const nearbyRoutes = normalizedRoutes(nearbyRouteIds);
  const eligible = issues
    .filter(
      (issue) =>
        (issue.confidence === "confirmed" ||
          issue.confidence === "strong_inference") &&
        isFresh(issue, nowMs) &&
        issueIsRelevant(issue, nearbyRoutes, hasPlannedRoute),
    )
    .sort(
      (left, right) =>
        CONFIDENCE_PRIORITY[right.confidence] -
          CONFIDENCE_PRIORITY[left.confidence] ||
        STATUS_PRIORITY[right.status] - STATUS_PRIORITY[left.status] ||
        Date.parse(right.observed_at) - Date.parse(left.observed_at),
    );

  const issue = eligible[0];
  if (!issue) return null;
  return {
    id: issue.id,
    confidence: issue.confidence,
    label: confidenceAwareLabel(issue),
    interactive: issue.source_types.length > 0,
  };
}
