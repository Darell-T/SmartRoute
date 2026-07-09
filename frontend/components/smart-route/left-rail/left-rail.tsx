"use client";

import { useMemo, useState } from "react";
import { MotionConfig, motion } from "motion/react";
import type { DestinationSelection } from "@/types";
import type { LiveFeedIncident } from "@/types/api";
import type {
  Arrival,
  Direction,
  FeedEvent,
  IssueItem,
  NearbyTransitGroup,
  NetworkHealth,
  RoutePlan,
  RouteRailStatus,
  ServiceAlert,
  Station,
  TabId,
} from "./types";
import { RouteView } from "./route-view";
import { AlertsView } from "./alerts-view";

type ArrivalFilter = Direction;

export interface LeftRailProps {
  width?: number;
  routeStatus?: RouteRailStatus;
  onRouteStatusChange?: (status: RouteRailStatus) => void;
  initialTab?: TabId;
  data: {
    station: Station;
    health: NetworkHealth;
    arrivals: Arrival[];
    nearbyTransitGroups?: NearbyTransitGroup[];
    nearbyBusArrivals?: Arrival[];
    plan: RoutePlan;
    feed: FeedEvent[];
    lineState: Record<string, "major" | "minor" | "planned">;
    alerts: ServiceAlert[];
    issues?: IssueItem[];
    incidents?: LiveFeedIncident[];
  };
  onSelectAlternative?: (candidateId: string) => void;
  search?: RailSearchProps;
  onRouteSearchFocusChange?: (focused: boolean) => void;
  onRailInteraction?: () => void;
}

export interface RailSearchProps {
  inputValue: string;
  isLoading: boolean;
  planningPhase: RailPlanningPhase;
  hasActiveRoute: boolean;
  onInputChange: (value: string) => void;
  onSubmit: (
    destination?: string,
    selection?: DestinationSelection | null,
  ) => void;
  onCancelPlanning: () => void;
  onClear: () => void;
}

export type RailPlanningPhase = "idle" | "cancellable" | "finalizing";

const TAB_ORDER = [
  { id: "route" as const, label: "Route", icon: RouteTabIcon },
  { id: "alerts" as const, label: "Alerts", icon: AlertsTabIcon },
];

export function LeftRail({
  width = 420,
  routeStatus,
  onRouteStatusChange,
  initialTab = "route",
  data,
  onSelectAlternative,
  search,
  onRouteSearchFocusChange,
  onRailInteraction,
}: LeftRailProps) {
  const [tab, setTab] = useState<TabId>(initialTab);
  const [preferredWay, setPreferredWay] = useState<ArrivalFilter>("uptown");
  const [internalRouteState, setInternalRouteState] =
    useState<RouteRailStatus>(routeStatus ?? "standby");
  const effectiveRouteState = routeStatus ?? internalRouteState;

  function setRouteState(next: RouteRailStatus) {
    setInternalRouteState(next);
    onRouteStatusChange?.(next);
  }

  // Routes the user is standing near (from the Nearby Transit feed) — the
  // Alerts tab uses this to float alerts on the user's own lines to the top.
  const nearbyRouteIds = useMemo(() => {
    const set = new Set<string>();
    for (const group of data.nearbyTransitGroups ?? []) {
      for (const routeId of group.routeIds) set.add(routeId.toUpperCase());
    }
    for (const arrival of data.arrivals ?? []) {
      for (const routeId of arrival.routeIds) set.add(routeId.toUpperCase());
    }
    for (const arrival of data.nearbyBusArrivals ?? []) {
      for (const routeId of arrival.routeIds) set.add(routeId.toUpperCase());
    }
    return Array.from(set);
  }, [data.nearbyTransitGroups, data.arrivals, data.nearbyBusArrivals]);

  return (
    <MotionConfig reducedMotion="user">
      <aside
        className="sr-rail"
        style={{
          width,
          flexShrink: 0,
          overflowY: "auto",
          overflowX: "hidden",
          position: "relative",
          height: "100%",
        }}
      >
        <RailHeader
          tab={tab}
          onTabChange={setTab}
          onInteraction={onRailInteraction}
        />
        <div key={tab} className="sr-fade-in">
          {tab === "route" && (
            <RouteView
              station={data.station}
              health={data.health}
              arrivals={data.arrivals}
              nearbyTransitGroups={data.nearbyTransitGroups ?? []}
              nearbyBusArrivals={data.nearbyBusArrivals ?? []}
              alerts={data.alerts}
              incidents={data.incidents}
              plan={data.plan}
              way={preferredWay}
              onWayChange={setPreferredWay}
              routeStatus={effectiveRouteState}
              onRouteStatusChange={setRouteState}
              onSelectAlternative={onSelectAlternative}
              search={search}
              onSearchFocusChange={onRouteSearchFocusChange}
              onRequestRailExpand={onRailInteraction}
            />
          )}
          {tab === "alerts" && (
            <AlertsView
              alerts={data.alerts}
              feed={data.feed}
              nearbyRouteIds={nearbyRouteIds}
            />
          )}
        </div>
      </aside>
    </MotionConfig>
  );
}

function RailHeader({
  tab,
  onTabChange,
  onInteraction,
}: {
  tab: TabId;
  onTabChange: (next: TabId) => void;
  onInteraction?: () => void;
}) {
  return (
    <header className="sr-rail-header">
      <nav className="sr-rail-tabs" aria-label="SmartRoute sections">
        {TAB_ORDER.map((item) => {
          const Icon = item.icon;
          const active = item.id === tab;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                onInteraction?.();
                onTabChange(item.id);
              }}
              aria-current={active ? "page" : undefined}
              data-active={active ? "true" : "false"}
            >
              <Icon size={16} strokeWidth={1.8} />
              <span>{item.label}</span>
              {active && (
                <motion.span
                  layoutId="sr-rail-tab-underline"
                  className="sr-tab-underline"
                  transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
                  aria-hidden="true"
                />
              )}
            </button>
          );
        })}
      </nav>
    </header>
  );
}

function RouteTabIcon({
  size = 17,
  strokeWidth = 1.8,
}: {
  size?: number;
  strokeWidth?: number;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <path
        d="M5 4.5h6.5a3.5 3.5 0 0 1 0 7H8.5a3.5 3.5 0 0 0 0 7H15"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={strokeWidth}
      />
      <circle cx="5" cy="4.5" r="1.6" fill="currentColor" />
      <circle cx="15" cy="18.5" r="1.6" fill="currentColor" />
    </svg>
  );
}

function AlertsTabIcon({
  size = 17,
  strokeWidth = 1.8,
}: {
  size?: number;
  strokeWidth?: number;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <path
        d="M10 3.5v1.8M5.5 9.1a4.5 4.5 0 0 1 9 0v3.8l1.3 1.8H4.2l1.3-1.8V9.1Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth={strokeWidth}
      />
      <path
        d="M8.1 16.2a2 2 0 0 0 3.8 0"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth={strokeWidth}
      />
    </svg>
  );
}
