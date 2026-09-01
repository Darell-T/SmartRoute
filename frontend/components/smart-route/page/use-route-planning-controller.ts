"use client";

import { useRef, useState } from "react";
import type {
  DestinationSelection,
  RouteCandidate,
  RouteStep,
} from "@/types";
import { enrichRoute, planTrip } from "@/lib/api";
import {
  deriveTransitRouteIds,
  normalizeTripCandidates,
} from "@/lib/route-planning";

type UserLocation = { lng: number; lat: number } | null;

/** Origin of the currently displayed route. This is presentation context, not
 * route data: selecting an alternative must preserve it. */
export type RouteEntryContext = "chat" | "map_search" | "deep_link" | "restored";

type RoutePlanningControllerInput = {
  userLocation: UserLocation;
};

export type ExternalRoutePlan = {
  destination: DestinationSelection;
  candidates: RouteCandidate[];
  activeCandidateId: string;
  recommendationText: string;
  entryContext?: RouteEntryContext;
};

export type RoutePlanningPhase = "idle" | "cancellable" | "finalizing";

type SubmitPrep =
  | { ok: false; error?: string }
  | { ok: true; destination: string };

export function prepareRouteSubmit(
  destinationOverride: string | undefined,
  inputValue: string,
  userLocation: UserLocation,
): SubmitPrep {
  const destination = (destinationOverride ?? inputValue).trim();
  if (!destination) return { ok: false };
  if (!userLocation) return { ok: false, error: "Waiting for GPS location..." };
  return { ok: true, destination };
}

export function planningErrorText(error: unknown): string {
  const message = error instanceof Error ? error.message : "Unknown error";
  if (message.includes("Failed to plan trip")) {
    return "No route found. Try a more specific address.";
  }
  return "Connection error. Check your network and try again.";
}

export function useRoutePlanningController({
  userLocation,
}: RoutePlanningControllerInput) {
  const [inputValue, setInputValue] = useState("");
  const [selectedDestination, setSelectedDestination] =
    useState<DestinationSelection | null>(null);
  const [recommendationText, setRecommendationText] = useState("");
  const [routeEntryContext, setRouteEntryContext] =
    useState<RouteEntryContext>("map_search");
  // Canned line after the user switches to an alternative route;
  // overrides the rail's plan headline until the next trip or clear.
  const [switchHeadline, setSwitchHeadline] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [plannedRouteSteps, setPlannedRouteSteps] = useState<RouteStep[]>([]);
  const [routeCandidates, setRouteCandidates] = useState<RouteCandidate[]>([]);
  const [activeRouteCandidateId, setActiveRouteCandidateId] =
    useState<string | null>(null);
  const [planningPhase, setPlanningPhase] =
    useState<RoutePlanningPhase>("idle");

  const routePlanningAbortRef = useRef<AbortController | null>(null);
  const routePlanningRequestIdRef = useRef(0);

  function handleDestinationInputChange(value: string) {
    setInputValue(value);
    setSelectedDestination(null);
    if (
      routeCandidates.length > 0 ||
      plannedRouteSteps.length > 0 ||
      activeRouteCandidateId !== null ||
      errorText !== null
    ) {
      routePlanningRequestIdRef.current += 1;
      routePlanningAbortRef.current?.abort();
      routePlanningAbortRef.current = null;
      setIsLoading(false);
      setPlanningPhase("idle");
      setRouteCandidates([]);
      setActiveRouteCandidateId(null);
      setPlannedRouteSteps([]);
      setRecommendationText("");
      setSwitchHeadline(null);
      setErrorText(null);
    }
  }

    async function executePlan(
      requestId: number,
      abortController: AbortController,
      destination: string,
      destinationSelection: DestinationSelection | null,
      origin: NonNullable<UserLocation>,
    ) {
      await waitForCancellationCheckpoint();
      if (routePlanningRequestIdRef.current !== requestId || abortController.signal.aborted) {
        return;
      }
      setPlanningPhase("finalizing");
      const tripData = await planTrip(
        origin.lat,
        origin.lng,
        destination,
        destinationSelection,
        { signal: abortController.signal },
      );
      if (routePlanningRequestIdRef.current !== requestId) return;
      const normalizedTrip = normalizeTripCandidates(tripData);
      if (!normalizedTrip) {
        throw new Error("The route response is missing its canonical itinerary.");
      }
      setRouteCandidates(normalizedTrip.candidates);
      setActiveRouteCandidateId(normalizedTrip.selected.id);
      setPlannedRouteSteps(normalizedTrip.selected.steps);
      setSwitchHeadline(null);
      setRecommendationText(tripData.recommendation);
      if (destinationSelection) setSelectedDestination(destinationSelection);
    }

    async function handleSubmit(
    destinationOverride?: string,
    selectionOverride?: DestinationSelection | null,
  ) {
    const prepared = prepareRouteSubmit(destinationOverride, inputValue, userLocation);
    if (!prepared.ok) {
      if (prepared.error) setErrorText(prepared.error);
      return;
    }
    if (!userLocation) return;

    const destinationSelection =
      selectionOverride === undefined ? selectedDestination : selectionOverride;
    const requestId = routePlanningRequestIdRef.current + 1;
    routePlanningRequestIdRef.current = requestId;
    routePlanningAbortRef.current?.abort();
    const abortController = new AbortController();
    routePlanningAbortRef.current = abortController;

    setErrorText(null);
    setIsLoading(true);
    setPlanningPhase("cancellable");
    setRecommendationText(
      "Checking live arrivals, service alerts, walking time, and transfers.",
    );
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setPlannedRouteSteps([]);

    try {
      await executePlan(
        requestId,
        abortController,
        prepared.destination,
        destinationSelection,
        userLocation,
      );
    } catch (error) {
      if (routePlanningRequestIdRef.current !== requestId) return;
      if (abortController.signal.aborted) return;
      setErrorText(planningErrorText(error));
    } finally {
      if (routePlanningRequestIdRef.current === requestId) {
        setIsLoading(false);
        setPlanningPhase("idle");
        if (routePlanningAbortRef.current === abortController) {
          routePlanningAbortRef.current = null;
        }
      }
    }
  }

  function handleSearchSubmit(
    destinationOverride?: string,
    selectionOverride?: DestinationSelection | null,
  ) {
    setRouteEntryContext("map_search");
    if (selectionOverride) setSelectedDestination(selectionOverride);
    void handleSubmit(destinationOverride, selectionOverride);
  }

  function handleSelectAlternative(candidateId: string) {
    const candidate = routeCandidates.find((item) => item.id === candidateId);
    if (!candidate) return;

    const isSwitch =
      activeRouteCandidateId !== null &&
      candidate.id !== activeRouteCandidateId;
    setActiveRouteCandidateId(candidate.id);
    setPlannedRouteSteps(candidate.steps);

    // Lazily enrich an alternate's intermediate stops the first time it's
    // selected -- the initial trip only enriched the chosen route. Updating the
    // candidate in state re-renders the map via the activeRouteCandidate memo.
    if (candidate.enriched === false && candidate.can_enrich_on_select) {
      enrichRoute(candidate.steps)
        .then((result) => {
          if (!result?.steps?.length) return;
          setRouteCandidates((prev) =>
            prev.map((item) =>
              item.id === candidate.id
                ? {
                    ...item,
                    steps: result.steps,
                    enriched: true,
                    can_enrich_on_select: false,
                  }
                : item,
            ),
          );
        })
        .catch(() => {
          // Keep the un-enriched route shown; stop dots simply won't appear.
        });
    }

    if (!isSwitch) return;
    const line = deriveTransitRouteIds(candidate.steps)[0];
    if (!line) return;
    setSwitchHeadline(`Rerouting via the ${line}.`);
  }

  function handleLoadExternalRoutes(plan: ExternalRoutePlan) {
    const activeCandidate = plan.candidates.find(
      (candidate) => candidate.id === plan.activeCandidateId,
    );
    if (!activeCandidate) return;

    routePlanningRequestIdRef.current += 1;
    routePlanningAbortRef.current?.abort();
    routePlanningAbortRef.current = null;
    setInputValue(plan.destination.label);
    setSelectedDestination(plan.destination);
    setIsLoading(false);
    setPlanningPhase("idle");
    setErrorText(null);
    setRouteCandidates(plan.candidates);
    setActiveRouteCandidateId(activeCandidate.id);
    setPlannedRouteSteps(activeCandidate.steps);
    setRecommendationText(plan.recommendationText);
    // Older restored/deep-linked plans predate the explicit field. Keep that
    // fallback intentional rather than silently treating them as chat routes.
    setRouteEntryContext(plan.entryContext ?? "restored");
    setSwitchHeadline(null);
  }

  function handleCancelRoutePlanning() {
    routePlanningRequestIdRef.current += 1;
    routePlanningAbortRef.current?.abort();
    routePlanningAbortRef.current = null;
    setIsLoading(false);
    setPlanningPhase("idle");
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setPlannedRouteSteps([]);
    setRecommendationText("");
    setSwitchHeadline(null);
    setErrorText(null);
  }

  function handleClearRoute() {
    routePlanningRequestIdRef.current += 1;
    routePlanningAbortRef.current?.abort();
    routePlanningAbortRef.current = null;
    setInputValue("");
    setSelectedDestination(null);
    setIsLoading(false);
    setPlanningPhase("idle");
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setPlannedRouteSteps([]);
    setRecommendationText("");
    setRouteEntryContext("map_search");
    setSwitchHeadline(null);
    setErrorText(null);
  }

  return {
    inputValue, selectedDestination, recommendationText, routeEntryContext, switchHeadline,
    isLoading, planningPhase, errorText, plannedRouteSteps,
    routeCandidates, activeRouteCandidateId,
    handleDestinationInputChange, handleSearchSubmit,
    handleSelectAlternative, handleLoadExternalRoutes,
    handleCancelRoutePlanning, handleClearRoute,
  };
}

function waitForCancellationCheckpoint() {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, 180);
  });
}

export type RoutePlanningController = ReturnType<typeof useRoutePlanningController>;
