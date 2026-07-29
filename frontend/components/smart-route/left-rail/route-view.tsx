"use client";

import { useEffect, useMemo, useRef, type RefObject } from "react";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import type { LiveFeedIncident } from "@/types/api";
import { buildRouteReasoningInsights } from "./live-data";
import { recommendedCandidateFromPlan, routeResultKey } from "./route-display-compat";
import { DestinationInput } from "./route-view-actions";
import { RouteErrorPanel, RoutePlanningReasoning } from "./route-view-state";
import { AlternateRoutesCollapsible } from "./route-view-alternatives";
import { RecommendedRouteCard } from "./route-view-itinerary";
import { NearbyTransitPanel } from "./route-view-nearby";
import type {
  Arrival,
  Direction,
  NearbyTransitGroup,
  NetworkHealth,
  RoutePlan,
  RouteRailStatus,
  ServiceAlert,
  Station,
} from "./types";
import type { RailSearchProps } from "./left-rail";

type ArrivalFilter = Direction;

export function RouteView({
  station,
  health: _health,
  arrivals,
  nearbyTransitGroups,
  nearbyBusArrivals,
  alerts,
  incidents,
  plan,
  way,
  onWayChange,
  routeStatus,
  onRouteStatusChange,
  onSelectAlternative,
  search,
  onSearchFocusChange,
  onRequestRailExpand,
}: {
  station: Station;
  health: NetworkHealth;
  arrivals: Arrival[];
  nearbyTransitGroups: NearbyTransitGroup[];
  nearbyBusArrivals: Arrival[];
  alerts: ServiceAlert[];
  incidents?: LiveFeedIncident[];
  plan: RoutePlan;
  way: ArrivalFilter;
  onWayChange: (d: ArrivalFilter) => void;
  routeStatus: RouteRailStatus;
  onRouteStatusChange: (s: RouteRailStatus) => void;
  onSelectAlternative?: (candidateId: string) => void;
  search?: RailSearchProps;
  onSearchFocusChange?: (focused: boolean) => void;
  onRequestRailExpand?: () => void;
}) {
  const isPlanning = routeStatus === "thinking";
  const isReady = routeStatus === "result";
  const isError = routeStatus === "error";
  const recommended = useMemo(
    () => (isReady ? recommendedCandidateFromPlan(plan) : null),
    [isReady, plan],
  );
  const shouldReduceMotion = useReducedMotion();
  const recommendedCardRef = useRef<HTMLElement | null>(null);
  useScrollToRecommendedCard({
    cardRef: recommendedCardRef,
    routeStatus,
    plan,
    shouldReduceMotion,
  });
  // Public evaluation insights for the planning state, derived from the
  // live facts the rail already holds (station access, live arrivals,
  // official alerts, reported incidents). No fact → no line.
  const planningInsights = useMemo(
    () =>
      isPlanning
        ? buildRouteReasoningInsights({
            groups: nearbyTransitGroups,
            busArrivals: nearbyBusArrivals,
            alerts,
            incidents,
          })
        : [],
    [isPlanning, nearbyTransitGroups, nearbyBusArrivals, alerts, incidents],
  );

  return (
    <div className="sr-route-panel">
      <DestinationInput
        search={search}
        onDemoSubmit={() => onRouteStatusChange("thinking")}
        onFocusChange={onSearchFocusChange}
      />

      <AnimatePresence mode="wait" initial={false}>
        {routeStatus === "standby" && (
          <motion.div key="idle" {...CONTENT_PHASE}>
            <NearbyTransitPanel
              station={station}
              arrivals={arrivals}
              nearbyTransitGroups={nearbyTransitGroups}
              nearbyBusArrivals={nearbyBusArrivals}
              way={way}
              onWayChange={onWayChange}
              onRequestRailExpand={onRequestRailExpand}
            />
          </motion.div>
        )}

        {isPlanning && (
          <motion.div key="plan-flow" {...CONTENT_PHASE}>
            <section className="sr-rail-section">
              <RoutePlanningReasoning
                destination={search?.inputValue ?? ""}
                insights={planningInsights}
              />
            </section>
          </motion.div>
        )}

        {isReady && recommended && (
          <motion.div key="results" {...CONTENT_PHASE}>
            <LayoutGroup id="sr-route-results">
              <section className="sr-rail-section">
                <motion.div
                  key={routeResultKey(plan)}
                  layout
                  initial={
                    shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: 6 }
                  }
                  animate={
                    shouldReduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }
                  }
                  transition={{ duration: 0.22, ease: "easeOut" }}
                >
                  <RecommendedRouteCard
                    candidate={recommended}
                    plan={plan}
                    destination={plan.journeyPlaces?.at(-1) ?? search?.inputValue}
                    cardRef={recommendedCardRef}
                  />
                </motion.div>
                {plan.alternatives.length > 0 && (
                  <AlternateRoutesCollapsible
                    alternatives={plan.alternatives}
                    onSelectAlternative={onSelectAlternative}
                  />
                )}
              </section>
            </LayoutGroup>
          </motion.div>
        )}

        {isError && (
          <motion.div key="error" {...CONTENT_PHASE}>
            <RouteErrorPanel
              onRetry={() => {
                if (search?.inputValue.trim()) {
                  search.onSubmit(search.inputValue.trim(), null);
                } else {
                  onRouteStatusChange("standby");
                }
              }}
              onClear={() => {
                search?.onClear();
                onRouteStatusChange("standby");
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* Shared enter/exit for rail content phases (idle / planning / results /
   error): a quiet 4–6px fade so state changes read as data updating in
   place, not a screen transition. */
const CONTENT_PHASE = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4 },
  transition: { duration: 0.2, ease: "easeOut" as const },
};

/* ── Scroll choreography ──────────────────────────────────────────────
   The rail scrolls so the recommended card sits at the top, just under
   the pinned search, whenever (a) route status transitions into "result"
   (a recommendation lands) or (b) the active plan changes while already
   in "result" (the rider tapped "Use" on an alternative). It must never
   fire just because the component re-rendered, or because the rider
   expanded/collapsed alternates or details — so the trigger is a
   usePrevious-style ref comparison, not a plain effect dependency. */
const SCROLL_BREATHING_ROOM = 8;
const SCROLL_MOUNT_POLL_LIMIT = 90; // ~1.5s at 60fps, then silently give up

function useScrollToRecommendedCard({
  cardRef,
  routeStatus,
  plan,
  shouldReduceMotion,
}: {
  cardRef: RefObject<HTMLElement | null>;
  routeStatus: RouteRailStatus;
  plan: RoutePlan;
  shouldReduceMotion: boolean | null;
}) {
  const resultKey = routeStatus === "result" ? routeResultKey(plan) : null;
  const previousRef = useRef<{ status: RouteRailStatus; key: string | null }>({
    status: routeStatus,
    key: resultKey,
  });

  useEffect(() => {
    const previous = previousRef.current;
    const enteredResult =
      routeStatus === "result" && previous.status !== "result";
    const planChangedInResult =
      routeStatus === "result" &&
      previous.status === "result" &&
      resultKey !== previous.key;
    previousRef.current = { status: routeStatus, key: resultKey };

    if (!enteredResult && !planChangedInResult) return;

    // The idle/planning/results/error switch above uses `mode="wait"`, so
    // the card can mount after this effect. Poll a few frames for the ref,
    // then hold two frames so AnimatePresence/layout settle before measuring.
    let frame = 0;
    let attempts = 0;
    const settleThenScroll = () => {
      frame = window.requestAnimationFrame(() => {
        frame = window.requestAnimationFrame(() => {
          scrollRecommendedCardIntoView(cardRef.current, shouldReduceMotion);
        });
      });
    };
    const waitForCard = () => {
      attempts += 1;
      if (cardRef.current) {
        settleThenScroll();
        return;
      }
      if (attempts >= SCROLL_MOUNT_POLL_LIMIT) return;
      frame = window.requestAnimationFrame(waitForCard);
    };

    frame = window.requestAnimationFrame(waitForCard);
    return () => window.cancelAnimationFrame(frame);
  }, [routeStatus, resultKey, cardRef, shouldReduceMotion]);
}

function scrollRecommendedCardIntoView(
  card: HTMLElement | null,
  shouldReduceMotion: boolean | null,
) {
  if (!card) return;
  const scroller = card.closest<HTMLElement>(".sr-rail");
  if (!scroller) return;

  // Use a viewport delta rather than offsetTop because positioned ancestors
  // (LayoutGroup wrappers and the search block) break the offset-parent chain.
  const cardRect = card.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  const cardOffsetTopWithinScroller =
    cardRect.top - scrollerRect.top + scroller.scrollTop;
  // Reserve the search block only when it is actually sticky. Keeping this
  // conditional preserves the keyboard-safe, non-sticky suggestions layout.
  const searchBlock = scroller.querySelector<HTMLElement>(".sr-route-search");
  const stickySearchHeight =
    searchBlock && getComputedStyle(searchBlock).position === "sticky"
      ? searchBlock.getBoundingClientRect().height
      : 0;
  const top = Math.max(
    0,
    cardOffsetTopWithinScroller - (stickySearchHeight + SCROLL_BREATHING_ROOM),
  );

  scroller.scrollTo({
    top,
    behavior: shouldReduceMotion ? "auto" : "smooth",
  });
}
