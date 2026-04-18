"use client";

import { useEffect, useState } from "react";
import { SmartRouteMark } from "./logo";

export type TabId = "planner" | "livemap" | "grok" | "alerts";

export const TABS: { id: TabId; label: string }[] = [
  { id: "planner", label: "Route Planner" },
  { id: "livemap", label: "Live Map" },
  { id: "grok", label: "Grok Intel" },
  { id: "alerts", label: "Service Alerts" },
];

interface Props {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  accent: string;
  systemStatus?: "nominal" | "warning" | "error";
}

function useLiveClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

export function Header({ activeTab, onTabChange, accent, systemStatus = "nominal" }: Props) {
  const now = useLiveClock();
  const clockStr = now
    ? now.toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      })
    : "--:-- --";

  const statusColor =
    systemStatus === "nominal"
      ? "#9ccfbf"
      : systemStatus === "warning"
        ? "#f0b04a"
        : "#ff6868";
  const statusLabel =
    systemStatus === "nominal"
      ? "ALL SYSTEMS NOMINAL"
      : systemStatus === "warning"
        ? "DEGRADED · MONITORING"
        : "FEED ERROR";

  return (
    <div
      className="flex items-center flex-shrink-0"
      style={{
        padding: "14px 20px",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(10,13,18,0.65)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
      }}
    >
      <SmartRouteMark accent={accent} />

      <nav className="flex gap-0.5 ml-10">
        {TABS.map((t) => {
          const active = t.id === activeTab;
          return (
            <button
              key={t.id}
              onClick={() => onTabChange(t.id)}
              className="relative cursor-pointer"
              style={{
                background: "transparent",
                border: "none",
                padding: "8px 14px",
                color: active ? "#fff" : "rgba(255,255,255,0.55)",
                fontFamily: "var(--font-geist), system-ui, sans-serif",
                fontSize: 12.5,
                fontWeight: active ? 600 : 500,
                letterSpacing: "-0.005em",
              }}
            >
              {t.label}
              {active && (
                <span
                  style={{
                    position: "absolute",
                    left: 14,
                    right: 14,
                    bottom: -15,
                    height: 2,
                    background: accent,
                    borderRadius: 2,
                    boxShadow: `0 0 10px ${accent}88`,
                  }}
                />
              )}
            </button>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-3.5">
        <div
          className="text-right"
          style={{ fontFamily: "var(--font-jetbrains-mono), monospace" }}
        >
          <div
            style={{
              fontSize: 11,
              color: "rgba(255,255,255,0.5)",
              letterSpacing: "0.08em",
            }}
          >
            {clockStr} · NYC
          </div>
          <div
            style={{
              fontSize: 10,
              color: statusColor,
              letterSpacing: "0.1em",
              display: "flex",
              alignItems: "center",
              gap: 5,
              justifyContent: "flex-end",
            }}
          >
            <span
              style={{
                width: 5,
                height: 5,
                borderRadius: 3,
                background: statusColor,
                animation: "srPulse 1.2s infinite",
              }}
            />
            {statusLabel}
          </div>
        </div>
        <div
          className="flex items-center justify-center"
          style={{
            width: 32,
            height: 32,
            borderRadius: 16,
            background: `linear-gradient(135deg, ${accent}, #8a8fe0)`,
            color: "#0b0e13",
            fontFamily: "var(--font-geist), system-ui, sans-serif",
            fontWeight: 600,
            fontSize: 11,
          }}
        >
          AR
        </div>
      </div>
    </div>
  );
}
