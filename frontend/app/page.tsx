"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MotionConfig } from "motion/react";
import type { TransitRouteData } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import {
  authoritativeChatOrigin,
  locationStateForCoordinates,
  nextLocationState,
  requestInitialLocation,
  visibleMapLocation,
  type InitialLocationState,
} from "@/lib/initial-geolocation";
import { useLiveFeed } from "@/lib/use-live-feed";
import { useServiceAlerts } from "@/lib/use-service-alerts";
import { useMobileVisibleViewport } from "@/lib/use-mobile-visible-viewport";
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
} from "@/lib/agent-route-selection";
import {
  type TabId,
} from "@/components/smart-route/left-rail";
import { buildLeftRailData } from "@/components/smart-route/left-rail/live-data";
import { ChatPanel } from "@/components/smart-route/chat/chat-panel";
import { ChatSidebar } from "@/components/smart-route/chat/chat-sidebar";
import { MobileNavigation } from "@/components/smart-route/chat/mobile-navigation";
import { MobileStage } from "@/components/smart-route/chat/mobile-stage";
import { MobileTopBar } from "@/components/smart-route/chat/mobile-top-bar";
import { buildHomeNearbyModel } from "@/components/smart-route/chat/near-you";

import { useMobileRailSheet } from "@/components/smart-route/page/use-mobile-rail-sheet";
import { useRoutePlanningController } from "@/components/smart-route/page/use-route-planning-controller";

import { type AppTab, type MapActions, destinationCoordinatesFromRoute, nearbyStationSelection, routeCardsForSelection, routeRailStatus, shellPanelProps, toggleElementFullscreen, transitRouteData } from "./page-parts";

const LiveWorkspace = dynamic(
  () =>
    import("@/components/smart-route/page/live-workspace").then(
      (module) => module.LiveWorkspace,
    ),
  { ssr: false },
);

export default function SmartRoutePage() {
  return (
    <SmartRouteThemeProvider>
      <SmartRoutePageContent />
    </SmartRouteThemeProvider>
  );
}

function SmartRoutePageContent() {
  useMobileVisibleViewport();
  const [locationState, setLocationState] = useState<InitialLocationState>({
    status: "pending",
  });
  const userLocation = visibleMapLocation(locationState);
  const chatOrigin = authoritativeChatOrigin(locationState);
  const [activeTab, setActiveTab] = useState<AppTab>("chat");
  const [mapRequested, setMapRequested] = useState(false);
  const [leftRailTab, setLeftRailTab] = useState<TabId>("route");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
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

  const routeData = useMemo<TransitRouteData | null>(
    () => transitRouteData(activeRouteSteps, activeRouteCandidate?.itinerary),
    [activeRouteSteps, activeRouteCandidate?.itinerary],
  );

  const summary = useMemo(
    () => formatCanonicalRouteSummary(activeRouteCandidate),
    [activeRouteCandidate],
  );

  const destCoords = useMemo(
    () => destinationCoordinatesFromRoute(activeRouteSteps, selectedDestination?.coordinates),
    [activeRouteSteps, selectedDestination],
  );

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

  const homeNearby = useMemo(
    () =>
      buildHomeNearbyModel({
        data: leftRailData,
        nearestStopName: liveFeed.nearestStop?.stop_name,
        nearestRouteIds: liveFeed.nearestStop?.route_ids ?? [],
        arrivalsLoading: liveFeed.isLoading,
        arrivalsUnavailable: Boolean(liveFeed.error),
        serviceAlertsLoading:
          serviceAlerts.isLoading && serviceAlerts.alerts.length === 0,
        serviceAlertsUnavailable:
          Boolean(serviceAlerts.error) && serviceAlerts.alerts.length === 0,
        nearbyIssues: liveFeed.nearbyIssues,
        hasPlannedRoute: Boolean(activeRouteCandidate),
        locationState: locationState.status,
        nowMs: liveFeed.nowMs,
      }),
    [
      leftRailData,
      liveFeed.nearestStop,
      liveFeed.isLoading,
      liveFeed.error,
      serviceAlerts.isLoading,
      serviceAlerts.error,
      serviceAlerts.alerts.length,
      liveFeed.nearbyIssues,
      liveFeed.nowMs,
      activeRouteCandidate,
      locationState.status,
    ],
  );

  const routeStatus = routeRailStatus(isLoading, errorText, Boolean(activeRouteCandidate));

  useEffect(() => {
    return requestInitialLocation(
      navigator.geolocation,
      DEFAULT_LOCATION,
      setLocationState,
    );
  }, []);

  useEffect(() => {
    const desktopQuery = window.matchMedia("(min-width: 721px)");
    const closeNavigationOnDesktop = (event: MediaQueryListEvent) => {
      if (event.matches) setMobileNavigationOpen(false);
    };
    desktopQuery.addEventListener("change", closeNavigationOnDesktop);
    return () => {
      desktopQuery.removeEventListener("change", closeNavigationOnDesktop);
    };
  }, []);

  const handleLocationUpdate = useCallback(
    (coords: { lng: number; lat: number; fallback?: true }) => {
      setLocationState((current) =>
        nextLocationState(
          current,
          locationStateForCoordinates(coords, coords.fallback ? "fallback" : "precise"),
        ),
      );
    },
    [],
  );

  const handleMapReady = useCallback((actions: MapActions) => {
    mapActionsRef.current = actions;
  }, []);

  async function toggleFullscreen(target: HTMLElement | null) {
    if (typeof document === "undefined") return;
    await toggleElementFullscreen(target, {
      element: document.fullscreenElement,
      exit: () => document.exitFullscreen(),
    }, () => mapActionsRef.current?.recenter());
  }

  const chat = useAgentChat({ getOrigin: () => chatOrigin });
  const { theme, toggleTheme } = useSmartRouteTheme();

  const openLiveMap = useCallback(() => {
    setMapRequested(true);
    setActiveTab("livemap");
  }, []);
  const openAlerts = useCallback(() => {
    setLeftRailTab("alerts");
    setMapRequested(true);
    setActiveTab("livemap");
  }, []);
  const openChat = useCallback(() => setActiveTab("chat"), []);
  const closeMobileNavigation = useCallback(() => {
    setMobileNavigationOpen(false);
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLButtonElement>("#sr-mobile-menu-trigger")?.focus();
    });
  }, []);

  const startNewTrip = useCallback(() => {
    chat.reset();
    routePlanning.handleClearRoute();
    setNewTripKey((key) => key + 1);
    setLeftRailTab("route");
    setActiveTab("chat");
  }, [chat, routePlanning]);

  const handleOpenNearbyStation = useCallback(
    (arrivals: ArrivalsTurnPayload) => {
      const selection = nearbyStationSelection(arrivals);
      if (selection) {
        routePlanning.handleSearchSubmit(selection.label, selection);
      }
      openLiveMap();
    },
    [openLiveMap, routePlanning],
  );

  // Card tap -> the same route model used by manual planning. This lets the
  // map, detailed rail steps, and alternatives stay synchronized while chat
  // remains mounted for follow-up questions.
  const handleSelectRouteCard = useCallback(
    (card: RouteCard) => {
      const plan = agentRoutePlanFromCards(
        routeCardsForSelection(chat.messages, card.card_id, [card]),
        card.card_id,
      );
      if (!plan) return;
      routePlanning.handleLoadExternalRoutes(plan);
      setLeftRailTab("route");
      openLiveMap();
    },
    [chat.messages, openLiveMap, routePlanning],
  );

  const isLivemapTab = activeTab === "livemap";
  const livemapPanel = shellPanelProps(isLivemapTab);
  const chatPanel = shellPanelProps(!isLivemapTab);

  return (
    <MotionConfig reducedMotion="user">
      <main
        className="sr-tab-shell"
        data-tab={activeTab}
        data-sr-theme={theme}
        data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
      >
        <h1 className="sr-only">SmartRoute</h1>
        <MobileNavigation
          open={mobileNavigationOpen}
          activeTab={activeTab}
          theme={theme}
          onClose={closeMobileNavigation}
          onOpenChat={openChat}
          onOpenLiveMap={openLiveMap}
          onNewTrip={startNewTrip}
          onToggleTheme={toggleTheme}
        />
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

        <MobileStage
          navigationOpen={mobileNavigationOpen}
          onDismissNavigation={closeMobileNavigation}
        >
          <MobileTopBar
            navigationOpen={mobileNavigationOpen}
            showBrand={!isLivemapTab}
            onOpenNavigation={() => setMobileNavigationOpen(true)}
            onNewTrip={startNewTrip}
          />

          <div
            className={`sr-app-shell sr-tab-shell__panel sr-tab-shell__panel--livemap${livemapPanel.hiddenClass}`}
            data-active-tab="livemap"
            inert={livemapPanel.inert}
            style={{
              // Single full-viewport row: 400px LeftRail | 1fr Map. The rail owns
              // Route / Alerts, while the map carries its own overlays.
              display: "flex",
              flexDirection: "row",
              overflow: "hidden",
            }}
          >
            {mapRequested ? (
              <LiveWorkspace
                mobileRail={mobileRail}
                routePlanning={routePlanning}
                leftRailData={leftRailData}
                routeStatus={routeStatus}
                hasActiveRoute={Boolean(summary)}
                leftRailTab={leftRailTab}
                onLeftRailTabChange={setLeftRailTab}
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
            ) : null}
          </div>

          <div
            className={`sr-chat-tab sr-tab-shell__panel sr-tab-shell__panel--chat${chatPanel.hiddenClass}`}
            data-sr-theme={theme}
            inert={chatPanel.inert}
          >
            <ChatPanel
              key={newTripKey}
              chat={chat}
              theme={theme}
              nearby={homeNearby}
              onOpenLiveMap={openLiveMap}
              onViewAlerts={openAlerts}
              onSelectRouteCard={handleSelectRouteCard}
              onOpenNearbyStation={handleOpenNearbyStation}
            />
          </div>
        </MobileStage>
      </main>
    </MotionConfig>
  );
}
