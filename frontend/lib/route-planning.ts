import type { RouteCandidate, RouteStep, ServiceAlert, TripResponse } from "@/types";

/** Rail-capable transit step types accepted across the card, API, and map. */
export const TRANSIT_STEP_TYPES: ReadonlySet<RouteStep["type"]> = new Set([
  "SUBWAY",
  "BUS",
  "RAIL",
  "TRAIN",
  "LIGHT_RAIL",
  "TRAM",
]);

export function isTransitStep(step: RouteStep): boolean {
  return TRANSIT_STEP_TYPES.has(step.type);
}

export function deriveTransitRouteIds(steps: RouteStep[] = []) {
  const ids = new Set<string>();
  for (const step of steps) {
    if (!isTransitStep(step)) continue;
    const id = (step.route_id || step.train_line || "").trim().toUpperCase();
    if (id) ids.add(id);
  }
  return Array.from(ids);
}

export function normalizeTripCandidates(response: TripResponse) {
  const candidates = response.route_candidates;
  if (!Array.isArray(candidates) || candidates.length === 0) return null;
  if (typeof response.selected_route_index !== "number") return null;

  const normalized = candidates.filter(
    (candidate): candidate is RouteCandidate =>
      Boolean(
        candidate.id &&
          candidate.itinerary?.itinerary_id &&
          Array.isArray(candidate.steps) &&
          typeof candidate.index === "number" &&
          typeof candidate.total_minutes === "number" &&
          typeof candidate.score_breakdown?.transfers === "number",
      ),
  );
  const selected = normalized.find(
    (candidate) => candidate.index === response.selected_route_index,
  );
  if (!selected) return null;

  return {
    candidates: normalized,
    selected,
    selectedIndex: response.selected_route_index,
  };
}

export function routeCandidateLabel(steps: RouteStep[] = []) {
  const ids = deriveTransitRouteIds(steps);
  const transferStep = steps.find(
    (step, index) =>
      index > 0 && isTransitStep(step) && Boolean(step.departure_stop),
  );
  const firstTransit = steps.find(isTransitStep);
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
