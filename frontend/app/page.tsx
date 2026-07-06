"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  LiveFeedIncident,
  TransitRouteData,
} from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import { useLiveFeed } from "@/lib/use-live-feed";
import { useServiceAlerts } from "@/lib/use-service-alerts";
import { deriveTransitRouteIds } from "@/lib/route-planning";
import { summarizeRoute } from "@/lib/smart-route";
import { type RouteRailStatus } from "@/components/smart-route/left-rail";
import { buildLeftRailData } from "@/components/smart-route/left-rail/live-data";

import { LiveWorkspace } from "@/components/smart-route/page/live-workspace";
import { useMobileRailSheet } from "@/components/smart-route/page/use-mobile-rail-sheet";
import { useRoutePlanningController } from "@/components/smart-route/page/use-route-planning-controller";

import { type MapActions } from "./page-parts";

export default function SmartRoutePage() {
  const [userLocation, setUserLocation] = useState<{
    lng: number;
    lat: number;
  } | null>(null);
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
    tripIncidents,
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

  const liveFeed = useLiveFeed(
    userLocation,
    activeRouteCandidate ? activeRouteIds : [],
    false,
  );
  const serviceAlerts = useServiceAlerts();

  // Merge live-feed and route-planning incidents for rail alert context.
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
        recommendationText,
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
      recommendationText,
      summary,
      serviceAlerts.alerts,
      routeAwareIncidents,
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

  return (
    <div
      className="sr-app-shell"
      data-active-tab="livemap"
      style={{
        // Single full-viewport row: 400px LeftRail | 1fr Map. The rail owns
        // Route / Alerts, while the map carries its own overlays.
        height: "100dvh",
        width: "100vw",
        display: "flex",
        flexDirection: "row",
        overflow: "hidden",
      }}
    >
      <LiveWorkspace
        mobileRail={mobileRail}
        routePlanning={routePlanning}
        leftRailData={leftRailDisplayData}
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
  );
}

