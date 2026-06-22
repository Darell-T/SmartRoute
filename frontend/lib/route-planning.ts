import type { RouteCandidate, RouteStep, ServiceAlert, TripResponse } from "@/types";

export function deriveTransitRouteIds(steps: RouteStep[] = []) {
  const ids = new Set<string>();
  for (const step of steps) {
    if (step.type !== "SUBWAY" && step.type !== "BUS") continue;
    const id = (step.route_id || step.train_line || "").trim().toUpperCase();
    if (id) ids.add(id);
  }
  return Array.from(ids);
}

export function normalizeTripCandidates(response: TripResponse) {
  const candidates =
    response.route_candidates?.length
      ? response.route_candidates
      : [
          {
            id: "candidate-0",
            index: response.selected_route_index ?? 0,
            steps: response.route,
            is_recommended: true,
            recommendation_reason: "Recommended route after checking live service conditions.",
          },
        ];

  const normalized = candidates.map((candidate, position) => ({
    ...candidate,
    id: candidate.id || `candidate-${candidate.index ?? position}`,
    index: candidate.index ?? position,
    steps: candidate.steps?.length ? candidate.steps : response.route,
  }));

  const selectedIndex =
    response.selected_route_index ??
    normalized.find((candidate) => candidate.is_recommended)?.index ??
    0;
  const selected =
    normalized.find((candidate) => candidate.index === selectedIndex) ??
    normalized.find((candidate) => candidate.is_recommended) ??
    normalized[0] ??
    null;

  return {
    candidates: normalized as RouteCandidate[],
    selected,
    selectedIndex,
  };
}

export function routeCandidateLabel(steps: RouteStep[] = []) {
  const ids = deriveTransitRouteIds(steps);
  const transferStep = steps.find(
    (step, index) =>
      index > 0 &&
      (step.type === "SUBWAY" || step.type === "BUS") &&
      Boolean(step.departure_stop),
  );
  const firstTransit = steps.find(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const via = transferStep?.departure_stop || firstTransit?.departure_stop || "direct";
  const label = ids.length ? ids.join("/") : "Walk";
  return `${label} via ${via}`;
}

function alertRouteIds(alert: ServiceAlert) {
  return (alert.routeIds || alert.route_ids || [])
    .map((route) => route.trim().toUpperCase())
    .filter(Boolean);
}

export function isAlertForRouteIds(alert: ServiceAlert, routeIds: string[]) {
  if (routeIds.length === 0) return false;
  const scoped = new Set(routeIds.map((route) => route.toUpperCase()));
  return alertRouteIds(alert).some((route) => scoped.has(route));
}
