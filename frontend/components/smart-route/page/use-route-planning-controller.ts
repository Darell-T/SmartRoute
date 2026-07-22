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

type RoutePlanningControllerInput = {
  userLocation: UserLocation;
};

export type ExternalRoutePlan = {
  destination: DestinationSelection;
  candidates: RouteCandidate[];
  activeCandidateId: string;
  recommendationText: string;
};

export type RoutePlanningPhase = "idle" | "cancellable" | "finalizing";

export function useRoutePlanningController({
  userLocation,
}: RoutePlanningControllerInput) {
  const [inputValue, setInputValue] = useState("");
  const [selectedDestination, setSelectedDestination] =
    useState<DestinationSelection | null>(null);
  const [recommendationText, setRecommendationText] = useState("");
  // Canned line after the user switches to an alternative route;
  // overrides the rail's plan headline until the next trip or clear.
  const [switchHeadline, setSwitchHeadline] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [plannedRouteSteps, setPlannedRouteSteps] = useState<RouteStep[]>([]);
  const [routeCandidates, setRouteCandidates] = useState<RouteCandidate[]>([]);
  const [activeRouteCandidateId, setActiveRouteCandidateId] =
    useState<string | null>(null);
  const [, setSelectedRouteIndex] = useState<number | null>(null);
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
      setSelectedRouteIndex(null);
      setPlannedRouteSteps([]);
      setRecommendationText("");
      setSwitchHeadline(null);
      setErrorText(null);
    }
  }

  async function handleSubmit(
    destinationOverride?: string,
    selectionOverride?: DestinationSelection | null,
  ) {
    const destination = (destinationOverride ?? inputValue).trim();
    if (!destination) return;
    if (!userLocation) {
      setErrorText("Waiting for GPS location...");
      return;
    }

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
    const initialThinkingText =
      "Checking live arrivals, service alerts, walking time, and transfers.";
    setRecommendationText(initialThinkingText);
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setSelectedRouteIndex(null);
    setPlannedRouteSteps([]);

    try {
      await waitForCancellationCheckpoint();
      if (
        routePlanningRequestIdRef.current !== requestId ||
        abortController.signal.aborted
      ) {
        return;
      }
      setPlanningPhase("finalizing");
      const tripData = await planTrip(
        userLocation.lat,
        userLocation.lng,
        destination,
        destinationSelection,
        { signal: abortController.signal },
      );
      if (routePlanningRequestIdRef.current !== requestId) return;

      const {
        candidates: nextCandidates,
        selected: selectedCandidate,
        selectedIndex: nextSelectedIndex,
      } = normalizeTripCandidates(tripData);
      const selectedSteps = selectedCandidate?.steps ?? tripData.route;
      setRouteCandidates(nextCandidates);
      setActiveRouteCandidateId(
        selectedCandidate?.id ?? nextCandidates[0]?.id ?? null,
      );
      setSelectedRouteIndex(nextSelectedIndex);
      setPlannedRouteSteps(selectedSteps);
      setSwitchHeadline(null);
      setRecommendationText(tripData.recommendation);
      if (destinationSelection) setSelectedDestination(destinationSelection);
    } catch (error) {
      if (routePlanningRequestIdRef.current !== requestId) return;
      if (abortController.signal.aborted) return;
      const message = error instanceof Error ? error.message : "Unknown error";
      setErrorText(
        message.includes("Failed to plan trip")
          ? "No route found. Try a more specific address."
          : "Connection error. Check your network and try again.",
      );
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
    setSelectedRouteIndex(candidate.index);
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
    setSelectedRouteIndex(activeCandidate.index);
    setPlannedRouteSteps(activeCandidate.steps);
    setRecommendationText(plan.recommendationText);
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
    setSelectedRouteIndex(null);
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
    setSelectedRouteIndex(null);
    setPlannedRouteSteps([]);
    setRecommendationText("");
    setSwitchHeadline(null);
    setErrorText(null);
  }

  return {
    inputValue, selectedDestination, recommendationText, switchHeadline,
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
