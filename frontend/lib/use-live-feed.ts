"use client";

import { useEffect, useRef, useState } from "react";
import { fetchWsTicket, wsUrlWithTicket } from "./ws-ticket";
import { LiveFeedConnection } from "./live-feed-connection";
import type {
  LiveArrival,
  LiveFeedIncident,
  LiveFeedResponse,
  LiveSystemSignals,
  LiveVehicle,
  NearestStop,
  ServiceAlertDetail,
} from "@/types";

interface LiveFeedState {
  nearestStop: NearestStop | null;
  stops: NearestStop[];
  arrivals: LiveArrival[];
  alerts: ServiceAlertDetail[];
  vehicles: LiveVehicle[];
  signals: LiveSystemSignals | null;
  incidents: LiveFeedIncident[];
  updatedAt: number | null;
  isLoading: boolean;
  degraded: boolean;
  debug: LiveFeedResponse["debug"] | null;
  error: string | null;
  nowMs: number;
}

const INITIAL: LiveFeedState = {
  nearestStop: null, stops: [], arrivals: [], alerts: [], vehicles: [], signals: null,
  incidents: [], updatedAt: null, isLoading: false, degraded: false, debug: null,
  error: null, nowMs: 0,
};

export function withLiveFeedNow<T extends { nowMs: number }>(state: T, nowMs: number): T {
  return nowMs > 0 ? { ...state, nowMs } : state;
}

function applySocketMessage(raw: string, setState: React.Dispatch<React.SetStateAction<LiveFeedState>>): void {
  try {
    const message: unknown = JSON.parse(raw);
    if (!message || typeof message !== "object") throw new Error("not an object");
    const payload = message as { type?: unknown; data?: LiveFeedResponse; message?: unknown };
    if (payload.type === "snapshot" && payload.data) {
      const data = payload.data;
      setState((previous) => withLiveFeedNow({
        ...previous,
        nearestStop: data.nearest_stop ?? null,
        stops: data.stops ?? [], arrivals: data.arrivals ?? [], alerts: data.alerts ?? [],
        vehicles: data.vehicles ?? [], signals: data.signals ?? null, incidents: data.incidents ?? [],
        updatedAt: data.updated_at ?? Math.floor(Date.now() / 1000), degraded: Boolean(data.degraded),
        debug: data.debug ?? null, isLoading: false, error: null,
      }, Date.now()));
    } else if (payload.type === "error") {
      const error = payload.message;
      if (typeof error !== "string") throw new Error("missing error message");
      setState((previous) => withLiveFeedNow({ ...previous, isLoading: false, degraded: true, error }, Date.now()));
    }
  } catch {
    setState((previous) => withLiveFeedNow({ ...previous, isLoading: false, degraded: true, error: "Malformed live feed message" }, Date.now()));
  }
}

export function useLiveFeed(
  location: { lng: number; lat: number } | null,
  selectedRouteIds: string[] = [],
): LiveFeedState {
  const [state, setState] = useState<LiveFeedState>(INITIAL);
  const controllerRef = useRef<LiveFeedConnection | null>(null);
  const locationRef = useRef(location);
  const routeIdsRef = useRef(selectedRouteIds);
  const hasLocation = location !== null;

  useEffect(() => {
    locationRef.current = location;
    routeIdsRef.current = selectedRouteIds;
    controllerRef.current?.updateLocation(location);
    controllerRef.current?.updateRouteIds(selectedRouteIds);
  }, [location, selectedRouteIds]);

  useEffect(() => {
    if (!hasLocation) return;
    const controller = new LiveFeedConnection({
      fetchTicket: () => fetchWsTicket("/ws/live-feed"),
      createSocket: (ticket) => new WebSocket(wsUrlWithTicket("/ws/live-feed", ticket)),
      onMessage: (raw) => applySocketMessage(raw, setState),
      onStatus: (status) => {
        setState((previous) => withLiveFeedNow(status === "open"
          ? { ...previous, isLoading: false, error: null }
          : status === "error"
            ? { ...previous, degraded: true }
            : { ...previous, isLoading: true, degraded: status === "reconnecting" ? true : previous.degraded, error: status === "reconnecting" ? "Live feed reconnecting" : null }, Date.now()));
      },
    });
    controllerRef.current = controller;
    controller.updateLocation(locationRef.current);
    controller.updateRouteIds(routeIdsRef.current);
    controller.start();
    return () => {
      if (controllerRef.current === controller) controllerRef.current = null;
      controller.dispose();
    };
  }, [hasLocation]);

  useEffect(() => {
    const id = window.setInterval(() => setState((previous) => withLiveFeedNow(previous, Date.now())), 10_000);
    return () => window.clearInterval(id);
  }, []);

  return state;
}
