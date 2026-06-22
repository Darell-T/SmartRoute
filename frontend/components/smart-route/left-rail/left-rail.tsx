"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail (TSX port of the design handoff prototype)

   The primary surface for the SmartRoute web app. Three modes:
     1. Route   — destination input + ATLAS pick + arrivals + incidents
     2. Hub     — ambient real-time intelligence
     3. Alerts  — MTA service alert board, AI-contextualized

   The orb appears only inside the JarvisBlock (where ATLAS speaks). No
   static wordmark anywhere in the rail. Wordmark lives in marketing/favicon.

   Implementation notes:
   - Visual fidelity to the prototype is high-bar (pixel-perfect colors,
     spacing, motion, and tracking). All numeric constants here are copied
     from `design_handoff_left_rail/prototype/rail.jsx` and the README.
   - Three.js orb reused from `components/smart-route/agent-orb.tsx` via
     `./rail-orb.tsx`. MTA SVG bullets reused from `train-bullet.tsx`.
   - Motion is CSS-keyframe-driven (declared in
     `app/styles/smart-route-left-rail.css`). The FX primitives in
     `./fx.tsx` are thin React wrappers around those keyframes.
   - Live data is required via the `data` prop; the shapes in `./types.ts`
     are the contract. The standalone story (`app/dev/left-rail`) passes the
     demo fixtures from `./demo-data.ts`; production passes live data.
   ════════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { Dot, Meta } from "./atoms";
import type { DestinationSelection } from "@/types";
import type { LiveFeedIncident } from "@/types/api";
import type {
  Arrival,
  Direction,
  FeedEvent,
  IssueItem,
  JarvisState,
  NetworkHealth,
  RoutePlan,
  ServiceAlert,
  Station,
  TabId,
} from "./types";
import { incidentTone, incidentTimeLabel } from "./incident-format";
import { RouteView } from "./route-view";
import { HubView } from "./hub-view";
import { AlertsView } from "./alerts-view";

/* ──────────────────────────────────────────────────────────────
   Top-level component
   ────────────────────────────────────────────────────────────── */

export interface LeftRailProps {
  width?: number;
  jarvisState?: JarvisState;
  onJarvisStateChange?: (state: JarvisState) => void;
  // True while the narration audio is actually playing; drives the live
  // "ATLAS speaking" waveform in the result card.
  isSpeaking?: boolean;
  thinkingText?: string;
  /**
   * Initial tab. Defaults to "route". The rail manages tab state internally
   * but the prop allows deep-linking from a parent.
   */
  initialTab?: TabId;
  /**
   * Live data for the rail (required). The production app passes
   * `buildLeftRailData(...)` output plus incidents; the dev story passes the
   * demo fixtures. The seven core fields are always present so the rail never
   * silently renders demo values; incidents and issues are optional.
   */
  data: {
    station: Station;
    health: NetworkHealth;
    arrivals: Arrival[];
    plan: RoutePlan;
    feed: FeedEvent[];
    lineState: Record<string, "major" | "minor" | "planned">;
    alerts: ServiceAlert[];
    issues?: IssueItem[];
    incidents?: LiveFeedIncident[];
  };
  /**
   * Activates a route candidate when an Alternatives row is clicked.
   * Omit (demo mode) and the rows stay inert.
   */
  onSelectAlternative?: (candidateId: string) => void;
  atlasScanOn?: boolean;
  onAtlasScanToggle?: () => void;
  onSelectIncident?: (incident: LiveFeedIncident) => void;
  /**
   * Wires the WHERE TO box to the real trip flow (controlled input +
   * Mapbox autocomplete + voice + clear). Omit for the demo prototype.
   */
  search?: RailSearchProps;
}

export interface RailSearchProps {
  inputValue: string;
  isLoading: boolean;
  isListening: boolean;
  hasActiveRoute: boolean;
  onInputChange: (value: string) => void;
  onSubmit: (destination?: string, selection?: DestinationSelection | null) => void;
  onVoiceInput: () => void;
  onClear: () => void;
}

const TAB_ORDER: { id: TabId; label: string }[] = [
  { id: "route", label: "Route" },
  { id: "hub", label: "Hub" },
  { id: "alerts", label: "Alerts" },
];

export function LeftRail({
  width = 400,
  jarvisState = "standby",
  onJarvisStateChange,
  isSpeaking = false,
  thinkingText,
  initialTab = "route",
  data,
  onSelectAlternative,
  atlasScanOn = false,
  onAtlasScanToggle,
  onSelectIncident,
  search,
}: LeftRailProps) {
  const [tab, setTab] = useState<TabId>(initialTab);
  const [way, setWay] = useState<Direction>("uptown");
  const [clock, setClock] = useState(() => formatClock(new Date()));
  const [internalJarvis, setInternalJarvis] = useState<JarvisState>(jarvisState);
  const effectiveJarvis = onJarvisStateChange ? jarvisState : internalJarvis;
  const setJarvis = (next: JarvisState) => {
    if (onJarvisStateChange) onJarvisStateChange(next);
    else setInternalJarvis(next);
  };

  useEffect(() => {
    setInternalJarvis(jarvisState);
  }, [jarvisState]);

  useEffect(() => {
    const tick = () => setClock(formatClock(new Date()));
    tick();
    const id = setInterval(tick, 10_000);
    return () => clearInterval(id);
  }, []);

  const station = data.station;
  const health = data.health;
  const arrivals = data.arrivals;
  const plan = data.plan;
  const feed = data.feed;
  const lineState = data.lineState;
  const alerts = data.alerts;
  const incidents = data.incidents ?? [];
  // `data.issues` is accepted on LeftRailProps for forward compatibility but
  // no longer rendered (the IssuesFooter was removed per design feedback).

  useEffect(() => {
    // "both" rows satisfy whichever tab is active, so a crosstown bus
    // never forces a tab flip. The toggle itself stays two-state: when a
    // flip is needed, target the first arrival with a real direction; if
    // only "both" rows exist, the current selection stands.
    if (
      arrivals.length === 0
      || arrivals.some((arrival) => arrival.way === way || arrival.way === "both")
    ) {
      return;
    }
    const fallback = arrivals
      .map((arrival) => arrival.way)
      .find((w): w is Direction => w !== "both");
    if (fallback) setWay(fallback);
  }, [arrivals, way]);

  return (
    <aside
      className="sr-rail sr-rail-grain"
      style={{
        width,
        flexShrink: 0,
        // Border/radius/shadow come from the .sr-rail liquid-glass panel
        // styles; no flat border-right -- the rail floats over the map.
        overflowY: "auto",
        overflowX: "hidden",
        position: "relative",
        height: "100%",
      }}
    >
      <RailHeader tab={tab} onTabChange={setTab} clock={clock} />
      <div key={tab} className="sr-fade-in">
        {tab === "route" && (
          <RouteView
            station={station}
            health={health}
            arrivals={arrivals}
            plan={plan}
            incidents={incidents}
            atlasScanOn={atlasScanOn}
            way={way}
            onWayChange={setWay}
            jarvisState={effectiveJarvis}
            onJarvisStateChange={setJarvis}
            isSpeaking={isSpeaking}
            thinkingText={thinkingText}
            onSelectAlternative={onSelectAlternative}
            onSelectIncident={onSelectIncident}
            search={search}
          />
        )}
        {tab === "hub" && (
          <HubView
            feed={feed}
            lineState={lineState}
            atlasScanOn={atlasScanOn}
            onAtlasScanToggle={onAtlasScanToggle}
          />
        )}
        {tab === "alerts" && <AlertsView alerts={alerts} />}
      </div>
      {/* IssuesFooter (the 2-issues coral pill at the bottom of the rail)
          was removed per design feedback — it competed with the rail's tab
          nav for visual weight. The `IssuesFooter` function below is kept
          so a future caller can opt back in. The `issues` prop is still
          accepted on `LeftRailProps` for forward compatibility. */}
    </aside>
  );
}

function formatClock(d: Date) {
  let h = d.getHours();
  const m = d.getMinutes();
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")} ${ap}`;
}

/* ──────────────────────────────────────────────────────────────
   Header — shared across all three tabs
   No wordmark. Tabs on the left, live clock on the right.
   ────────────────────────────────────────────────────────────── */

function RailHeader({
  tab,
  onTabChange,
  clock,
}: {
  tab: TabId;
  onTabChange: (next: TabId) => void;
  clock: string;
}) {
  const visibleIncidents: LiveFeedIncident[] = [];
  const onSelectIncident: ((incident: LiveFeedIncident) => void) | undefined =
    undefined;
  return (
    <header style={{ padding: "22px 24px 0" }}>
      {false && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {visibleIncidents.slice(0, 6).map((incident) => {
            const tone = incidentTone(incident);
            const color = `var(--sr-${tone})`;
            return (
              <li key={incident.id} style={{ borderTop: "1px solid var(--sr-rule)" }}>
                <button
                  type="button"
                  onClick={() => onSelectIncident?.(incident)}
                  style={{
                    width: "100%",
                    padding: "14px 24px",
                    display: "grid",
                    gridTemplateColumns: "12px minmax(0, 1fr) auto",
                    gap: 12,
                    alignItems: "start",
                    textAlign: "left",
                    background: "transparent",
                    border: 0,
                    color: "inherit",
                    cursor: "pointer",
                  }}
                >
                  <Dot color={color} size={8} pulse={tone === "coral"} style={{ marginTop: 5 }} />
                  <span style={{ minWidth: 0 }}>
                    <span
                      style={{
                        display: "block",
                        fontFamily: "var(--sr-display)",
                        fontSize: 13.5,
                        fontWeight: 600,
                        lineHeight: 1.25,
                        color: "var(--sr-fg)",
                      }}
                    >
                      {incident.title}
                    </span>
                    <span
                      style={{
                        display: "block",
                        marginTop: 4,
                        fontFamily: "var(--sr-display)",
                        fontSize: 12.2,
                        lineHeight: 1.45,
                        color: "var(--sr-fg-3)",
                      }}
                    >
                      {incident.detail ?? "ATLAS is monitoring this incident near your route."}
                    </span>
                    <Meta style={{ display: "block", marginTop: 7 }}>
                      {incident.routeIds?.length ? `${incident.routeIds.join("/")} · ` : ""}
                      ATLAS signal
                    </Meta>
                  </span>
                  <Meta tone={tone}>{incidentTimeLabel(incident.updated_at)}</Meta>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <nav style={{ display: "flex", gap: 18, alignItems: "center" }}>
          {TAB_ORDER.map((t) => {
            const active = t.id === tab;
            return (
              <button
                key={t.id}
                onClick={() => onTabChange(t.id)}
                aria-pressed={active}
                style={{
                  background: "transparent",
                  border: 0,
                  padding: 0,
                  cursor: "pointer",
                  fontFamily: "var(--sr-mono)",
                  fontSize: 10.5,
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                  fontWeight: active ? 600 : 400,
                  color: active ? "var(--sr-fg)" : "var(--sr-muted)",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  transition: "color var(--sr-dur-1)",
                }}
              >
                {active && <Dot color="var(--sr-cyan)" size={5} />}
                {t.label}
              </button>
            );
          })}
        </nav>
        <Meta
          tone="muted"
          style={{ display: "flex", alignItems: "center", gap: 6 }}
          suppressHydrationWarning
        >
          <Dot color="var(--sr-cyan)" size={5} pulse /> Live · {clock}
        </Meta>
      </div>
      <div style={{ marginTop: 16, height: 1, background: "var(--sr-rule)" }} />
    </header>
  );
}
