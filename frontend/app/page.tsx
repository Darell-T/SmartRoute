"use client";

import {
  type CSSProperties,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  DestinationSelection,
  FocusedLiveDirection,
  LiveFeedIncident,
  RouteCandidate,
  RouteStep,
  TransitRouteData,
} from "@/types";
import { JarvisMap } from "@/components/jarvis-map";
import { IncidentA11yList } from "@/components/map/incidents/incident-a11y-list";
import {
  liveFeedIncidentToMapIncident,
  type MapIncident,
} from "@/components/map/incidents/incident-marker-types";
import { normalizeIncidentType } from "@/components/map/incidents/incident-marker-tokens";
import { DEFAULT_LOCATION, enrichRoute, getSwitchNarration, getThinking, planTrip } from "@/lib/api";
import {
  buildLiveDirectionRows,
  directionFromVehicle,
  normalizeLiveRouteId,
} from "@/lib/live-directions";
import { useLiveFeed } from "@/lib/use-live-feed";
import { useServiceAlerts } from "@/lib/use-service-alerts";
import {
  deriveTransitRouteIds,
  normalizeTripCandidates,
} from "@/lib/route-planning";
import { summarizeRoute } from "@/lib/smart-route";
import {
  LeftRail,
  type JarvisState,
} from "@/components/smart-route/left-rail";
import { buildLeftRailData } from "@/components/smart-route/left-rail/live-data";

import { DisruptionLegend } from "@/components/smart-route/disruption-legend";
import { MapMiniControls } from "@/components/smart-route/map-mini-controls";
import { useMobileRailSheet } from "@/components/smart-route/page/use-mobile-rail-sheet";

import { type MapActions } from "./page-parts";

export default function JarvisPage() {
  const [inputValue, setInputValue] = useState("");
  const [legendHidden, setLegendHidden] = useState(false);
  const [selectedDestination, setSelectedDestination] =
    useState<DestinationSelection | null>(null);
  const [userLocation, setUserLocation] = useState<{
    lng: number;
    lat: number;
  } | null>(null);
  const [jarvisText, setJarvisText] = useState("");
  const [thinkingText, setThinkingText] = useState("");
  // Canned ATLAS line after the user switches to an alternative route;
  // overrides the rail's plan headline until the next trip or clear.
  const [switchHeadline, setSwitchHeadline] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [plannedRouteSteps, setPlannedRouteSteps] = useState<RouteStep[]>([]);
  const [routeCandidates, setRouteCandidates] = useState<RouteCandidate[]>([]);
  const [activeRouteCandidateId, setActiveRouteCandidateId] =
    useState<string | null>(null);
  const [selectedRouteIndex, setSelectedRouteIndex] = useState<number | null>(
    null,
  );
  const [tripIncidents, setTripIncidents] = useState<LiveFeedIncident[]>([]);
  const [focusedLiveDirection, setFocusedLiveDirection] =
    useState<FocusedLiveDirection | null>(null);
  const [liveRailActivityKey, setLiveRailActivityKey] = useState(0);
  // ATLAS incident scan is OFF by default. It drives a slow, paid Grok + X-search
  // sweep of the half-mile radius, so the rider opts in: flipping it on starts the
  // backend scan and surfaces incidents in the rail and as map markers.
  const [atlasScanOn, setAtlasScanOn] = useState(false);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const routePlanningRequestIdRef = useRef(0);
  const mapActionsRef = useRef<MapActions | null>(null);
  const liveMapFrameRef = useRef<HTMLElement | null>(null);
  const liveArrivalSignatureRef = useRef<string | null>(null);
  // Pre-warmed ATLAS "thinking" clip so the spoken phrase fires the instant
  // route planning starts, with no network/TTS wait on the critical path.
  const thinkingClipRef = useRef<{ text: string; audio: string } | null>(null);
  const {
    mobileRailSheet,
    mobileRailSheetHeight,
    isMobileRailDragging,
    handleMobileRailPointerDown,
    handleMobileRailPointerMove,
    handleMobileRailPointerUp,
    handleMobileRailPointerCancel,
    handleMobileRailKeyDown,
  } = useMobileRailSheet();

  const activeRouteCandidate = useMemo(
    () =>
      routeCandidates.find(
        (candidate) => candidate.id === activeRouteCandidateId,
      ) ?? null,
    [activeRouteCandidateId, routeCandidates],
  );

  const activeRouteSteps = activeRouteCandidate?.steps ?? plannedRouteSteps;

  const routeData = useMemo<TransitRouteData | null>(
    () => (activeRouteSteps.length > 0 ? { steps: activeRouteSteps } : null),
    [activeRouteSteps],
  );

  const summary = useMemo(
    () =>
      activeRouteSteps.length > 0
        ? summarizeRoute(activeRouteSteps, new Date(), activeRouteCandidate?.total_minutes)
        : null,
    [activeRouteSteps, activeRouteCandidate?.total_minutes],
  );

  const destCoords = useMemo(() => {
    const lastStep = activeRouteSteps[activeRouteSteps.length - 1];
    const rawDest =
      lastStep?.type === "WALK" ? lastStep.end_point : lastStep?.arrival_coords;
    if (rawDest) {
      return { lat: rawDest.latitude, lng: rawDest.longitude };
    }
    return selectedDestination?.coordinates ?? null;
  }, [activeRouteSteps, selectedDestination]);

  const activeRouteIds = useMemo(
    () => deriveTransitRouteIds(activeRouteSteps),
    [activeRouteSteps],
  );

  const mapFocusedRouteIds = useMemo(() => {
    if (activeRouteCandidate) return activeRouteIds;
    if (focusedLiveDirection) return [focusedLiveDirection.routeId];
    return [];
  }, [activeRouteCandidate, activeRouteIds, focusedLiveDirection]);

  const liveFeed = useLiveFeed(
    userLocation,
    activeRouteCandidate
      ? activeRouteIds
      : focusedLiveDirection
        ? [focusedLiveDirection.routeId]
        : [],
    atlasScanOn,
  );
  const serviceAlerts = useServiceAlerts();

  // Convert backend `LiveFeedIncident` payloads to the marker-system
  // `MapIncident` shape once. This feeds the map marker bridge and the
  // screen-reader fallback list from the same source.
  const routeAwareIncidents = useMemo<LiveFeedIncident[]>(() => {
    const byId = new Map<string, LiveFeedIncident>();
    for (const incident of liveFeed.incidents ?? []) {
      byId.set(incident.id, incident);
    }
    for (const incident of tripIncidents) {
      byId.set(incident.id, incident);
    }
    return Array.from(byId.values());
  }, [liveFeed.incidents, tripIncidents]);

  const mapIncidents = useMemo<MapIncident[]>(() => {
    return routeAwareIncidents.map((incident) =>
      liveFeedIncidentToMapIncident(incident, normalizeIncidentType),
    );
  }, [routeAwareIncidents]);

  // Only real incidents render on the map -- live-feed + route incidents. The
  // former dev-preview marker (a fake pin near the user that never cleared) is
  // gone.
  const visibleMapIncidents = atlasScanOn ? mapIncidents : [];

  const activeIncidentRouteIds = useMemo(() => {
    const routeIds = new Set<string>();
    for (const incident of visibleMapIncidents) {
      if (!incident.active) continue;
      for (const routeId of incident.routeIds ?? []) {
        if (routeId) routeIds.add(routeId);
      }
    }
    return Array.from(routeIds);
  }, [visibleMapIncidents]);

  const liveDirectionRows = useMemo(
    () => buildLiveDirectionRows(liveFeed.arrivals),
    [liveFeed.arrivals],
  );
  const [clientNowMs, setClientNowMs] = useState(0);

  useEffect(() => {
    setClientNowMs(Date.now());
  }, [liveFeed.clockTick]);

  const leftRailFeed = useMemo(
    () => ({
      nearest_stop: liveFeed.nearestStop,
      stops: liveFeed.stops,
      arrivals: liveFeed.arrivals,
      alerts: liveFeed.alerts,
      vehicles: liveFeed.vehicles,
      summary: liveFeed.summary,
      signals: liveFeed.signals,
      incidents: routeAwareIncidents,
      updated_at: liveFeed.updatedAt ?? undefined,
      degraded: liveFeed.degraded,
      debug: liveFeed.debug ?? undefined,
    }),
    [
      liveFeed.nearestStop,
      liveFeed.stops,
      liveFeed.arrivals,
      liveFeed.alerts,
      liveFeed.vehicles,
      liveFeed.summary,
      liveFeed.signals,
      routeAwareIncidents,
      liveFeed.updatedAt,
      liveFeed.degraded,
      liveFeed.debug,
    ],
  );

  const leftRailData = useMemo(
    () =>
      buildLeftRailData({
        liveFeed: leftRailFeed,
        routeSteps: activeRouteSteps,
        routeCandidates,
        activeRouteCandidate,
        switchHeadline,
        recommendationText: jarvisText,
        routeEta: summary?.arriveLabel ?? null,
        routeTotalTime: summary ? `${summary.totalMin} min` : null,
        serviceAlerts: serviceAlerts.alerts,
        incidents: routeAwareIncidents,
        nowMs: clientNowMs || 0,
      }),
    [
      leftRailFeed,
      activeRouteSteps,
      routeCandidates,
      activeRouteCandidate,
      switchHeadline,
      jarvisText,
      summary,
      serviceAlerts.alerts,
      routeAwareIncidents,
      liveFeed.clockTick,
      clientNowMs,
    ],
  );

  const leftRailDisplayData = useMemo(
    () => ({
      ...leftRailData,
      incidents: routeAwareIncidents,
    }),
    [leftRailData, routeAwareIncidents],
  );

  const liveArrivalSignature = useMemo(
    () =>
      liveDirectionRows
        .map((row) =>
          [
            row.key,
            ...row.arrivals.slice(0, 5).map((arrival) =>
              [
                arrival.trip_id ?? arrival.stop_id ?? arrival.terminal_stop_id ?? "arrival",
                arrival.arrival_time,
                arrival.delay ?? 0,
              ].join(":"),
            ),
          ].join("|"),
        )
        .join("||"),
    [liveDirectionRows],
  );

  const pulseLiveRail = useCallback(() => {
    setLiveRailActivityKey((key) => key + 1);
  }, []);

  useEffect(() => {
    if (!liveArrivalSignature) return;
    if (liveArrivalSignatureRef.current === null) {
      liveArrivalSignatureRef.current = liveArrivalSignature;
      return;
    }
    if (liveArrivalSignatureRef.current === liveArrivalSignature) return;
    liveArrivalSignatureRef.current = liveArrivalSignature;
    pulseLiveRail();
  }, [liveArrivalSignature, pulseLiveRail]);

  const visibleVehicles = useMemo(() => {
    if (activeRouteCandidate) {
      // While a planned route is displayed, live vehicles for its lines
      // bloat the map (every train on those lines citywide) and drag the
      // frame rate. The route + its stops are the focus; live trains
      // return when the route is cleared.
      return [];
    }
    if (!focusedLiveDirection) return [];
    const byRoute = liveFeed.vehicles.filter(
      (vehicle) =>
        normalizeLiveRouteId(vehicle.route_id) === focusedLiveDirection.routeId,
    );
    const sameDirection = byRoute.filter(
      (vehicle) =>
        directionFromVehicle(vehicle) === focusedLiveDirection.direction,
    );
    if (sameDirection.length > 0) return sameDirection;
    return byRoute.filter(
      (vehicle) => directionFromVehicle(vehicle) === "UNKNOWN",
    );
  }, [activeRouteCandidate, focusedLiveDirection, liveFeed.vehicles]);

  // ── SmartRoute Left Rail state machine ───────────────────────────────────
  // The new rail consumes a four-value ATLAS state. We derive it from the
  // existing app signals so the rail stays in lockstep with the recommendation
  // pipeline (loading → active → idle/error).
  const jarvisState: JarvisState = isLoading
    ? "thinking"
    : errorText
      ? "error"
      : activeRouteCandidate
        ? "result"
        : "standby";

  useEffect(() => {
    if (!navigator.geolocation) {
      setUserLocation(DEFAULT_LOCATION);
      return;
    }

    let resolved = false;
    const timeoutId = setTimeout(() => {
      if (!resolved)
        setUserLocation((previous) => previous ?? DEFAULT_LOCATION);
    }, 8_000);

    navigator.geolocation.getCurrentPosition(
      (position) => {
        resolved = true;
        clearTimeout(timeoutId);
        setUserLocation({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
        });
      },
      () => {
        resolved = true;
        clearTimeout(timeoutId);
        setUserLocation(DEFAULT_LOCATION);
      },
      // This first fix only gates the live feed (nearest stops), where
      // city-block accuracy is plenty -- so use a fast NETWORK fix instead of
      // waiting up to 5s for GPS. The precise dot is refined separately by the
      // high-accuracy watchPosition in jarvis-map. A recent cached fix is
      // accepted instantly (maximumAge) so arrivals can appear right away.
      { enableHighAccuracy: false, timeout: 6_000, maximumAge: 60_000 },
    );

    return () => clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    if (!focusedLiveDirection) return;
    const stillExists = liveDirectionRows.some(
      (row) =>
        row.routeId === focusedLiveDirection.routeId &&
        row.direction === focusedLiveDirection.direction &&
        row.terminalKey === focusedLiveDirection.terminalKey,
    );
    if (!stillExists) setFocusedLiveDirection(null);
  }, [focusedLiveDirection, liveDirectionRows]);

  // Warm the first thinking clip on load so the very first plan plays its
  // spoken phrase immediately rather than after a cold TTS round-trip.
  useEffect(() => {
    void prewarmThinking();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
      }
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current);
      }
    };
  }, []);

  const handleLocationUpdate = useCallback(
    (coords: { lng: number; lat: number }) => {
      setUserLocation(coords);
    },
    [],
  );

  const handleDestinationInputChange = useCallback((value: string) => {
    setInputValue(value);
    setSelectedDestination(null);
  }, []);

  const handleMapReady = useCallback((actions: MapActions) => {
    mapActionsRef.current = actions;
  }, []);

  const handleSelectIncident = useCallback(
    (incident: LiveFeedIncident) => {
      const mapIncident = liveFeedIncidentToMapIncident(
        incident,
        normalizeIncidentType,
      );
      setAtlasScanOn(true);
      mapActionsRef.current?.focusIncident(mapIncident);
    },
    [],
  );

  function handleVoiceInput() {
    const win = window as unknown as {
      SpeechRecognition?: new () => {
        lang: string;
        onstart: () => void;
        onresult: (event: {
          results: {
            [index: number]: { [index: number]: { transcript: string } };
          };
        }) => void;
        onend: () => void;
        onerror: () => void;
        start: () => void;
      };
      webkitSpeechRecognition?: new () => {
        lang: string;
        onstart: () => void;
        onresult: (event: {
          results: {
            [index: number]: { [index: number]: { transcript: string } };
          };
        }) => void;
        onend: () => void;
        onerror: () => void;
        start: () => void;
      };
    };
    const RecognitionCtor =
      win.SpeechRecognition || win.webkitSpeechRecognition;
    if (!RecognitionCtor) return;
    const recognition = new RecognitionCtor();
    recognition.lang = "en-US";
    recognition.onstart = () => setIsListening(true);
    recognition.onresult = (event) => {
      setInputValue(event.results[0][0].transcript);
      setSelectedDestination(null);
    };
    recognition.onend = () => setIsListening(false);
    recognition.onerror = () => setIsListening(false);
    recognition.start();
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

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }

    setErrorText(null);
    setIsLoading(true);
    setIsSpeaking(false);
    const initialThinkingText = "Scanning live feeds, alerts, and route options...";
    setThinkingText(initialThinkingText);
    setJarvisText(initialThinkingText);
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setSelectedRouteIndex(null);
    setPlannedRouteSteps([]);
    setTripIncidents([]);

    let tripSettled = false;
    // Play ATLAS's thinking line the instant the state flips to "thinking".
    // The clip is pre-warmed (on mount and after each plan), so there is no
    // network wait on the critical path. Cold start (ref not yet filled)
    // falls back to fetching on demand.
    const warmClip = thinkingClipRef.current;
    if (warmClip?.audio) {
      thinkingClipRef.current = null;
      if (warmClip.text) {
        setThinkingText(warmClip.text);
        setJarvisText(warmClip.text);
      }
      playNarrationAudio(warmClip.audio);
      void prewarmThinking();
    } else {
      getThinking()
        .then((thinking) => {
          if (routePlanningRequestIdRef.current !== requestId || tripSettled) return;
          if (thinking?.text) {
            setThinkingText(thinking.text);
            setJarvisText(thinking.text);
          }
          if (thinking?.audio) {
            playNarrationAudio(thinking.audio);
          }
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
      setActiveRouteCandidateId(selectedCandidate?.id ?? nextCandidates[0]?.id ?? null);
      setSelectedRouteIndex(nextSelectedIndex);
      setPlannedRouteSteps(selectedSteps);
      setTripIncidents(tripData.incidents ?? []);
      setSwitchHeadline(null);
      setJarvisText(tripData.recommendation);
      if (destinationSelection) {
        setSelectedDestination(destinationSelection);
      }

      if (tripData.audio) {
        playNarrationAudio(tripData.audio);
      }
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
      if (routePlanningRequestIdRef.current === requestId) {
        setIsLoading(false);
      }
    }
  }

  /** Fetch one ATLAS thinking clip (text + cached audio) into the ref so the
   *  next plan can play it immediately. Fire-and-forget; silent on failure. */
  function prewarmThinking() {
    return getThinking()
      .then((thinking) => {
        if (thinking?.audio) {
          thinkingClipRef.current = { text: thinking.text ?? "", audio: thinking.audio };
        }
      })
      .catch(() => {});
  }

  /** Decode base64 MP3 and play it through the shared audio ref, driving
   *  isSpeaking. Pauses/revokes any narration already playing. */
  function playNarrationAudio(audioB64: string) {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    const bytes = Uint8Array.from(atob(audioB64), (char) => char.charCodeAt(0));
    const nextAudioUrl = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
    const routeAudio = new Audio(nextAudioUrl);
    audioUrlRef.current = nextAudioUrl;
    audioRef.current = routeAudio;
    setIsSpeaking(true);
    routeAudio.onended = () => {
      setIsSpeaking(false);
      audioRef.current = null;
    };
    routeAudio.onerror = () => {
      setIsSpeaking(false);
      audioRef.current = null;
    };
    routeAudio.play().catch(() => {
      setIsSpeaking(false);
    });
  }

  function handleSelectRouteCandidate(candidate: RouteCandidate) {
    const isSwitch =
      activeRouteCandidateId !== null && candidate.id !== activeRouteCandidateId;
    setActiveRouteCandidateId(candidate.id);
    setSelectedRouteIndex(candidate.index);
    setPlannedRouteSteps(candidate.steps);
    setFocusedLiveDirection(null);
    pulseLiveRail();

    // Lazily enrich an alternate's intermediate stops the first time it's
    // selected -- the initial trip only enriched the chosen route. Updating the
    // candidate in state re-renders the map via the activeRouteCandidate memo.
    if (candidate.enriched === false && candidate.can_enrich_on_select) {
      enrichRoute(candidate.steps)
        .then((result) => {
          if (!result?.steps?.length) return;
          setRouteCandidates((prev) =>
            prev.map((c) =>
              c.id === candidate.id
                ? { ...c, steps: result.steps, enriched: true, can_enrich_on_select: false }
                : c,
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
    setSwitchHeadline(`Rerouting via the ${line}, sir.`);
    getSwitchNarration(line)
      .then((narration) => {
        setSwitchHeadline(narration.text);
        if (narration.audio) playNarrationAudio(narration.audio);
      })
      .catch(() => {
        // Local text already showing.
      });
  }

  function handleClearRoute() {
    routePlanningRequestIdRef.current += 1;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    setInputValue("");
    setSelectedDestination(null);
    setRouteCandidates([]);
    setActiveRouteCandidateId(null);
    setSelectedRouteIndex(null);
    setPlannedRouteSteps([]);
    setTripIncidents([]);
    setJarvisText("");
    setThinkingText("");
    setSwitchHeadline(null);
    setErrorText(null);
    setIsSpeaking(false);
    setFocusedLiveDirection(null);
    pulseLiveRail();
  }

  async function toggleFullscreen(target: HTMLElement | null) {
    if (!target || typeof document === "undefined") return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
        return;
      }
      await target.requestFullscreen();
    } catch {
      mapActionsRef.current?.recenter();
    }
  }

  const liveRailShellStyle = {
    position: "absolute",
    top: 14,
    left: 14,
    bottom: 14,
    width: 400,
    zIndex: 20,
    padding: 0,
    border: "none",
    background: "transparent",
    borderRadius: 26,
    overflow: "hidden",
    display: "flex",
    "--sr-mobile-sheet-height": mobileRailSheetHeight,
  } as CSSProperties;

  const liveWorkspace = (
    <div
      className="sr-live-console"
      // Liquid-glass layout: the map runs full-bleed and the rail FLOATS
      // over it as a detached glass panel — the basemap showing through
      // the backdrop blur is what makes the glass read. Single column;
      // the aside is absolutely positioned with an inset margin.
      style={{ gridTemplateColumns: "minmax(0, 1fr)", position: "relative" }}
    >
      {/* SmartRoute Left Rail — agent-first surface that hosts Route / Hub /
          Alerts. The ATLAS state bridges from the existing pipeline
          signals (isLoading / activeRouteCandidate / errorText) so the rail
          stays in lockstep with the rest of the app. */}
      <aside
        className="sr-live-left-rail-shell"
        data-mobile-sheet-state={mobileRailSheet}
        data-mobile-sheet-dragging={isMobileRailDragging ? "true" : undefined}
        aria-label="SmartRoute Left Rail"
        style={liveRailShellStyle}
      >
        <button
          type="button"
          className="sr-mobile-rail-grip"
          aria-label="Resize route panel"
          aria-expanded={mobileRailSheet !== "hidden"}
          onPointerDown={handleMobileRailPointerDown}
          onPointerMove={handleMobileRailPointerMove}
          onPointerUp={handleMobileRailPointerUp}
          onPointerCancel={handleMobileRailPointerCancel}
          onKeyDown={handleMobileRailKeyDown}
        >
          <span aria-hidden="true" />
        </button>
        <div className="sr-mobile-rail-body">
          <LeftRail
            width={400}
            jarvisState={jarvisState}
            isSpeaking={isSpeaking}
            thinkingText={thinkingText}
            data={leftRailDisplayData}
            atlasScanOn={atlasScanOn}
            onAtlasScanToggle={() => setAtlasScanOn((value) => !value)}
            onSelectIncident={handleSelectIncident}
            onSelectAlternative={(candidateId) => {
              const candidate = routeCandidates.find(
                (c) => c.id === candidateId,
              );
              if (candidate) handleSelectRouteCandidate(candidate);
            }}
            search={{
              inputValue,
              isLoading,
              isListening,
              hasActiveRoute: Boolean(summary),
              onInputChange: handleDestinationInputChange,
              onSubmit: (destination, selection) => {
                if (selection) setSelectedDestination(selection);
                void handleSubmit(destination, selection);
              },
              onVoiceInput: handleVoiceInput,
              onClear: handleClearRoute,
            }}
          />
        </div>
      </aside>

      <section
        ref={liveMapFrameRef}
        className="sr-shell-canvas sr-shell-canvas--map sr-live-console__map"
      >
        <div className="absolute inset-0">
          <JarvisMap
            onLocationUpdate={handleLocationUpdate}
            mode="liveFeed"
            routeData={routeData}
            destCoords={destCoords}
            isSpeaking={isSpeaking}
            vehicles={visibleVehicles}
            focusedRouteIds={mapFocusedRouteIds}
            incidentRouteIds={activeIncidentRouteIds}
            incidents={visibleMapIncidents}
            liveVehicleScopeKey={
              activeRouteCandidate
                ? `mission:${activeRouteCandidate.id}:${activeRouteIds.join(",")}`
                : focusedLiveDirection
                ? `${focusedLiveDirection.routeId}:${focusedLiveDirection.direction}:${focusedLiveDirection.terminalKey}`
                : "live:none"
            }
            onMapReady={handleMapReady}
          />
        </div>

        <div className="sr-map-vignette" aria-hidden="true" />
        {/* Search moved into the left rail's WHERE TO box — the floating
            map overlay competed with it for the same job. */}
        <MapMiniControls
          onExpand={() => void toggleFullscreen(liveMapFrameRef.current)}
          onRecenter={() => mapActionsRef.current?.recenter()}
        />
        {legendHidden ? (
          <button
            type="button"
            className="sr-disruption-legend__restore"
            onClick={() => setLegendHidden(false)}
            aria-label="Show map key"
          >
            Map key
          </button>
        ) : (
          <DisruptionLegend
            variant="map"
            onHide={() => setLegendHidden(true)}
          />
        )}
        {/* Hidden screen-reader mirror of the canvas-rendered incident
            markers. deck.gl IconLayer paints to <canvas> and so does not
            participate in the accessibility tree — this list bridges that
            gap so assistive tech users get the same incident inventory. */}
        <IncidentA11yList incidents={visibleMapIncidents} />
      </section>
    </div>
  );

  return (
    <div
      className="sr-app-shell"
      data-active-tab="livemap"
      style={{
        // Single full-viewport row: 400px LeftRail | 1fr Map. The rail owns
        // Route / Hub / Alerts, while the map carries its own overlays.
        height: "100dvh",
        width: "100vw",
        display: "flex",
        flexDirection: "row",
        overflow: "hidden",
      }}
    >
      {liveWorkspace}
    </div>
  );
}

