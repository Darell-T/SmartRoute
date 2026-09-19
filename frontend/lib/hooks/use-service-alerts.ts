"use client";

import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { shouldApplyServiceAlertPoll } from "../service-alert-poll";
import { fetchWsTicket, wsUrlWithTicket } from "../ws-ticket";
import type { ServiceAlertDetail, ServiceAlertsResponse } from "@/types";

export type ServiceAlertConnectionState = "connecting" | "open" | "closed";

interface ServiceAlertsState {
  alerts: ServiceAlertDetail[];
  updatedAt: number | null;
  activeCount: number;
  affectedRouteCount: number;
  isLoading: boolean;
  error: string | null;
  connectionState: ServiceAlertConnectionState;
  changedAlertIds: Set<string>;
}

const INITIAL: ServiceAlertsState = {
  alerts: [],
  updatedAt: null,
  activeCount: 0,
  affectedRouteCount: 0,
  isLoading: true,
  error: null,
  connectionState: "connecting",
  changedAlertIds: new Set(),
};

function alertStableId(alert: ServiceAlertDetail, index: number) {
  const routes = alert.route_ids ?? alert.routeIds ?? [];
  return alert.alert_id || `${routes.join("-") || "system"}-${alert.start || index}`;
}

function alertSignature(alert: ServiceAlertDetail) {
  return JSON.stringify({
    id: alert.alert_id,
    header: alert.header,
    description: alert.description,
    route_ids: alert.route_ids ?? alert.routeIds,
    stop_ids: alert.stop_ids,
    stop_names: alert.stop_names,
    start: alert.start,
    end: alert.end,
  });
}

function signatureMap(alerts: ServiceAlertDetail[]) {
  return new Map(
    alerts.map((alert, index) => [alertStableId(alert, index), alertSignature(alert)]),
  );
}

function changedIdsFromMaps(
  previous: Map<string, string>,
  next: Map<string, string>,
) {
  if (previous.size === 0) return new Set<string>();
  const changed = new Set<string>();
  for (const [id, signature] of next.entries()) {
    if (previous.get(id) !== signature) changed.add(id);
  }
  return changed;
}

function parseServiceAlertsResponse<T>(data: T): ServiceAlertsResponse {
  if (!(data instanceof Object) || !("alerts" in data) || !Array.isArray(data.alerts)) {
    throw new Error("Service alerts unavailable");
  }
  // SAFETY: each alert object comes from the service-alerts contract after alerts[] is present.
  return {
    alerts: data.alerts as ServiceAlertDetail[],
    updated_at: "updated_at" in data ? Number(data.updated_at) : 0,
    active_count: "active_count" in data ? Number(data.active_count) : data.alerts.length,
    affected_route_count: "affected_route_count" in data ? Number(data.affected_route_count) : 0,
    source: "mta",
  };
}

async function fetchServiceAlerts(signal?: AbortSignal): Promise<ServiceAlertsResponse> {
  const response = await fetch("/api/service-alerts", {
    method: "GET",
    cache: "no-store",
    signal,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.error || "Service alerts unavailable");
  }
  return parseServiceAlertsResponse(data);
}

function applyServiceAlertSocketEvent(
  event: MessageEvent<string>,
  applyData: (data: ServiceAlertsResponse, connectionState: ServiceAlertConnectionState, changedAlertIds?: string[]) => void,
  setState: Dispatch<SetStateAction<ServiceAlertsState>>,
): void {
  try {
    const msg = JSON.parse(event.data);
    if (!(msg instanceof Object) || !("type" in msg)) return;
    if (msg.type === "SERVICE_SNAPSHOT" || msg.type === "SERVICE_UPDATE") {
      applyServiceAlertSnapshot(msg, applyData);
      return;
    }
    if (msg.type === "SERVICE_HEARTBEAT") {
      applyServiceAlertHeartbeat(msg, setState);
      return;
    }
    if (msg.type === "error" && "message" in msg && String(msg.message) === msg.message) {
      setState((prev) => ({ ...prev, error: msg.message }));
    }
  } catch {
    setState((prev) => ({
      ...prev,
      error: "Malformed service alert stream message",
    }));
  }
}

function applyServiceAlertSnapshot<T>(
  msg: T,
  applyData: (data: ServiceAlertsResponse, connectionState: ServiceAlertConnectionState, changedAlertIds?: string[]) => void,
): void {
  if (!(msg instanceof Object) || !("data" in msg)) return;
  const changedAlertIds =
    "changed_alert_ids" in msg && Array.isArray(msg.changed_alert_ids)
      ? msg.changed_alert_ids
      : undefined;
  applyData(parseServiceAlertsResponse(msg.data), "open", changedAlertIds);
}

function applyServiceAlertHeartbeat<T>(
  msg: T,
  setState: Dispatch<SetStateAction<ServiceAlertsState>>,
): void {
  const updatedAt =
    msg instanceof Object && "updated_at" in msg && Number.isFinite(Number(msg.updated_at))
      ? Number(msg.updated_at)
      : undefined;
  setState((prev) => ({
    ...prev,
    connectionState: "open",
    updatedAt: updatedAt ?? prev.updatedAt,
  }));
}

export function useServiceAlerts(pollMs = 60_000): ServiceAlertsState {
  const [state, setState] = useState<ServiceAlertsState>(INITIAL);
  const wsRef = useRef<WebSocket | null>(null);
  const wsEpochRef = useRef(0);
  const reconnectRef = useRef<number | null>(null);
  const clearChangedRef = useRef<number | null>(null);
  const previousSignaturesRef = useRef<Map<string, string>>(new Map());

  function isWsOpen() {
    return wsRef.current?.readyState === WebSocket.OPEN;
  }

  function applyData(
    data: ServiceAlertsResponse,
    connectionState: ServiceAlertConnectionState,
    explicitChangedIds?: string[],
  ) {
    const alerts = data.alerts ?? [];
    const nextSignatures = signatureMap(alerts);
    const computedChanged = changedIdsFromMaps(previousSignaturesRef.current, nextSignatures);
    const changedAlertIds = new Set(explicitChangedIds ?? computedChanged);
    previousSignaturesRef.current = nextSignatures;

    if (clearChangedRef.current) {
      window.clearTimeout(clearChangedRef.current);
      clearChangedRef.current = null;
    }

    setState({
      alerts,
      updatedAt: data.updated_at ?? Math.floor(Date.now() / 1000),
      activeCount: data.active_count ?? alerts.length,
      affectedRouteCount: data.affected_route_count ?? 0,
      isLoading: false,
      error: null,
      connectionState,
      changedAlertIds,
    });

    if (changedAlertIds.size > 0) {
      clearChangedRef.current = window.setTimeout(() => {
        setState((prev) => ({ ...prev, changedAlertIds: new Set() }));
      }, 2_000);
    }
  }

  useEffect(() => {
    let mounted = true;
    const controller = new AbortController();

    async function load() {
      if (isWsOpen()) return;
      const startedWsEpoch = wsEpochRef.current;
      setState((prev) => ({ ...prev, isLoading: true, error: null }));
      try {
        const data = await fetchServiceAlerts(controller.signal);
        if (!mounted) return;
        if (
          !shouldApplyServiceAlertPoll({
            startedWsEpoch,
            currentWsEpoch: wsEpochRef.current,
            wsIsOpen: isWsOpen(),
          })
        ) {
          return;
        }
        applyData(data, "closed");
      } catch (error) {
        if (!mounted || controller.signal.aborted) return;
        if (
          !shouldApplyServiceAlertPoll({
            startedWsEpoch,
            currentWsEpoch: wsEpochRef.current,
            wsIsOpen: isWsOpen(),
          })
        ) {
          return;
        }
        setState((prev) => ({
          ...prev,
          isLoading: false,
          connectionState: "closed",
          error: error instanceof Error ? error.message : "Service alerts unavailable",
        }));
      }
    }

    load();
    const interval = window.setInterval(load, pollMs);
    return () => {
      mounted = false;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [pollMs]);

  useEffect(() => {
    let cancelled = false;
    let connecting = false;
    let backoff = 1000;

    function scheduleReconnect() {
      if (cancelled || reconnectRef.current) return;
      const delay = Math.min(backoff, 30_000);
      reconnectRef.current = window.setTimeout(() => {
        reconnectRef.current = null;
        backoff = Math.min(backoff * 2, 30_000);
        connect();
      }, delay);
    }

    async function connect() {
      if (cancelled || wsRef.current || connecting) return;
      connecting = true;
      setState((prev) => ({ ...prev, connectionState: "connecting" }));

      let ticket: string;
      try {
        ticket = await fetchWsTicket("/ws/service-alerts");
      } catch {
        connecting = false;
        if (cancelled) return;
        setState((prev) => ({ ...prev, connectionState: "closed" }));
        scheduleReconnect();
        return;
      }

      connecting = false;
      if (cancelled || wsRef.current) return;

      const ws = new WebSocket(wsUrlWithTicket("/ws/service-alerts", ticket));
      wsRef.current = ws;

      ws.onopen = () => {
        backoff = 1000;
        wsEpochRef.current += 1;
        setState((prev) => ({ ...prev, connectionState: "open", error: null }));
      };

      ws.onmessage = (event) => applyServiceAlertSocketEvent(event, applyData, setState);

      ws.onclose = () => {
        wsRef.current = null;
        if (cancelled) return;
        setState((prev) => ({ ...prev, connectionState: "closed" }));
        scheduleReconnect();
      };

      ws.onerror = () => {
        setState((prev) => ({ ...prev, connectionState: "closed" }));
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
      if (clearChangedRef.current) window.clearTimeout(clearChangedRef.current);
      clearChangedRef.current = null;
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return state;
}
