"use client";

import { useEffect, useRef, useState } from "react";
import { fetchWsTicket, wsUrlWithTicket } from "../ws-ticket";
import { LiveFeedConnection } from "../live-feed-connection";
import type {
  LiveArrival,
  LiveFeedIncident,
  LiveFeedBusUpdate,
  LiveFeedResponse,
  NearbyTransitIssue,
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
  nearbyIssues: NearbyTransitIssue[];
  updatedAt: number | null;
  busGeneration: number | null;
  isLoading: boolean;
  degraded: boolean;
  debug: LiveFeedResponse["debug"] | null;
  error: string | null;
  nowMs: number;
}

const INITIAL: LiveFeedState = {
  nearestStop: null, stops: [], arrivals: [], alerts: [], vehicles: [], signals: null,
  incidents: [], nearbyIssues: [], updatedAt: null, isLoading: false, degraded: false, debug: null,
  error: null, nowMs: 0, busGeneration: null,
};

export function withLiveFeedNow<T extends { nowMs: number }>(state: T, nowMs: number): T {
  return nowMs > 0 ? { ...state, nowMs } : state;
}

function applySocketMessage(raw: string, setState: React.Dispatch<React.SetStateAction<LiveFeedState>>): void {
  try {
    const message = JSON.parse(raw);
    if (!(message instanceof Object) || !("type" in message)) throw new Error("not an object");
    if (message.type === "snapshot" && "data" in message && isLiveFeedSnapshot(message.data)) {
      applySnapshot(message.data, setState);
    } else if (message.type === "bus_update" && "data" in message && isBusUpdate(message.data)) {
      applyBusUpdate(message.data, setState);
    } else if (message.type === "error") {
      applyFeedError(message, setState);
    }
  } catch {
    setState((previous) => withLiveFeedNow({ ...previous, isLoading: false, degraded: true, error: "Malformed live feed message" }, Date.now()));
  }
}

function orEmpty<T>(value: T[] | null | undefined): T[] {
  return value ?? [];
}

function applySnapshot(
  data: LiveFeedResponse,
  setState: React.Dispatch<React.SetStateAction<LiveFeedState>>,
): void {
  setState((previous) => withLiveFeedNow({
    ...previous,
    nearestStop: data.nearest_stop ?? null,
    stops: orEmpty(data.stops),
    arrivals: orEmpty(data.arrivals),
    alerts: orEmpty(data.alerts),
    vehicles: orEmpty(data.vehicles),
    signals: data.signals ?? null,
    incidents: orEmpty(data.incidents),
    nearbyIssues: orEmpty(data.nearby_issues),
    updatedAt: data.updated_at ?? Math.floor(Date.now() / 1000),
    degraded: Boolean(data.degraded),
    debug: data.debug ?? null,
    isLoading: false,
    error: null,
    busGeneration: data.bus_generation ?? null,
  }, Date.now()));
}

function applyBusUpdate(
  data: LiveFeedBusUpdate,
  setState: React.Dispatch<React.SetStateAction<LiveFeedState>>,
): void {
  setState((previous) => {
    if (previous.busGeneration !== data.generation) return previous;
    const subwayArrivals = previous.arrivals.filter((arrival) => arrival.mode !== "bus");
    return withLiveFeedNow({
      ...previous,
      arrivals: [...subwayArrivals, ...data.arrivals].sort(
        (left, right) => (left.arrival_time ?? 0) - (right.arrival_time ?? 0),
      ),
      updatedAt: Math.max(previous.updatedAt ?? 0, data.fetched_at),
    }, Date.now());
  });
}

function applyFeedError<T>(
  message: T,
  setState: React.Dispatch<React.SetStateAction<LiveFeedState>>,
): void {
  if (!(message instanceof Object) || !("message" in message) || String(message.message) !== message.message) {
    throw new Error("missing error message");
  }
  const error = message.message;
  setState((previous) => withLiveFeedNow({ ...previous, isLoading: false, degraded: true, error }, Date.now()));
}

function isLiveFeedSnapshot<T>(value: T): value is T & LiveFeedResponse {
  return Boolean(value instanceof Object && "arrivals" in value && "updated_at" in value);
}

function isBusUpdate<T>(value: T): value is T & LiveFeedBusUpdate {
  if (!(value instanceof Object)) return false;
  if (!("generation" in value) || !("arrivals" in value) || !("fetched_at" in value) || !("status" in value)) {
    return false;
  }
  return (
    Number.isFinite(Number(value.generation))
    && Array.isArray(value.arrivals)
    && Number.isFinite(Number(value.fetched_at))
    && (value.status === "ready" || value.status === "cached" || value.status === "unavailable")
  );
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
        setState((previous) => {
          let next: LiveFeedState;
          if (status === "open") {
            next = { ...previous, isLoading: false, error: null };
          } else if (status === "error") {
            next = { ...previous, degraded: true };
          } else {
            next = {
              ...previous,
              isLoading: true,
              degraded: status === "reconnecting" || previous.degraded,
              error: status === "reconnecting" ? "Live feed reconnecting" : null,
            };
          }
          return withLiveFeedNow(next, Date.now());
        });
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
