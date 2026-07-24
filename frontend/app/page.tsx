"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MotionConfig } from "motion/react";
import type { TransitRouteData } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import { useLiveFeed } from "@/lib/use-live-feed";
import { useServiceAlerts } from "@/lib/use-service-alerts";
import { deriveTransitRouteIds } from "@/lib/route-planning";
import { summarizeRoute } from "@/lib/smart-route";
import { useAgentChat, type ArrivalsTurnPayload } from "@/lib/use-agent-chat";
import {
  SmartRouteThemeProvider,
  useSmartRouteTheme,
} from "@/lib/use-chat-theme";
import type { RouteCard } from "@/lib/agent-chat-stream";
import {
  agentRoutePlanFromCards,
  normalizeRouteCoordinate,
} from "@/lib/agent-route-selection";
import { type RouteRailStatus } from "@/components/smart-route/left-rail";
import { buildLeftRailData } from "@/components/smart-route/left-rail/live-data";
import { ChatPanel } from "@/components/smart-route/chat/chat-panel";
import { ChatSidebar } from "@/components/smart-route/chat/chat-sidebar";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import {
  buildArrivalsPayloadForRoute,
  deriveNearbyRouteIds,
  stationNameForRoute,
} from "@/components/smart-route/chat/near-you";

import { LiveWorkspace } from "@/components/smart-route/page/live-workspace";
import { useMobileRailSheet } from "@/components/smart-route/page/use-mobile-rail-sheet";
import { useRoutePlanningController } from "@/components/smart-route/page/use-route-planning-controller";

import { type AppTab, type MapActions } from "./page-parts";

export default function SmartRoutePage() {
  return (
    <SmartRouteThemeProvider>
      <SmartRoutePageContent />
    </SmartRouteThemeProvider>
  );
}

function SmartRoutePageContent() {
  const [userLocation, setUserLocation] = useState<{
    lng: number;
    lat: number;
  } | null>(null);
  const [activeTab, setActiveTab] = useState<AppTab>("chat");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [newTripKey, setNewTripKey] = useState(0);
  const mapActionsRef = useRef<MapActions | null>(null);
  const liveMapFrameRef = useRef<HTMLElement | null>(null);
  const mobileRail = useMobileRailSheet();

  const routePlanning = useRoutePlanningController({
    userLocation,
  });
  const {
    selectedDestination,
    recommendationText,
    routeEntryContext,
    switchHeadline,
    isLoading,
    errorText,
    plannedRouteSteps,
    routeCandidates,
    activeRouteCandidateId,
  } = routePlanning;

  const activeRouteCandidate = useMemo(
    () =>
      routeCandidates.find(
        (candidate) => candidate.id === activeRouteCandidateId,
      ) ?? null,
    [activeRouteCandidateId, routeCandidates],
  );

  const activeRouteSteps = activeRouteCandidate?.steps ?? plannedRouteSteps;

  const routeData = useMemo<TransitRouteData | null>(() => {
    return activeRouteSteps.length > 0 ? { steps: activeRouteSteps } : null;
  }, [activeRouteSteps]);

  const summary = useMemo(
    () =>
      activeRouteSteps.length > 0
        ? summarizeRoute(
            activeRouteSteps,
            new Date(),
            activeRouteCandidate?.total_minutes,
            {
              arrivalAtIso: activeRouteCandidate?.arrival_at,
              transfers: activeRouteCandidate?.score_breakdown?.transfers,
            },
          )
        : null,
    [
      activeRouteSteps,
      activeRouteCandidate?.total_minutes,
      activeRouteCandidate?.arrival_at,
      activeRouteCandidate?.score_breakdown?.transfers,
    ],
  );

  const destCoords = useMemo(() => {
    const lastStep = activeRouteSteps[activeRouteSteps.length - 1];
    const rawDest =
      lastStep?.type === "WALK" ? lastStep.end_point : lastStep?.arrival_coords;
    const stepDestination = normalizeRouteCoordinate(rawDest);
    if (stepDestination) return stepDestination;
    return selectedDestination?.coordinates ?? null;
  }, [activeRouteSteps, selectedDestination]);

  const activeRouteIds = useMemo(
    () => deriveTransitRouteIds(activeRouteSteps),
    [activeRouteSteps],
  );

  const liveFeed = useLiveFeed(
    userLocation,
    activeRouteCandidate ? activeRouteIds : [],
  );
  const serviceAlerts = useServiceAlerts();

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
      signals: liveFeed.signals,
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
      liveFeed.signals,
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
        recommendationText,
        routeEntryContext,
        routeEta: summary?.arriveLabel ?? null,
        routeTotalTime: summary ? `${summary.totalMin} min` : null,
        serviceAlerts: serviceAlerts.alerts,
        incidents: [],
        nowMs: clientNowMs || 0,
      }),
    [
      leftRailFeed,
      activeRouteSteps,
      routeCandidates,
      activeRouteCandidate,
      switchHeadline,
      recommendationText,
      routeEntryContext,
      summary,
      serviceAlerts.alerts,
      clientNowMs,
    ],
  );

  // ── SmartRoute Left Rail status ──────────────────────────────────────────
  // The rail consumes a four-value route status. We derive it from the
  // existing app signals so the rail stays in lockstep with the recommendation
  // pipeline (loading -> active -> idle/error).
  const routeStatus: RouteRailStatus = isLoading
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
      // high-accuracy watchPosition in SmartRouteMap. A recent cached fix is
      // accepted instantly (maximumAge) so arrivals can appear right away.
      { enableHighAccuracy: false, timeout: 6_000, maximumAge: 60_000 },
    );

    return () => clearTimeout(timeoutId);
  }, []);

  const handleLocationUpdate = useCallback(
    (coords: { lng: number; lat: number }) => {
      setUserLocation(coords);
    },
    [],
  );

  const handleMapReady = useCallback((actions: MapActions) => {
    mapActionsRef.current = actions;
  }, []);

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

  const chat = useAgentChat({ getOrigin: () => userLocation });
  const { theme, toggleTheme } = useSmartRouteTheme();

  const nearbyRouteIds = useMemo(
    () =>
      deriveNearbyRouteIds(leftRailData).filter((routeId) =>
        SUBWAY_BULLET_ROUTES.has(routeId),
      ),
    [leftRailData],
  );

  const openLiveMap = useCallback(() => setActiveTab("livemap"), []);
  const openChat = useCallback(() => setActiveTab("chat"), []);

  const startNewTrip = useCallback(() => {
    chat.reset();
    routePlanning.handleClearRoute();
    setNewTripKey((key) => key + 1);
    setActiveTab("chat");
  }, [chat, routePlanning]);

  const handleSelectNearbyLine = useCallback(
    (routeId: string) => {
      const normalizedRouteId = routeId.toUpperCase();
      const nearbyGroup = leftRailData.nearbyTransitGroups.find((group) =>
        group.routeIds.some((id) => id.toUpperCase() === normalizedRouteId),
      );
      const stationName = stationNameForRoute(
        normalizedRouteId,
        leftRailData.nearbyTransitGroups,
        liveFeed.nearestStop?.stop_name ?? "Nearest station",
      );
      const stop =
        liveFeed.stops.find(
          (candidate) =>
            candidate.stop_name === stationName &&
            candidate.route_ids.some((id) => id.toUpperCase() === normalizedRouteId),
        ) ??
        liveFeed.stops.find((candidate) =>
          candidate.route_ids.some((id) => id.toUpperCase() === normalizedRouteId),
        ) ??
        liveFeed.nearestStop;
      const stationCoordinates =
        typeof stop?.stop_lat === "number" && typeof stop.stop_lon === "number"
          ? { lat: stop.stop_lat, lng: stop.stop_lon }
          : undefined;
      const distanceMiles =
        nearbyGroup?.distanceMiles ??
        (typeof stop?.distance_m === "number" ? stop.distance_m / 1609.344 : undefined);
      const walkMinutes =
        nearbyGroup?.walkMinutes ??
        (typeof stop?.distance_m === "number"
          ? Math.max(1, Math.round(stop.distance_m / 80))
          : undefined);

      const arrivals = buildArrivalsPayloadForRoute(
        normalizedRouteId,
        leftRailData.arrivals,
        stationName,
        { walkMinutes, distanceMiles, coordinates: stationCoordinates },
      );
      chat.appendLocalTurn({
        text: `Here are the next ${normalizedRouteId} trains at ${stationName}.`,
        arrivals,
      });
      setActiveTab("chat");
    },
    [chat, leftRailData, liveFeed.nearestStop, liveFeed.stops],
  );

  const handleOpenNearbyStation = useCallback(
    (arrivals: ArrivalsTurnPayload) => {
      if (!arrivals.stationCoordinates) {
        setActiveTab("livemap");
        return;
      }
      routePlanning.handleSearchSubmit(`${arrivals.stationName} station`, {
        label: `${arrivals.stationName} station`,
        coordinates: arrivals.stationCoordinates,
      });
      setActiveTab("livemap");
    },
    [routePlanning],
  );

  // Card tap -> the same route model used by manual planning. This lets the
  // map, detailed rail steps, and alternatives stay synchronized while chat
  // remains mounted for follow-up questions.
  const handleSelectRouteCard = useCallback(
    (card: RouteCard) => {
      const sourceTurn = [...chat.messages]
        .reverse()
        .find(
          (turn) =>
            turn.role === "assistant" &&
            turn.routeCards.some((candidate) => candidate.card_id === card.card_id),
        );
      const plan = agentRoutePlanFromCards(
        sourceTurn?.role === "assistant" ? sourceTurn.routeCards : [card],
        card.card_id,
      );
      if (!plan) return;
      routePlanning.handleLoadExternalRoutes(plan);
      setActiveTab("livemap");
    },
    [chat.messages, routePlanning],
  );

  const isLivemapTab = activeTab === "livemap";

  return (
    <MotionConfig reducedMotion="user">
      <div
        className="sr-tab-shell"
        data-tab={activeTab}
        data-sr-theme={theme}
        data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
      >
        <ChatSidebar
          activeTab={activeTab}
          collapsed={sidebarCollapsed}
          theme={theme}
          onOpenChat={openChat}
          onOpenLiveMap={openLiveMap}
          onNewTrip={startNewTrip}
          nearbyRouteIds={nearbyRouteIds}
          onSelectNearbyLine={handleSelectNearbyLine}
          onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
          onToggleTheme={toggleTheme}
        />

        <div
          className={`sr-app-shell sr-tab-shell__panel sr-tab-shell__panel--livemap${
            isLivemapTab ? "" : " sr-tab-shell__panel--hidden"
          }`}
          data-active-tab="livemap"
          inert={isLivemapTab ? undefined : true}
          style={{
            // Single full-viewport row: 400px LeftRail | 1fr Map. The rail owns
            // Route / Alerts, while the map carries its own overlays.
            display: "flex",
            flexDirection: "row",
            overflow: "hidden",
          }}
        >
          <LiveWorkspace
            mobileRail={mobileRail}
            routePlanning={routePlanning}
            leftRailData={leftRailData}
            routeStatus={routeStatus}
            hasActiveRoute={Boolean(summary)}
            liveMap={{
              frameRef: liveMapFrameRef,
              routeData,
              destCoords,
              onLocationUpdate: handleLocationUpdate,
              onMapReady: handleMapReady,
              onExpand: () => void toggleFullscreen(liveMapFrameRef.current),
              onRecenter: () => mapActionsRef.current?.recenter(),
            }}
          />
        </div>

        <div
          className={`sr-chat-tab sr-tab-shell__panel sr-tab-shell__panel--chat${
            isLivemapTab ? " sr-tab-shell__panel--hidden" : ""
          }`}
          data-sr-theme={theme}
          inert={isLivemapTab ? true : undefined}
        >
          <ChatPanel
            key={newTripKey}
            chat={chat}
            theme={theme}
            onOpenLiveMap={openLiveMap}
            onSelectRouteCard={handleSelectRouteCard}
            onOpenNearbyStation={handleOpenNearbyStation}
          />
        </div>
      </div>
    </MotionConfig>
  );
}

