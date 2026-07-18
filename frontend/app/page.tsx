"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MotionConfig } from "motion/react";
import type { TransitRouteData } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import { useLiveFeed } from "@/lib/use-live-feed";
import { useServiceAlerts } from "@/lib/use-service-alerts";
import { deriveTransitRouteIds } from "@/lib/route-planning";
import { summarizeRoute } from "@/lib/smart-route";
import { useAgentChat } from "@/lib/use-agent-chat";
import { useChatTheme } from "@/lib/use-chat-theme";
import type { RouteCard } from "@/lib/agent-chat-stream";
import { agentRouteFromCard, type AgentRouteSelection } from "@/lib/agent-route-selection";
import { type RouteRailStatus } from "@/components/smart-route/left-rail";
import { buildLeftRailData } from "@/components/smart-route/left-rail/live-data";
import { ChatPanel } from "@/components/smart-route/chat/chat-panel";
import { TabToggle } from "@/components/smart-route/chat/tab-toggle";

import { LiveWorkspace } from "@/components/smart-route/page/live-workspace";
import { useMobileRailSheet } from "@/components/smart-route/page/use-mobile-rail-sheet";
import { useRoutePlanningController } from "@/components/smart-route/page/use-route-planning-controller";

import { type AppTab, type MapActions } from "./page-parts";

export default function SmartRoutePage() {
  const [userLocation, setUserLocation] = useState<{
    lng: number;
    lat: number;
  } | null>(null);
  const [activeTab, setActiveTab] = useState<AppTab>("chat");
  const mapActionsRef = useRef<MapActions | null>(null);
  const liveMapFrameRef = useRef<HTMLElement | null>(null);
  const mobileRail = useMobileRailSheet();

  const routePlanning = useRoutePlanningController({
    userLocation,
  });
  const {
    selectedDestination,
    recommendationText,
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

  // The agent-selected route (a tapped chat route card) never enters the
  // rail's own state -- the rail must stay in standby for agent routes so
  // rail and map never disagree about what's "the" active route (see the
  // build plan's "Card->map wiring" section). It only ever overrides what
  // the map itself renders, below.
  const [agentRoute, setAgentRoute] = useState<AgentRouteSelection | null>(null);

  const routeData = useMemo<TransitRouteData | null>(() => {
    if (agentRoute) return { steps: agentRoute.steps };
    return activeRouteSteps.length > 0 ? { steps: activeRouteSteps } : null;
  }, [agentRoute, activeRouteSteps]);

  const summary = useMemo(
    () =>
      activeRouteSteps.length > 0
        ? summarizeRoute(activeRouteSteps, new Date(), activeRouteCandidate?.total_minutes)
        : null,
    [activeRouteSteps, activeRouteCandidate?.total_minutes],
  );

  const destCoords = useMemo(() => {
    if (agentRoute) return agentRoute.destCoords;
    const lastStep = activeRouteSteps[activeRouteSteps.length - 1];
    const rawDest =
      lastStep?.type === "WALK" ? lastStep.end_point : lastStep?.arrival_coords;
    if (rawDest) {
      return { lat: rawDest.latitude, lng: rawDest.longitude };
    }
    return selectedDestination?.coordinates ?? null;
  }, [agentRoute, activeRouteSteps, selectedDestination]);

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
  const { theme, toggleTheme } = useChatTheme();

  const openLiveMap = useCallback(() => setActiveTab("livemap"), []);

  // Card tap -> map handoff. Clearing the rail's route FIRST (before setting
  // agentRoute) guarantees the rail drops to standby the instant an agent
  // route takes over the map, so the two never show conflicting routes.
  const handleSelectRouteCard = useCallback(
    (card: RouteCard) => {
      const selection = agentRouteFromCard(card);
      if (!selection) return;
      routePlanning.handleClearRoute();
      setAgentRoute(selection);
      setActiveTab("livemap");
    },
    [routePlanning],
  );

  // Thin wrappers around the rail's three manual route entry points so a
  // manual search / alternative pick / clear always drops any agent route
  // first -- rail and map must never disagree about which route is "the"
  // active one. The controller itself (use-route-planning-controller.ts) is
  // untouched; this only intercepts what page.tsx hands down to LiveWorkspace.
  const manualRoutePlanning = useMemo(
    () => ({
      ...routePlanning,
      handleSearchSubmit: (
        ...args: Parameters<typeof routePlanning.handleSearchSubmit>
      ) => {
        setAgentRoute(null);
        routePlanning.handleSearchSubmit(...args);
      },
      handleSelectAlternative: (
        ...args: Parameters<typeof routePlanning.handleSelectAlternative>
      ) => {
        setAgentRoute(null);
        routePlanning.handleSelectAlternative(...args);
      },
      handleClearRoute: () => {
        setAgentRoute(null);
        routePlanning.handleClearRoute();
      },
    }),
    [routePlanning],
  );

  const isLivemapTab = activeTab === "livemap";

  return (
    <MotionConfig reducedMotion="user">
      <div className="sr-tab-shell" data-tab={activeTab}>
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
            routePlanning={manualRoutePlanning}
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
            chat={chat}
            theme={theme}
            onToggleTheme={toggleTheme}
            nearbyTransitGroups={leftRailData.nearbyTransitGroups ?? []}
            nearbyArrivals={leftRailData.arrivals}
            nearbyBusArrivals={leftRailData.nearbyBusArrivals ?? []}
            nearestStopName={leftRailData.station.name}
            onOpenLiveMap={openLiveMap}
            onSelectRouteCard={handleSelectRouteCard}
          />
        </div>

        <TabToggle activeTab={activeTab} onChange={setActiveTab} />
      </div>
    </MotionConfig>
  );
}

