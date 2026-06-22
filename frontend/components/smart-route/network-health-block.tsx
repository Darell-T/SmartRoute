"use client";

import type { CSSProperties } from "react";
import type { LiveNetworkSummary, LiveSystemSignals } from "@/types";
import LiveSummaryOrb from "@/components/smart-route/live-summary-orb";
import { Eyebrow } from "./eyebrow";
import {
  mapStatusToOrbColor,
  NETWORK_STATUS_HEX,
  NETWORK_STATUS_LABEL,
  normalizeNetworkStatus,
  type NetworkHealthStatus,
} from "./network-orb-color";

interface Props {
  summary: LiveNetworkSummary | null;
  signals: LiveSystemSignals | null;
  isLoading: boolean;
  degraded: boolean;
  error?: string | null;
}

function fallbackBody(status: NetworkHealthStatus, signals: LiveSystemSignals | null) {
  const alertCount = signals?.active_alert_count ?? 0;
  const routeCount = signals?.affected_route_count ?? 0;
  const staleCount = signals?.stale_vehicle_count ?? 0;
  if (status === "disrupted") {
    if (alertCount && routeCount) {
      const alertLabel = alertCount === 1 ? "alert is" : "alerts are";
      const lineLabel = routeCount === 1 ? "line" : "lines";
      const staleLabel = staleCount
        ? ` ${staleCount} train${staleCount === 1 ? " is" : "s are"} reporting stale positions, so headways may wobble a bit.`
        : " Major disruption needs attention.";
      return `${alertCount} subway ${alertLabel} active across ${routeCount} ${lineLabel}.${staleLabel}`;
    }
    return "Major disruption needs attention.";
  }
  if (status === "caution") {
    if (routeCount) {
      return `${routeCount} line${routeCount === 1 ? "" : "s"} reporting changes. Monitoring headways.`;
    }
    return "Monitoring service for shifts.";
  }
  return "All systems operating normally.";
}

const LEGEND_ITEMS: Array<{
  key: NetworkHealthStatus;
  label: string;
  color: string;
}> = [
  { key: "healthy", label: "Clear", color: NETWORK_STATUS_HEX.healthy },
  { key: "caution", label: "Minor", color: NETWORK_STATUS_HEX.caution },
  { key: "disrupted", label: "Major", color: NETWORK_STATUS_HEX.disrupted },
];

export function NetworkHealthBlock({
  summary,
  signals,
  isLoading,
  degraded,
  error,
}: Props) {
  void isLoading;
  void error;
  const status = normalizeNetworkStatus(
    signals?.network_status ?? summary?.status ?? (degraded ? "caution" : "healthy"),
  );
  const statusHex = NETWORK_STATUS_HEX[status];
  const statusStyle = { "--sr-status": statusHex } as CSSProperties;
  const body =
    status === "healthy"
      ? summary?.body?.trim() || fallbackBody(status, signals)
      : fallbackBody(status, signals);

  const alertCount = signals?.active_alert_count ?? 0;
  const lineCount = signals?.affected_route_count ?? 0;
  const majorCount = signals?.major_alert_count ?? (status === "disrupted" ? 1 : 0);

  return (
    <section className="sr-net-health" style={statusStyle}>
      {/* 1. Section label + status pill */}
      <div className="sr-net-health__row">
        <Eyebrow>Network Health</Eyebrow>
        <span className="sr-net-health__pill">
          <span aria-hidden="true" />
          {NETWORK_STATUS_LABEL[status]}
        </span>
      </div>

      {/* 2. Compact orb + summary copy */}
      <div className="sr-net-health__brief">
        <div className="sr-net-health__orb-wrap" aria-hidden="true">
          <LiveSummaryOrb
            phase="thinking"
            compact
            contained
            color={mapStatusToOrbColor(status)}
          />
        </div>
        <p className="sr-net-health__copy">{body}</p>
      </div>

      {/* 3. Inline status legend */}
      <ul className="sr-net-health__legend" aria-label="Service status legend">
        {LEGEND_ITEMS.map((item) => {
          const active = item.key === status;
          return (
            <li
              key={item.key}
              className="sr-net-health__legend-item"
              data-active={active ? "true" : "false"}
              style={{ "--sr-legend-dot": item.color } as CSSProperties}
            >
              <span aria-hidden="true" />
              {item.label}
            </li>
          );
        })}
      </ul>

      {/* 4. Stats row */}
      <dl className="sr-net-health__stats" aria-label="Network statistics">
        <div className="sr-net-health__stat">
          <dt>{alertCount}</dt>
          <dd>Alerts</dd>
        </div>
        <span aria-hidden="true" className="sr-net-health__stat-divider" />
        <div className="sr-net-health__stat">
          <dt>{lineCount}</dt>
          <dd>Lines</dd>
        </div>
        <span aria-hidden="true" className="sr-net-health__stat-divider" />
        <div className="sr-net-health__stat" data-emphasis="alert">
          <dt>{majorCount}</dt>
          <dd>Major</dd>
        </div>
      </dl>
    </section>
  );
}
