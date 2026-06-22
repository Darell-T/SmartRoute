"use client";

import { useEffect, useRef, useState } from "react";
import { fetchWsTicket, wsUrlWithTicket } from "./ws-ticket";
import type {
  LiveArrival,
  LiveFeedResponse,
  LiveFeedIncident,
  LiveNetworkSummary,
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
  summary: LiveNetworkSummary | null;
  signals: LiveSystemSignals | null;
  incidents: LiveFeedIncident[];
  updatedAt: number | null;
  isLoading: boolean;
  degraded: boolean;
  debug: LiveFeedResponse["debug"] | null;
  error: string | null;
  clockTick: number;
}

const INITIAL: LiveFeedState = {
  nearestStop: null,
  stops: [],
  arrivals: [],
  alerts: [],
  vehicles: [],
  summary: null,
  signals: null,
  incidents: [],
  updatedAt: null,
  isLoading: false,
  degraded: false,
  debug: null,
  error: null,
  clockTick: 0,
};

const DEBUG_LIVE_FEED = process.env.NODE_ENV !== "production";

function shouldSendLocation(
  previous: { lng: number; lat: number } | null,
  next: { lng: number; lat: number },
) {
  if (!previous) return true;
  return (
    Math.abs(previous.lat - next.lat) > 0.00045 ||
    Math.abs(previous.lng - next.lng) > 0.00045
  );
}

export function useLiveFeed(
  location: { lng: number; lat: number } | null,
  selectedRouteIds: string[] = [],
  atlasScan = false,
): LiveFeedState {
  const [state, setState] = useState<LiveFeedState>(INITIAL);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(1000);
  const locationRef = useRef(location);
  const selectedRouteIdsRef = useRef(selectedRouteIds);
  const atlasScanRef = useRef(atlasScan);
  const sentLocationRef = useRef<{ lng: number; lat: number } | null>(null);

  locationRef.current = location;
  selectedRouteIdsRef.current = selectedRouteIds;
  atlasScanRef.current = atlasScan;

  useEffect(() => {
    if (!location) return;

    let cancelled = false;
    let connecting = false;

    function sendLocation(ws: WebSocket, force = false) {
      const loc = locationRef.current;
      if (!loc || ws.readyState !== WebSocket.OPEN) return;
      if (!force && !shouldSendLocation(sentLocationRef.current, loc)) return;
      if (DEBUG_LIVE_FEED) {
        console.info("[live-feed/ws] send location", {
          lat: loc.lat,
          lng: loc.lng,
          selected_route_ids: selectedRouteIdsRef.current,
          force,
        });
      }
      ws.send(JSON.stringify({
        type: "location",
        lat: loc.lat,
        lng: loc.lng,
        selected_route_ids: selectedRouteIdsRef.current,
        atlas_scan: atlasScanRef.current,
      }));
      sentLocationRef.current = loc;
    }

    function scheduleReconnect() {
      if (cancelled || reconnectRef.current) return;
      const delay = Math.min(backoffRef.current, 30_000);
      reconnectRef.current = setTimeout(() => {
        reconnectRef.current = null;
        backoffRef.current = Math.min(backoffRef.current * 2, 30_000);
        connect();
      }, delay);
    }

    async function connect() {
      if (cancelled || wsRef.current || connecting) return;
      connecting = true;
      setState((prev) => ({ ...prev, isLoading: true, error: null }));

      let ticket: string;
      try {
        ticket = await fetchWsTicket("/ws/live-feed");
      } catch {
        connecting = false;
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          isLoading: false,
          degraded: true,
          error: "Live feed reconnecting",
        }));
        scheduleReconnect();
        return;
      }

      connecting = false;
      if (cancelled || wsRef.current) return;

      const ws = new WebSocket(wsUrlWithTicket("/ws/live-feed", ticket));
      wsRef.current = ws;

      ws.onopen = () => {
        backoffRef.current = 1000;
        if (DEBUG_LIVE_FEED) {
          console.info("[live-feed/ws] open");
        }
        sendLocation(ws, true);
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as
            | { type: "snapshot"; data: LiveFeedResponse }
            | { type: "error"; message: string };

          if (msg.type === "snapshot") {
            const data = msg.data;
            if (DEBUG_LIVE_FEED) {
              console.info("[live-feed/ws] snapshot", JSON.stringify({
                arrivals: data.arrivals?.length ?? 0,
                vehicles: data.vehicles?.length ?? 0,
                summary: data.summary
                  ? {
                      status: data.summary.status,
                      headline: data.summary.headline,
                      source: data.summary.source,
                    }
                  : null,
                updated_at: data.updated_at,
                degraded: data.degraded,
                debug: data.debug,
                sampleVehicles: (data.vehicles ?? []).slice(0, 3).map((vehicle) => ({
                  id: vehicle.id,
                  route_id: vehicle.route_id,
                  lat: vehicle.lat,
                  lng: vehicle.lng,
                  stale: vehicle.stale,
                  position_source: vehicle.position_source,
                  current_stop_sequence: vehicle.current_stop_sequence,
                  segment: vehicle.segment,
                })),
              }, null, 2));
            }
            setState((prev) => ({
              ...prev,
              nearestStop: data.nearest_stop ?? null,
              stops: data.stops ?? [],
              arrivals: data.arrivals ?? [],
              alerts: data.alerts ?? [],
              vehicles: data.vehicles ?? [],
              summary: data.summary ?? null,
              signals: data.signals ?? null,
              incidents: data.incidents ?? [],
              updatedAt: data.updated_at ?? Math.floor(Date.now() / 1000),
              degraded: Boolean(data.degraded),
              debug: data.debug ?? null,
              isLoading: false,
              error: null,
            }));
          } else if (msg.type === "error") {
            setState((prev) => ({
              ...prev,
              isLoading: false,
              degraded: true,
              error: msg.message,
            }));
          }
        } catch {
          setState((prev) => ({
            ...prev,
            isLoading: false,
            degraded: true,
            error: "Malformed live feed message",
          }));
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (cancelled) return;
        if (DEBUG_LIVE_FEED) {
          console.info("[live-feed/ws] close");
        }
        setState((prev) => ({
          ...prev,
          isLoading: false,
          degraded: true,
          error: "Live feed reconnecting",
        }));
        scheduleReconnect();
      };

      ws.onerror = () => {
        setState((prev) => ({ ...prev, degraded: true }));
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [Boolean(location)]);

  useEffect(() => {
    if (!location || !wsRef.current) return;
    const ws = wsRef.current;
    if (ws.readyState === WebSocket.OPEN && shouldSendLocation(sentLocationRef.current, location)) {
      ws.send(JSON.stringify({
        type: "location",
        lat: location.lat,
        lng: location.lng,
        selected_route_ids: selectedRouteIdsRef.current,
        atlas_scan: atlasScanRef.current,
      }));
      sentLocationRef.current = location;
    }
  }, [location?.lat, location?.lng]);

  useEffect(() => {
    // Re-announce when the ATLAS scan toggle flips so the backend starts/stops
    // the half-mile incident scan immediately, without waiting for movement.
    const ws = wsRef.current;
    const loc = locationRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !loc) return;
    ws.send(JSON.stringify({
      type: "location",
      lat: loc.lat,
      lng: loc.lng,
      selected_route_ids: selectedRouteIdsRef.current,
      atlas_scan: atlasScan,
    }));
  }, [atlasScan]);

  useEffect(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (DEBUG_LIVE_FEED) {
      console.info("[live-feed/ws] vehicle scope", {
        selected_route_ids: selectedRouteIds,
      });
    }
    ws.send(JSON.stringify({
      type: "vehicle_scope",
      selected_route_ids: selectedRouteIds,
    }));
  }, [selectedRouteIds.join("|")]);

  useEffect(() => {
    // Local display clock only. Live data arrives through the WebSocket.
    const id = setInterval(() => {
      setState((prev) => ({ ...prev, clockTick: prev.clockTick + 1 }));
    }, 10_000);
    return () => clearInterval(id);
  }, []);

  return state;
}
