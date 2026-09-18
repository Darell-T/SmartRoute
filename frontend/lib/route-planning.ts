import type { RouteStep, ServiceAlert } from "@/types";
import type { ValidatedTripResponse } from "./trip-response";

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

export function normalizeTripCandidates(response: ValidatedTripResponse) {
  const selected = response.route_candidates.find(
    (candidate) => candidate.index === response.selected_route_index,
  );
  if (!selected) return null;

  return {
    candidates: response.route_candidates,
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
