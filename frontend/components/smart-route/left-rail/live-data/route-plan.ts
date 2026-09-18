import type { RouteCandidate, RouteStep as ApiRouteStep } from "@/types/api";
import { canonicalPlaceLabel } from "@/lib/canonical-itinerary-label";
import type { RoutePlan } from "../types";
import { cleanDestinationLabel } from "./formatters";
import {
  buildAlternatives,
  canonicalDurationMinutes,
  canonicalTransferCount,
  clockFromIso,
  firstTransitStep,
} from "./route-candidates";
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

function withArriveStep(
  steps: RoutePlan["steps"],
  merged: ApiRouteStep[],
): RoutePlan["steps"] {
  if (steps.length === 0) return steps;
  const lastRaw = merged[merged.length - 1];
  const dest =
    lastRaw.arrival_stop
    || lastRaw.departure_stop
    || steps[steps.length - 1].title
    || "Destination";
  const next = steps.slice();
  next[next.length - 1] = {
    ...next[next.length - 1],
    type: "arrive",
    action: "Arrive",
    title: cleanDestinationLabel(dest) || "Destination",
    detail: "Arrive at destination",
  };
  return next;
}

function planHeadline(
  activeRouteCandidate: RouteCandidate | null | undefined,
  switchHeadline?: string | null,
): string {
  const override = publicRecommendationText(switchHeadline);
  if (override) return override;
  if (!activeRouteCandidate) return "Choose a destination for route guidance.";
  if (activeRouteCandidate.is_recommended === false) return "Alternative route engaged.";
  return "Route plan is live.";
}

function planRationale(
  candidate: RouteCandidate | null | undefined,
  routeSteps: ApiRouteStep[] | undefined,
  routeCandidates: RouteCandidate[] | undefined,
  routeEntryContext: RoutePlan["entryContext"],
): string {
  if (!candidate) return "Nearby arrivals are live within a half-mile radius.";
  if (routeEntryContext === "chat") return "";
  return buildVisibleRouteReason(candidate, routeSteps, routeCandidates);
}

function journeyPlacesFrom(
  candidate: RouteCandidate | null | undefined,
): string[] | undefined {
  const itinerary = candidate?.itinerary;
  if (!itinerary) return undefined;
  const waypoints = itinerary.waypoints ?? [];
  return [
    canonicalPlaceLabel(itinerary.origin, "Your location"),
    ...waypoints.map((waypoint) => canonicalPlaceLabel(waypoint, "Waypoint")),
    canonicalPlaceLabel(itinerary.destination, "Destination"),
  ].filter((place, index, values) => index === 0 || values[index - 1] !== place);
}

function liveCountdown(value: number | undefined): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  return Math.max(0, Math.round(value));
}

function emptyPlanFacts(hasSteps: boolean): Pick<RoutePlan, "eta" | "totalTime" | "leaveByLabel" | "transferCount"> {
  return {
    eta: "Live",
    totalTime: hasSteps ? "Calculated" : "Pending",
    leaveByLabel: undefined,
    transferCount: undefined,
  };
}

function candidateArrivalClock(candidate: RouteCandidate): string | null {
  const fromItinerary = clockFromIso(candidate.itinerary?.arrival_at);
  if (fromItinerary) return fromItinerary;
  return clockFromIso(candidate.arrival_at);
}

function selectedEta(routeEta: string | null | undefined, arrivalClock: string | null): string {
  if (routeEta) return routeEta;
  if (arrivalClock) return arrivalClock;
  return "Live";
}

function selectedTotalTime(
  routeTotalTime: string | null | undefined,
  durationMinutes: number | null,
  hasSteps: boolean,
): string {
  if (routeTotalTime) return routeTotalTime;
  if (durationMinutes !== null) return `${durationMinutes} min`;
  if (hasSteps) return "Calculated";
  return "Pending";
}

function selectedPlanFacts(
  candidate: RouteCandidate | null | undefined,
  routeEta: string | null | undefined,
  routeTotalTime: string | null | undefined,
  hasSteps: boolean,
): Pick<RoutePlan, "eta" | "totalTime" | "leaveByLabel" | "transferCount"> {
  if (!candidate) return emptyPlanFacts(hasSteps);
  return {
    eta: selectedEta(routeEta, candidateArrivalClock(candidate)),
    totalTime: selectedTotalTime(
      routeTotalTime,
      canonicalDurationMinutes(candidate),
      hasSteps,
    ),
    leaveByLabel: clockFromIso(candidate.itinerary?.departure_at) ?? undefined,
    transferCount: canonicalTransferCount(candidate),
  };
}

function planHeadsign(
  transitStep: ApiRouteStep | undefined,
  steps: RoutePlan["steps"],
): string {
  return (
    cleanDestinationLabel(transitStep?.direction || transitStep?.arrival_stop)
    || (steps.length > 0 ? steps[steps.length - 1].title : "")
    || "Walking route"
  );
}

function pickedLineFrom(transitStep: ApiRouteStep | undefined): string {
  return transitStep?.train_line || transitStep?.route_id || "";
}

export function buildPlan(
  routeSteps: ApiRouteStep[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
  routeCandidates?: RouteCandidate[],
  switchHeadline?: string | null,
  routeEta?: string | null,
  routeTotalTime?: string | null,
  routeEntryContext: "chat" | "map_search" | "deep_link" | "restored" = "map_search",
): RoutePlan {
  const transitStep = firstTransitStep(routeSteps);
  const merged = mergeConsecutiveWalks(routeSteps ?? []);
  const steps = withArriveStep(merged.map(routeStepToRailStep), merged);
  const facts = selectedPlanFacts(
    activeRouteCandidate,
    routeEta,
    routeTotalTime,
    steps.length > 0,
  );
  const shared = {
    entryContext: routeEntryContext,
    headline: planHeadline(activeRouteCandidate, switchHeadline),
    rationale: planRationale(
      activeRouteCandidate,
      routeSteps,
      routeCandidates,
      routeEntryContext,
    ),
    ...facts,
    pickedLine: pickedLineFrom(transitStep),
    steps,
    alternatives: buildAlternatives(routeCandidates, activeRouteCandidate),
    notes: [],
  };
  if (!activeRouteCandidate) return shared;
  return {
    ...shared,
    headsign: planHeadsign(transitStep, steps),
    isAlternativeRoute: activeRouteCandidate.is_recommended === false,
    nextDepartureMinutes: liveCountdown(transitStep?.minutes_until_train_arrives),
    journeyPlaces: journeyPlacesFrom(activeRouteCandidate),
    strip: stripFromSteps(routeSteps),
    detailSteps: detailStepsFromCanonicalItinerary(
      routeSteps,
      activeRouteCandidate.itinerary,
    ),
  };
}
