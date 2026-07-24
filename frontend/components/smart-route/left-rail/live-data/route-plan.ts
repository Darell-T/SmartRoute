import type { RouteCandidate, RouteStep as ApiRouteStep } from "@/types/api";
import type { RoutePlan } from "../types";
import { cleanDestinationLabel, formatClockAt } from "./formatters";
import { buildAlternatives, candidateEtaMinutes } from "./route-candidates";
import {
  buildVisibleRouteReason,
  publicRecommendationText,
} from "./route-reason-copy";
import {
  detailStepsFromCanonicalItinerary,
  mergeConsecutiveWalks,
  routeStepToRailStep,
  stripFromSteps,
} from "./route-steps";

function canonicalPlaceLabel(place: unknown, fallback: string): string {
  if (typeof place === "string" && place.trim()) return place.trim();
  if (place && typeof place === "object" && !Array.isArray(place)) {
    const record = place as Record<string, unknown>;
    for (const key of ["display_name", "label", "name", "address"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return fallback;
}

export function buildPlan(
  routeSteps: ApiRouteStep[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
  routeCandidates?: RouteCandidate[],
  switchHeadline?: string | null,
  routeEta?: string | null,
  routeTotalTime?: string | null,
  nowMs = Date.now(),
  routeEntryContext: "chat" | "map_search" | "deep_link" | "restored" = "map_search",
): RoutePlan {
  const transitStep = routeSteps?.find(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const line = transitStep?.train_line || transitStep?.route_id || "";
  const merged = mergeConsecutiveWalks(routeSteps ?? []);
  const steps: RoutePlan["steps"] = merged.map(routeStepToRailStep);
  // The final step of any plan is the destination: relabel it "Arrive" with
  // the celebratory checkered-flag icon, naming where you end up.
  if (steps.length > 0) {
    const lastRaw = merged[merged.length - 1];
    const dest =
      lastRaw.arrival_stop
      || lastRaw.departure_stop
      || steps[steps.length - 1].title
      || "Destination";
    steps[steps.length - 1] = {
      ...steps[steps.length - 1],
      type: "arrive",
      action: "Arrive",
      title: cleanDestinationLabel(dest) || "Destination",
      detail: "Arrive at destination",
    };
  }

  let defaultHeadline = "Choose a destination for route guidance.";
  if (activeRouteCandidate) {
    defaultHeadline =
      activeRouteCandidate.is_recommended === false
        ? "Alternative route engaged."
        : "Route plan is live.";
  }

  const headsign =
    cleanDestinationLabel(transitStep?.direction || transitStep?.arrival_stop)
    || (steps.length > 0 ? steps[steps.length - 1].title : "")
    || "Walking route";

  const selectedEtaMinutes = candidateEtaMinutes(activeRouteCandidate);
  // Preference: canonical itinerary.arrival_at → now+eta (legacy).
  let selectedEta: string | null = null;
  const arrivalAtIso = activeRouteCandidate?.arrival_at;
  if (typeof arrivalAtIso === "string" && arrivalAtIso.trim() && nowMs > 0) {
    const parsed = Date.parse(arrivalAtIso);
    if (Number.isFinite(parsed)) {
      selectedEta = formatClockAt(parsed);
    }
  }
  if (selectedEta === null && selectedEtaMinutes !== null && nowMs > 0) {
    selectedEta = formatClockAt(nowMs + selectedEtaMinutes * 60_000);
  }
  const selectedTotalTime =
    selectedEtaMinutes !== null ? `${selectedEtaMinutes} min` : null;
  const journeyPlaces = activeRouteCandidate?.itinerary
    ? [
        canonicalPlaceLabel(activeRouteCandidate.itinerary.origin, "Your location"),
        ...(activeRouteCandidate.itinerary.waypoints ?? []).map((waypoint) =>
          canonicalPlaceLabel(waypoint, "Waypoint"),
        ),
        canonicalPlaceLabel(activeRouteCandidate.itinerary.destination, "Destination"),
      ].filter((place, index, values) => index === 0 || values[index - 1] !== place)
    : undefined;

  // Transfers: prefer candidate score_breakdown (from itinerary.transfer_count
  // via agentRoutePlanFromCards). Recompute only when absent.
  // Recompute rule: transit-vehicle boardings minus one; walks never count.
  const transitLegs = merged.filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const fromCandidate = activeRouteCandidate?.score_breakdown?.transfers;
  const transferCount =
    typeof fromCandidate === "number" && Number.isFinite(fromCandidate)
      ? Math.max(0, Math.round(fromCandidate))
      : Math.max(0, transitLegs.length - 1);

  // "Leave by" backs the transit departure off by the approach walk; if the
  // walk consumes the whole wait, it's simply "now".
  const firstWalkMinutes =
    merged[0]?.type === "WALK" &&
    typeof merged[0].minutes_until_arrival === "number"
      ? Math.max(0, Math.round(merged[0].minutes_until_arrival))
      : 0;
  const departsIn = transitStep?.minutes_until_train_arrives;
  const nextDepartureMinutes =
    typeof departsIn === "number" && Number.isFinite(departsIn)
      ? Math.max(0, Math.round(departsIn))
      : undefined;
  let leaveByLabel: string | undefined;
  if (typeof departsIn === "number" && Number.isFinite(departsIn) && nowMs > 0) {
    const minutesUntilLeave = departsIn - firstWalkMinutes;
    leaveByLabel =
      minutesUntilLeave <= 0
        ? "now"
        : formatClockAt(nowMs + minutesUntilLeave * 60_000);
  }

  return {
    headline: publicRecommendationText(switchHeadline) || defaultHeadline,
    rationale: !activeRouteCandidate
      ? "Nearby arrivals are live within a half-mile radius."
      : routeEntryContext === "chat"
        ? ""
        : buildVisibleRouteReason(
            activeRouteCandidate,
            routeSteps,
            routeCandidates,
          ),
    headsign: activeRouteCandidate ? headsign : undefined,
    isAlternativeRoute: activeRouteCandidate?.is_recommended === false,
    eta: (activeRouteCandidate && (routeEta || selectedEta)) || "Live",
    totalTime:
      (activeRouteCandidate && (routeTotalTime || selectedTotalTime))
      || (steps.length ? "Calculated" : "Pending"),
    leaveByLabel: activeRouteCandidate ? leaveByLabel : undefined,
    nextDepartureMinutes: activeRouteCandidate ? nextDepartureMinutes : undefined,
    transferCount: activeRouteCandidate ? transferCount : undefined,
    journeyPlaces,
    strip: activeRouteCandidate ? stripFromSteps(routeSteps) : undefined,
    detailSteps: activeRouteCandidate
      ? detailStepsFromCanonicalItinerary(routeSteps, activeRouteCandidate.itinerary)
      : undefined,
    pickedLine: line,
    steps,
    alternatives: buildAlternatives(routeCandidates, activeRouteCandidate, nowMs),
    notes: [],
  };
}
