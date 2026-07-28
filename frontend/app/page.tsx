"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MotionConfig } from "motion/react";
import type { TransitRouteData } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import { requestInitialLocation } from "@/lib/initial-geolocation";
import { useLiveFeed } from "@/lib/use-live-feed";
import { useServiceAlerts } from "@/lib/use-service-alerts";
import { deriveTransitRouteIds } from "@/lib/route-planning";
import { formatCanonicalRouteSummary } from "@/lib/smart-route";
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
    return activeRouteSteps.length > 0
      ? { steps: activeRouteSteps, itinerary: activeRouteCandidate?.itinerary }
      : null;
  }, [activeRouteSteps, activeRouteCandidate?.itinerary]);

  const summary = useMemo(
    () => formatCanonicalRouteSummary(activeRouteCandidate),
    [activeRouteCandidate],
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
        routeTotalTime: summary?.totalLabel ?? null,
        serviceAlerts: serviceAlerts.alerts,
        incidents: [],
        nowMs: liveFeed.nowMs,
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
      liveFeed.nowMs,
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
    return requestInitialLocation(
      navigator.geolocation,
      DEFAULT_LOCATION,
      setUserLocation,
    );
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

  const openLiveMap = useCallback(() => setActiveTab("livemap"), []);
  const openChat = useCallback(() => setActiveTab("chat"), []);

  const startNewTrip = useCallback(() => {
    chat.reset();
    routePlanning.handleClearRoute();
    setNewTripKey((key) => key + 1);
    setActiveTab("chat");
  }, [chat, routePlanning]);

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

