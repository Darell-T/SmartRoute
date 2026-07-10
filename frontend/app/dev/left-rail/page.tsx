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
import type {
  Alternative,
  RouteDetailStep,
  RoutePlan,
  RouteStep,
} from "@/components/smart-route/left-rail/types";

const STATES: RouteRailStatus[] = ["standby", "thinking", "result", "error"];

const RECOMMENDED_PLAN = DEMO_RAIL_DATA.plan;

/* Story-only: the production handler promotes a candidate via
   useRoutePlanningController's precomputed analysis, without replanning.
   This harness has no controller, so it fakes the same outcome by hand —
   swapping the headline numbers over from the chosen alternative — purely
   so the scroll choreography (which fires on a real plan-identity change)
   has something to react to on this page. */
function stepsFromAlternative(alternative: Alternative): RouteStep[] {
  const strip = alternative.strip ?? [];
  const walkIn = strip.find((segment) => segment.kind === "walk");
  const steps: RouteStep[] = [];

  steps.push({
    type: "walk",
    action: "Walk",
    title: "Walk",
    detail: `To ${alternative.fromStop ?? "the platform"}`,
    duration:
      walkIn && walkIn.kind === "walk" && typeof walkIn.minutes === "number"
        ? `${walkIn.minutes} min`
        : "3 min",
  });
  steps.push({
    type: "board",
    action: "Board",
    line: alternative.line,
    title: `${alternative.line} train`,
    detail: alternative.dest,
    duration: typeof alternative.departsInMinutes === "number" ? "now" : "",
    note:
      typeof alternative.departsInMinutes === "number"
        ? `Departs in ${alternative.departsInMinutes} min`
        : undefined,
    live: typeof alternative.departsInMinutes === "number",
  });
  steps.push({
    type: "ride",
    action: "Ride",
    title: `Ride the ${alternative.line}`,
    detail: [alternative.fromStop, alternative.toStop]
      .filter(Boolean)
      .join(" → "),
    duration:
      typeof alternative.totalMinutes === "number"
        ? `${alternative.totalMinutes} min`
        : "",
  });
  steps.push({
    type: "destination",
    action: "Arrive",
    title: alternative.toStop ?? alternative.dest,
    detail: "",
    duration: "2 min",
  });
  return steps;
}

function detailStepsFromAlternative(
  alternative: Alternative,
): RouteDetailStep[] {
  const walkIn = alternative.strip?.find((segment) => segment.kind === "walk");
  return [
    {
      kind: "walk",
      title: `Walk to ${alternative.fromStop ?? "the platform"}`,
      subtitle:
        walkIn && walkIn.kind === "walk" && typeof walkIn.minutes === "number"
          ? `About ${walkIn.minutes} min`
          : undefined,
    },
    {
      kind: "board",
      routeId: alternative.line,
      mode: "subway",
      title: `Board the ${alternative.line} train`,
      subtitle: alternative.dest,
      note:
        typeof alternative.departsInMinutes === "number"
          ? `Departs in ${alternative.departsInMinutes} min`
          : undefined,
      live: typeof alternative.departsInMinutes === "number",
    },
    {
      kind: "ride",
      routeId: alternative.line,
      mode: "subway",
      title: `Ride the ${alternative.line}`,
      fromStop: alternative.fromStop,
      toStop: alternative.toStop,
      rideMeta:
        typeof alternative.totalMinutes === "number"
          ? `Ride · ${alternative.totalMinutes} min`
          : undefined,
    },
    { kind: "walk", title: "Walk to destination", subtitle: "About 2 min" },
  ];
}

function planFromAlternative(alternative: Alternative): RoutePlan {
  const rideCount = (alternative.strip ?? []).filter(
    (segment) => segment.kind === "ride",
  ).length;

  return {
    ...RECOMMENDED_PLAN,
    headline: `Take the ${alternative.line} from ${alternative.fromStop ?? "your stop"}.`,
    rationale: alternative.reason
      ? `You're using the ${alternative.line} to ${alternative.dest} instead. The ${RECOMMENDED_PLAN.pickedLine} is still the faster overall pick — ${alternative.reason}.`
      : `You're using the ${alternative.line} to ${alternative.dest} instead of the recommended ${RECOMMENDED_PLAN.pickedLine}.`,
    headsign: alternative.dest,
    isAlternativeRoute: true,
    eta: alternative.arriveLabel ?? RECOMMENDED_PLAN.eta,
    totalTime:
      typeof alternative.totalMinutes === "number"
        ? `${alternative.totalMinutes} min`
        : RECOMMENDED_PLAN.totalTime,
    leaveByLabel: alternative.leavesLabel ?? RECOMMENDED_PLAN.leaveByLabel,
    nextDepartureMinutes: alternative.departsInMinutes,
    transferCount: Math.max(0, rideCount - 1),
    strip: alternative.strip ?? RECOMMENDED_PLAN.strip,
    detailSteps: detailStepsFromAlternative(alternative),
    pickedLine: alternative.line,
    steps: stepsFromAlternative(alternative),
    // The rest of the field stays selectable — the harness always resolves
    // against the original fixture's alternatives, so picking again (or
    // resetting) never depends on what's currently on screen.
    alternatives: RECOMMENDED_PLAN.alternatives.filter(
      (candidate) => candidate.id !== alternative.id,
    ),
  };
}

export default function LeftRailStoryPage() {
  const [routeStatus, setRouteStatus] = useState<RouteRailStatus>("result");
  const [width, setWidth] = useState(400);
  const [plan, setPlan] = useState<RoutePlan>(RECOMMENDED_PLAN);
  const isAlternativeSelected = plan !== RECOMMENDED_PLAN;

  // Dev-only route: hide it from production builds so the demo fixtures are
  // never reachable by real users. (NODE_ENV is inlined at build time.)
  if (process.env.NODE_ENV === "production") notFound();

  return (
    <div
      style={{
        // A bounded height (not just minHeight) so the LeftRail aside's own
        // `height: 100%` resolves against something real and its
        // `overflowY: auto` actually engages — otherwise the rail grows to
        // fit its content instead of scrolling internally, and the whole
        // page scrolls instead. Production gets this from the live shell's
        // flex-column layout; this harness needs it spelled out.
        height: "100vh",
        background: "#07090F",
        color: "#ECEEF6",
        display: "flex",
      }}
    >
      <LeftRail
        width={width}
        routeStatus={routeStatus}
        onRouteStatusChange={setRouteStatus}
        data={{ ...DEMO_RAIL_DATA, plan }}
        onSelectAlternative={(candidateId) => {
          const alternative = RECOMMENDED_PLAN.alternatives.find(
            (candidate) => candidate.id === candidateId,
          );
          if (!alternative) return;
          setPlan(planFromAlternative(alternative));
        }}
      />

      {/* Tweaks panel — mirrors the prototype's design-time controls. */}
      <aside
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
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
            Active plan
          </h2>
          <p
            style={{
              maxWidth: 420,
              margin: "0 0 12px",
              fontSize: 13,
              color: "#9AA0B4",
              lineHeight: 1.6,
            }}
          >
            {isAlternativeSelected
              ? `Showing the selected alternative (${plan.pickedLine} to ${plan.headsign}). Tapping "Use" on a route card drives this — it's how the scroll choreography gets exercised on this page.`
              : `Showing the recommended route (${plan.pickedLine} to ${plan.headsign}).`}
          </p>
          <button
            onClick={() => setPlan(RECOMMENDED_PLAN)}
            disabled={!isAlternativeSelected}
            style={{
              background: isAlternativeSelected ? "#5FE3EA" : "#131A28",
              color: isAlternativeSelected ? "#0A0E18" : "#6B7287",
              border: 0,
              padding: "8px 14px",
              cursor: isAlternativeSelected ? "pointer" : "not-allowed",
              fontFamily: "var(--font-jetbrains-mono), ui-monospace, monospace",
              fontSize: 10,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              fontWeight: 600,
            }}
          >
            Reset to recommended
          </button>
        </section>
      </aside>
    </div>
  );
}
