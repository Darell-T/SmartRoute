"use client";

import { useEffect, useRef, useState } from "react";
import type {
  DestinationSelection,
  LiveFeedIncident,
  RouteCandidate,
  RouteStep,
} from "@/types";
import {
  enrichRoute,
  getSwitchNarration,
  getThinking,
  planTrip,
} from "@/lib/api";
import {
  deriveTransitRouteIds,
  normalizeTripCandidates,
} from "@/lib/route-planning";

type UserLocation = { lng: number; lat: number } | null;

type RoutePlanningControllerInput = {
  userLocation: UserLocation;
};

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
  const [tripIncidents, setTripIncidents] = useState<LiveFeedIncident[]>([]);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const routePlanningRequestIdRef = useRef(0);
  // Pre-warmed route-planning clip so the spoken phrase fires the instant
  // route planning starts, with no network/TTS wait on the critical path.
  const thinkingClipRef = useRef<{ text: string; audio: string } | null>(null);

  useEffect(() => {
    void prewarmThinking();
    return () => {
      if (audioRef.current) audioRef.current.pause();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    };
  }, []);

  function releaseNarrationAudio() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  }

  function stopNarration() {
    releaseNarrationAudio();
  }

  function handleDestinationInputChange(value: string) {
    setInputValue(value);
    setSelectedDestination(null);
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

    stopNarration();
    setErrorText(null);
    setIsLoading(true);
    const initialThinkingText =
      "Checking live arrivals, service alerts, walking time, and transfers.";
    setRecommendationText(initialThinkingText);
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setSelectedRouteIndex(null);
    setPlannedRouteSteps([]);
    setTripIncidents([]);

    let tripSettled = false;
    // Play the route-planning line the instant the state flips to "thinking".
    // The clip is pre-warmed (on mount and after each plan), so there is no
    // network wait on the critical path. Cold start (ref not yet filled)
    // falls back to fetching on demand.
    const warmClip = thinkingClipRef.current;
    if (warmClip?.audio) {
      thinkingClipRef.current = null;
      if (warmClip.text) {
        setRecommendationText(warmClip.text);
      }
      playNarrationAudio(warmClip.audio);
      void prewarmThinking();
    } else {
      getThinking()
        .then((thinking) => {
          if (routePlanningRequestIdRef.current !== requestId || tripSettled)
            return;
          if (thinking?.text) {
            setRecommendationText(thinking.text);
          }
          if (thinking?.audio) playNarrationAudio(thinking.audio);
        })
        .catch(() => {});
      void prewarmThinking();
    }

    try {
      const tripData = await planTrip(
        userLocation.lat,
        userLocation.lng,
        destination,
        destinationSelection,
      );
      tripSettled = true;
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
      setTripIncidents(tripData.incidents ?? []);
      setSwitchHeadline(null);
      setRecommendationText(tripData.recommendation);
      if (destinationSelection) setSelectedDestination(destinationSelection);
      if (tripData.audio) playNarrationAudio(tripData.audio);
    } catch (error) {
      tripSettled = true;
      if (routePlanningRequestIdRef.current !== requestId) return;
      const message = error instanceof Error ? error.message : "Unknown error";
      setErrorText(
        message.includes("Failed to plan trip")
          ? "No route found. Try a more specific address."
          : "Connection error. Check your network and try again.",
      );
    } finally {
      if (routePlanningRequestIdRef.current === requestId) setIsLoading(false);
    }
  }

  function handleSearchSubmit(
    destinationOverride?: string,
    selectionOverride?: DestinationSelection | null,
  ) {
    if (selectionOverride) setSelectedDestination(selectionOverride);
    void handleSubmit(destinationOverride, selectionOverride);
  }

  /** Fetch one route-planning clip (text + cached audio) into the ref so the
   *  next plan can play it immediately. Fire-and-forget; silent on failure. */
  function prewarmThinking() {
    return getThinking()
      .then((thinking) => {
        if (thinking?.audio) {
          thinkingClipRef.current = {
            text: thinking.text ?? "",
            audio: thinking.audio,
          };
        }
      })
      .catch(() => {});
  }

  /** Decode base64 MP3 and play it through the shared audio ref. */
  function playNarrationAudio(audioB64: string) {
    releaseNarrationAudio();
    const bytes = Uint8Array.from(atob(audioB64), (char) =>
      char.charCodeAt(0),
    );
    const nextAudioUrl = URL.createObjectURL(
      new Blob([bytes], { type: "audio/mpeg" }),
    );
    const routeAudio = new Audio(nextAudioUrl);
    audioUrlRef.current = nextAudioUrl;
    audioRef.current = routeAudio;
    routeAudio.onended = () => {
      URL.revokeObjectURL(nextAudioUrl);
      if (audioUrlRef.current === nextAudioUrl) audioUrlRef.current = null;
      audioRef.current = null;
    };
    routeAudio.onerror = () => {
      URL.revokeObjectURL(nextAudioUrl);
      if (audioUrlRef.current === nextAudioUrl) audioUrlRef.current = null;
      audioRef.current = null;
    };
    routeAudio.play().catch(() => {
      URL.revokeObjectURL(nextAudioUrl);
      if (audioUrlRef.current === nextAudioUrl) audioUrlRef.current = null;
      audioRef.current = null;
    });
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
    // Show the local line immediately; the server's canned phrase (cached
    // per line) replaces it and brings audio when TTS is available.
    setSwitchHeadline(`Rerouting via the ${line}.`);
    getSwitchNarration(line)
      .then((narration) => {
        setSwitchHeadline(publicRouteNarration(narration.text));
        if (narration.audio) playNarrationAudio(narration.audio);
      })
      .catch(() => {
        // Local text already showing.
      });
  }

  function handleClearRoute() {
    routePlanningRequestIdRef.current += 1;
    stopNarration();
    setInputValue("");
    setSelectedDestination(null);
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setSelectedRouteIndex(null);
    setPlannedRouteSteps([]);
    setTripIncidents([]);
    setRecommendationText("");
    setSwitchHeadline(null);
    setErrorText(null);
  }

  return {
    inputValue, selectedDestination, recommendationText, switchHeadline,
    isLoading, errorText, plannedRouteSteps,
    routeCandidates, activeRouteCandidateId, tripIncidents,
    handleDestinationInputChange, handleSearchSubmit,
    handleSelectAlternative, handleClearRoute,
  };
}

function publicRouteNarration(text: string) {
  return text
    .replace(/\bATLAS\b/gi, "SmartRoute")
    .replace(/\bVery well,\s*/i, "")
    .replace(/,\s*sir\.?/i, ".")
    .replace(/\bsir\.?\s*/i, "")
    .trim();
}

export type RoutePlanningController = ReturnType<typeof useRoutePlanningController>;
