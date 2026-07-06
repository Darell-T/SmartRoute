"use client";

/* ════════════════════════════════════════════════════════════════════════
   Dev story page — SmartRoute Left Rail

   A standalone preview of the rail with a tweaks panel that mirrors the
   prototype's. Visit `/dev/left-rail` to interact with all four route states
   (standby / thinking / result / error) and both tabs (Route / Alerts).

   This page is dev-only — not linked from the production site. It exists
   so design + engineering can A/B against the prototype HTML mockup.
   ════════════════════════════════════════════════════════════════════════ */

import { notFound } from "next/navigation";
import { useState } from "react";
import {
  LeftRail,
  type RouteRailStatus,
} from "@/components/smart-route/left-rail";
import { DEMO_RAIL_DATA } from "@/components/smart-route/left-rail/demo-data";

const STATES: RouteRailStatus[] = ["standby", "thinking", "result", "error"];

export default function LeftRailStoryPage() {
  const [routeStatus, setRouteStatus] = useState<RouteRailStatus>("result");
  const [width, setWidth] = useState(400);

  // Dev-only route: hide it from production builds so the demo fixtures are
  // never reachable by real users. (NODE_ENV is inlined at build time.)
  if (process.env.NODE_ENV === "production") notFound();

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#07090F",
        color: "#ECEEF6",
        display: "flex",
      }}
    >
      <LeftRail
        width={width}
        routeStatus={routeStatus}
        onRouteStatusChange={setRouteStatus}
        data={DEMO_RAIL_DATA}
        onSelectAlternative={() => {
          // Story-only: the production handler promotes the candidate via
          // useRoutePlanningController without replanning.
        }}
      />

      {/* Tweaks panel — mirrors the prototype's design-time controls. */}
      <aside
        style={{
          flex: 1,
          padding: "32px 36px",
          color: "#ECEEF6",
          fontFamily: "var(--font-space-grotesk), ui-sans-serif, system-ui, sans-serif",
        }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 16,
            marginBottom: 8,
          }}
        >
          <h1
            style={{
              fontSize: 28,
              fontWeight: 600,
              letterSpacing: "-0.02em",
              margin: 0,
            }}
          >
            SmartRoute Left Rail
          </h1>
          <span
            style={{
              fontFamily: "var(--font-jetbrains-mono), ui-monospace, monospace",
              fontSize: 11,
              letterSpacing: "0.22em",
              color: "#6B7287",
              textTransform: "uppercase",
            }}
          >
            v3 · dev story
          </span>
        </header>
        <p
          style={{
            maxWidth: 520,
            fontSize: 14,
            color: "#9AA0B4",
            lineHeight: 1.6,
            marginBottom: 28,
          }}
        >
          High-fidelity TSX preview of the production rail. Line bullets are
          authentic MTA SVGs; switch route status and width to inspect states.
        </p>

        <section style={{ marginBottom: 28 }}>
          <h2
            style={{
              fontFamily: "var(--font-jetbrains-mono), ui-monospace, monospace",
              fontSize: 11,
              letterSpacing: "0.22em",
              color: "#9AA0B4",
              textTransform: "uppercase",
              fontWeight: 600,
              marginBottom: 12,
            }}
          >
            Route status
          </h2>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {STATES.map((s) => {
              const active = routeStatus === s;
              return (
                <button
                  key={s}
                  onClick={() => setRouteStatus(s)}
                  style={{
                    background: active ? "#5FE3EA" : "#131A28",
                    color: active ? "#0A0E18" : "#ECEEF6",
                    border: 0,
                    padding: "8px 14px",
                    cursor: "pointer",
                    fontFamily:
                      "var(--font-jetbrains-mono), ui-monospace, monospace",
                    fontSize: 10,
                    letterSpacing: "0.22em",
                    textTransform: "uppercase",
                    fontWeight: 600,
                  }}
                >
                  {s}
                </button>
              );
            })}
          </div>
        </section>

        <section style={{ marginBottom: 28 }}>
          <h2
            style={{
              fontFamily: "var(--font-jetbrains-mono), ui-monospace, monospace",
              fontSize: 11,
              letterSpacing: "0.22em",
              color: "#9AA0B4",
              textTransform: "uppercase",
              fontWeight: 600,
              marginBottom: 12,
            }}
          >
            Width · {width}px
          </h2>
          <input
            type="range"
            min={340}
            max={520}
            step={4}
            value={width}
            onChange={(e) => setWidth(Number(e.target.value))}
            style={{ width: 320, accentColor: "#5FE3EA" }}
          />
        </section>
      </aside>
    </div>
  );
}
