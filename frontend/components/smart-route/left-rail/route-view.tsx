"use client";

import {
  Fragment,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import type { MapboxSearchSuggestion } from "@/lib/mapbox-search";
import type { LiveFeedIncident } from "@/types/api";
import { useDestinationSearch } from "@/lib/use-destination-search";
import { buildRouteReasoningInsights } from "./live-data";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import {
  BusChip,
  LocationPin,
  RouteBullet,
  RouteBulletGroup,
  StepIcon,
} from "./atoms";
import { LINE_COLORS } from "./types";
import type {
  Alternative,
  Arrival,
  Direction,
  NearbyGroupedArrival,
  NearbyTransitGroup,
  NetworkHealth,
  RouteDetailStep,
  RoutePlan,
  RouteReasoningInsight,
  RouteRailStatus,
  RouteStep,
  RouteStripSegment,
  ServiceAlert,
  Station,
} from "./types";
import type { RailSearchProps } from "./left-rail";

type ArrivalFilter = Direction;

type RecommendedRouteDisplay = {
  walkMinutes?: number;
  transfers?: number;
};

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
}) {
  const isPlanning = routeStatus === "thinking";
  const isReady = routeStatus === "result";
  const isError = routeStatus === "error";

  const recommended = useMemo(
    () => (isReady ? recommendedCandidateFromPlan(plan) : null),
    [isReady, plan],
  );
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
            <section className="sr-rail-section">
              <RecommendedRouteCard
                candidate={recommended}
                plan={plan}
                destination={search?.inputValue}
              />
              {plan.alternatives.length > 0 && (
                <AlternateRoutesCollapsible
                  alternatives={plan.alternatives}
                  onSelectAlternative={onSelectAlternative}
                />
              )}
            </section>
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

function cleanDestinationDraft(value: string) {
  return value
    .replace(/\s+/g, " ")
    .replace(/\s+,/g, ",")
    .replace(/,{2,}/g, ",")
    .trimStart();
}

function cleanDestinationSubmit(value: string) {
  return cleanDestinationDraft(value).trim();
}

function DestinationInput({
  search,
  onDemoSubmit,
}: {
  search?: RailSearchProps;
  onDemoSubmit: (query: string) => void;
}) {
  const [localValue, setLocalValue] = useState("");
  const [focused, setFocused] = useState(false);
  const controlledSearch = search ?? null;
  const wired = controlledSearch !== null;
  const value = controlledSearch ? controlledSearch.inputValue : localValue;
  const displayValue = cleanDestinationDraft(value);

  const destinationSearch = useDestinationSearch({
    inputValue: controlledSearch ? controlledSearch.inputValue : "",
    enabled: wired && focused,
    isLoading: search?.isLoading ?? false,
  });
  const {
    suggestions,
    highlightedIndex,
    setHighlightedIndex,
    choose,
    isResolving,
    clearSuggestions,
    markInputEdited,
    markSelectedLabel,
    resetSession,
  } = destinationSearch;

  function setValue(next: string) {
    const cleaned = cleanDestinationDraft(next);
    if (controlledSearch) {
      markInputEdited();
      controlledSearch.onInputChange(cleaned);
      return;
    }
    setLocalValue(cleaned);
  }

  async function chooseSuggestion(suggestion: MapboxSearchSuggestion) {
    const selection = await choose(suggestion);
    const label = cleanDestinationSubmit(selection?.label ?? suggestion.label);
    search?.onInputChange(label);
    clearSuggestions();
    resetSession();
    setFocused(false);
    search?.onSubmit(label, selection ?? null);
  }

  function submitSearch() {
    const query = cleanDestinationSubmit(value);
    if (!query) return;
    clearSuggestions();
    resetSession();
    setFocused(false);
    markSelectedLabel(query);
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    if (controlledSearch) controlledSearch.onSubmit(query, null);
    else onDemoSubmit(query);
  }

  function clearSearch() {
    clearSuggestions();
    resetSession();
    if (controlledSearch) {
      if (controlledSearch.hasActiveRoute) controlledSearch.onClear();
      else controlledSearch.onInputChange("");
      return;
    }
    setLocalValue("");
  }

  const busy = Boolean(search?.isLoading || isResolving);

  return (
    <section className="sr-rail-section sr-route-search">
      <h1 className="sr-rail-title">Where to?</h1>
      <form
        className="sr-input-group"
        onSubmit={(event) => {
          event.preventDefault();
          submitSearch();
        }}
      >
        <Search size={18} strokeWidth={1.8} aria-hidden="true" />
        <input
          value={displayValue}
          onChange={(event) => setValue(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => window.setTimeout(() => setFocused(false), 140)}
          onKeyDown={(event) => {
            if (!wired || suggestions.length === 0) return;
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setHighlightedIndex((highlightedIndex + 1) % suggestions.length);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setHighlightedIndex(
                highlightedIndex === 0
                  ? suggestions.length - 1
                  : highlightedIndex - 1,
              );
            } else if (event.key === "Enter" && suggestions[highlightedIndex]) {
              event.preventDefault();
              void chooseSuggestion(suggestions[highlightedIndex]);
            } else if (event.key === "Escape") {
              clearSuggestions();
            }
          }}
          placeholder="Search destination or address"
          autoComplete="off"
          disabled={busy}
          title={displayValue || undefined}
        />
        {busy && (
          <Loader2
            className="sr-input-spinner"
            size={16}
            strokeWidth={1.9}
            aria-hidden="true"
          />
        )}
        {!busy && value && (
          <button
            type="button"
            className="sr-icon-button"
            onClick={clearSearch}
            aria-label="Clear destination"
          >
            <X size={18} strokeWidth={1.8} aria-hidden="true" />
          </button>
        )}
      </form>

      {wired && focused && suggestions.length > 0 && (
        <div className="sr-search-popover" role="listbox">
          {suggestions.map((suggestion, index) => (
            <button
              key={suggestion.id}
              type="button"
              role="option"
              aria-selected={index === highlightedIndex}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setHighlightedIndex(index)}
              onClick={() => void chooseSuggestion(suggestion)}
            >
              <span>{suggestion.label.split(",")[0]?.trim() || suggestion.label}</span>
              <small>{suggestion.label}</small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function RoutePlanningReasoning({
  destination,
  insights,
}: {
  destination: string;
  insights: RouteReasoningInsight[];
}) {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt);
    }, 250);
    return () => window.clearInterval(timer);
  }, []);

  // Evaluation insights surface one at a time: the first lands immediately,
  // the rest pace in. Every line is backed by a real fact — missing facts
  // simply never queue a line.
  const revealCount = Math.min(
    insights.length,
    1 + Math.floor(elapsedMs / 1_400),
    5,
  );
  const visibleLines = insights.slice(0, revealCount);
  const cleanedDestination = cleanDestinationSubmit(destination);
  return (
    <>
      <Reasoning className="sr-reasoning" isStreaming>
        <ReasoningTrigger className="sr-reasoning__trigger">
          <span className="sr-reasoning__status">
            {/* The rail's own "checking live status" glyph — the same icon
               arrival rows use — pulsing while planning genuinely runs. */}
            <span
              className="sr-prediction-status"
              data-state="fresh"
              data-pulse="true"
              aria-hidden="true"
            >
              <PredictionSignalIcon state="fresh" />
            </span>
            <Shimmer as="span" duration={2.2}>
              Finding routes...
            </Shimmer>
          </span>
        </ReasoningTrigger>
        <ReasoningContent className="sr-reasoning__content">
          {cleanedDestination ? (
            <span className="sr-reasoning-destination" title={cleanedDestination}>
              {cleanedDestination}
            </span>
          ) : null}
          <ol className="sr-reasoning-lines">
            <AnimatePresence initial={false}>
              {visibleLines.map((insight, index) => {
                const isLatest = index === visibleLines.length - 1;
                return (
                  <motion.li
                    key={insight.id}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -3 }}
                    transition={{ duration: 0.2, ease: "easeOut" }}
                    data-age={isLatest ? "current" : "older"}
                  >
                    {isLatest ? (
                      <Shimmer as="span" duration={2.6}>
                        {insight.text}
                      </Shimmer>
                    ) : (
                      insight.text
                    )}
                  </motion.li>
                );
              })}
            </AnimatePresence>
          </ol>
        </ReasoningContent>
      </Reasoning>
    </>
  );
}

function CandidateStatusBadge({
  status,
}: {
  status: "winner" | "selected";
}) {
  const label = status === "winner" ? "Recommended" : "Selected";

  return <span className="sr-status-badge">{label}</span>;
}

function RecommendedRouteCard({
  candidate,
  plan,
  destination,
}: {
  candidate: RecommendedRouteDisplay;
  plan: RoutePlan;
  destination?: string;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const transfers = plan.transferCount ?? candidate.transfers ?? 0;
  const hasLiveDeparture = Boolean(plan.detailSteps?.some((step) => step.live));
  const hasDetails = (plan.detailSteps?.length ?? 0) > 0;
  const timing = [
    plan.leaveByLabel
      ? plan.leaveByLabel === "now"
        ? "Leave now"
        : `Leave by ${plan.leaveByLabel}`
      : null,
    plan.eta && plan.eta !== "Live" ? `${plan.eta} ETA` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const meta = [
    `${transfers} transfer${transfers === 1 ? "" : "s"}`,
    typeof candidate.walkMinutes === "number"
      ? `${candidate.walkMinutes} min walk`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article className="sr-recommended-route smart-route-liquid-card">
      <div className="sr-recommended-route__top">
        <CandidateStatusBadge
          status={plan.isAlternativeRoute ? "selected" : "winner"}
        />
        {hasLiveDeparture && (
          <PredictionStatus predictionType="live" predictionFreshness="fresh" />
        )}
      </div>
      <strong className="sr-recommended-route__duration">
        {formatDurationLabel(plan.totalTime)}
      </strong>
      {timing && (
        <span className="sr-recommended-route__timing">{timing}</span>
      )}
      {plan.strip && plan.strip.length > 0 && (
        <RouteStepStrip segments={plan.strip} />
      )}
      <p>{plan.rationale || "Best available route."}</p>
      <div className="sr-recommended-route__footer">
        <span>{meta}</span>
        {hasDetails && (
          <button
            type="button"
            className="sr-details-toggle"
            aria-expanded={detailsOpen}
            onClick={() => setDetailsOpen((value) => !value)}
          >
            See details
            <ChevronDown size={15} strokeWidth={1.8} aria-hidden="true" />
          </button>
        )}
      </div>
      {hasDetails && (
        <div className="sr-details" data-open={detailsOpen ? "true" : "false"}>
          <div>
            <RouteDetailsChain
              steps={plan.detailSteps ?? []}
              destination={destination}
            />
          </div>
        </div>
      )}
    </article>
  );
}

/* Compact Apple Maps-style route strip: walk chips and route badges joined
   by small pointers. A summary — the full instructions live in details. */
function RouteStepStrip({ segments }: { segments: RouteStripSegment[] }) {
  return (
    <span className="sr-route-strip" aria-label="Route sequence">
      {segments.map((segment, index) => (
        <Fragment key={index}>
          {index > 0 && (
            <ChevronRight
              className="sr-route-strip__sep"
              size={11}
              strokeWidth={2.4}
              aria-hidden="true"
            />
          )}
          {segment.kind === "walk" ? (
            <span
              className="sr-route-strip__walk"
              title={
                typeof segment.minutes === "number"
                  ? `Walk ${segment.minutes} min`
                  : "Walk"
              }
            >
              <StepIcon type="walk" size={16} />
            </span>
          ) : (
            <span className="sr-route-strip__ride">
              {segment.mode === "bus" ? (
                <BusChip route={segment.routeId} />
              ) : (
                <RouteBullet line={segment.routeId} size={20} />
              )}
              <StepIcon type={segment.mode === "bus" ? "bus" : "ride"} size={16} />
            </span>
          )}
        </Fragment>
      ))}
    </span>
  );
}

/* Full Apple Maps-style step chain: Start → walk → board → ride (route-
   colored connector, stop count, transfer hand-off) → walk → Arrive. */
function RouteDetailsChain({
  steps,
  destination,
}: {
  steps: RouteDetailStep[];
  destination?: string;
}) {
  const cleanedDestination = destination?.trim();

  return (
    <ol className="sr-detail-chain" aria-label="Route directions">
      <li className="sr-detail-step">
        <span className="sr-detail-step__icon">
          <LocationPin tone="start" size={20} />
        </span>
        <span className="sr-detail-step__copy">
          <strong>Start</strong>
          <small>Your location</small>
        </span>
      </li>
      {steps.map((step, index) => {
        if (step.kind === "ride") {
          const lineColor =
            step.mode === "bus"
              ? "#38445c"
              : (step.routeId && LINE_COLORS[step.routeId]) ||
                "var(--sr-rule-bright)";
          return (
            <li key={index} className="sr-detail-ride">
              <span
                className="sr-detail-ride__line"
                style={{ background: lineColor }}
                aria-hidden="true"
              />
              <span className="sr-detail-ride__copy">
                {step.fromStop && <strong>{step.fromStop}</strong>}
                {step.rideMeta && (
                  <span className="sr-detail-ride__meta">{step.rideMeta}</span>
                )}
                {step.toStop && <strong>{step.toStop}</strong>}
                {step.transferTo && (
                  <small className="sr-detail-ride__transfer">
                    <StepIcon type="transfer" />
                    Transfer to the
                    <span className="sr-line-token">
                      {step.transferMode === "bus" ? (
                        <BusChip route={step.transferTo} />
                      ) : (
                        <RouteBullet line={step.transferTo} size={14} />
                      )}
                    </span>
                    {step.transferMode === "bus" ? "bus" : "train"}
                  </small>
                )}
              </span>
            </li>
          );
        }
        return (
          <li key={index} className="sr-detail-step">
            <span className="sr-detail-step__icon">
              {step.kind === "board" && step.routeId ? (
                <span className="sr-detail-step__vehicle">
                  {step.mode === "bus" ? (
                    <>
                      <BusChip route={step.routeId} />
                      <StepIcon type="bus" />
                    </>
                  ) : (
                    <>
                      <RouteBullet line={step.routeId} size={22} />
                      <StepIcon type="ride" />
                    </>
                  )}
                </span>
              ) : (
                <StepIcon type="walk" />
              )}
            </span>
            <span className="sr-detail-step__copy">
              <strong>{step.title}</strong>
              {step.subtitle && <small>{step.subtitle}</small>}
              {step.note && (
                <small className="sr-detail-step__note">
                  {step.note}
                  {step.live && (
                    <PredictionStatus
                      predictionType="live"
                      predictionFreshness="fresh"
                    />
                  )}
                </small>
              )}
            </span>
          </li>
        );
      })}
      <li className="sr-detail-step">
        <span className="sr-detail-step__icon">
          <LocationPin tone="arrive" size={20} />
        </span>
        <span className="sr-detail-step__copy">
          <strong>Arrive</strong>
          {cleanedDestination && <small>{cleanedDestination}</small>}
        </span>
      </li>
    </ol>
  );
}

/* "86 min" → "1 hr 26 min"; strings without minutes pass through. */
function formatDurationLabel(totalTime: string) {
  const minutes = parseMinutes(totalTime);
  if (typeof minutes !== "number") return totalTime;
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours} hr ${rest} min` : `${hours} hr`;
}

function AlternateRoutesCollapsible({
  alternatives,
  onSelectAlternative,
}: {
  alternatives: Alternative[];
  onSelectAlternative?: (candidateId: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="sr-alternates">
      <button
        type="button"
        className="sr-alternates__trigger"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span>
          See {alternatives.length} alternate route
          {alternatives.length === 1 ? "" : "s"}
        </span>
        <ChevronDown size={17} strokeWidth={1.8} aria-hidden="true" />
      </button>
      <div className="sr-alternates__content" data-open={open ? "true" : "false"}>
        <div>
          <ul className="sr-alt-list" aria-label="Alternate routes">
            {alternatives.map((alternative, index) => (
              <AlternateRouteCard
                key={alternative.id ?? `${alternative.line}-${index}`}
                alternative={alternative}
                onSelectAlternative={onSelectAlternative}
              />
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

/* Apple Maps-style option card, at rail scale: big time, live departure /
   ETA line, boarding → alighting path, one passenger-facing reason it lost,
   and a compact Use action. Selecting reuses the candidate's precomputed
   analysis — nothing replans. */
function AlternateRouteCard({
  alternative,
  onSelectAlternative,
}: {
  alternative: Alternative;
  onSelectAlternative?: (candidateId: string) => void;
}) {
  const canUse = Boolean(alternative.id && onSelectAlternative);
  const reason = alternative.reason?.trim();
  const path =
    alternative.fromStop && alternative.toStop
      ? `${alternative.fromStop} → ${alternative.toStop}`
      : alternative.dest;
  const leaves = alternative.leavesLabel
    ? `Leaves ${alternative.leavesLabel}`
    : alternative.arriveLabel
      ? `Arrives ${alternative.arriveLabel}`
      : null;

  return (
    <li className="sr-alt-card smart-route-liquid-card smart-route-liquid-card--secondary">
      <div className="sr-alt-card__body">
        <div className="sr-alt-card__head">
          <strong className="sr-alt-card__duration">
            {typeof alternative.totalMinutes === "number"
              ? formatDurationLabel(`${alternative.totalMinutes} min`)
              : "Live"}
          </strong>
          {leaves && <span className="sr-alt-card__leaves">{leaves}</span>}
        </div>
        {alternative.strip && alternative.strip.length > 0 ? (
          <RouteStepStrip segments={alternative.strip} />
        ) : (
          path && <span className="sr-alt-card__path">{path}</span>
        )}
        {reason && <span className="sr-alt-card__reason">{reason}</span>}
      </div>
      {canUse && (
        <button
          type="button"
          className="sr-use-button"
          onClick={() => onSelectAlternative?.(alternative.id!)}
          aria-label={`Use this route instead: ${path || alternative.dest}`}
        >
          Use
        </button>
      )}
    </li>
  );
}

function NearbyTransitPanel({
  station: _station,
  arrivals: _arrivals,
  nearbyTransitGroups,
  nearbyBusArrivals,
  way,
  onWayChange,
}: {
  station: Station;
  arrivals: Arrival[];
  nearbyTransitGroups: NearbyTransitGroup[];
  nearbyBusArrivals: Arrival[];
  way: ArrivalFilter;
  onWayChange: (next: ArrivalFilter) => void;
}) {
  const groups = useMemo(
    () =>
      nearbyTransitGroups
        .map((group) => ({
          ...group,
          arrivals: group.arrivals.filter(
            (arrival) =>
              arrival.direction === way || arrival.direction === "unknown",
          ),
        }))
        .filter((group) => group.arrivals.length > 0),
    [nearbyTransitGroups, way],
  );
  const busRows = nearbyBusArrivals;
  const isEmpty = groups.length === 0 && busRows.length === 0;

  return (
    <section className="sr-nearby sr-rail-section">
      <SectionHeader
        title="Nearby transit"
        meta={
          <span className="sr-inline-meta">
            Updated just now
            <RefreshCw size={13} strokeWidth={1.8} aria-hidden="true" />
          </span>
        }
      />
      <div
        className="sr-toggle-group"
        data-way={way}
        role="radiogroup"
        aria-label="Arrival direction"
      >
        <span className="sr-toggle-pill" aria-hidden="true" />
        {(
          [
            ["uptown", "Uptown"],
            ["downtown", "Downtown"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={way === value}
            data-active={way === value ? "true" : "false"}
            onClick={() => onWayChange(value as ArrivalFilter)}
          >
            <span>{label}</span>
          </button>
        ))}
      </div>
      <div className="sr-nearby-scroll">
        <NearbyStationGroupList groups={groups} />
        {busRows.length > 0 && (
          <section className="sr-nearby-buses" aria-label="Nearby buses">
            <h3 className="sr-nearby-subhead">Nearby buses</h3>
            <ul className="sr-arrival-list sr-arrival-list--buses">
              <AnimatePresence initial={false}>
                {busRows.map((arrival) => (
                  <ArrivalRow key={arrival.id} arrival={arrival} />
                ))}
              </AnimatePresence>
            </ul>
          </section>
        )}
        {isEmpty && (
          <div className="sr-empty-row">
            <strong>No {way} subway arrivals nearby</strong>
            <small>Try {way === "uptown" ? "Downtown" : "Uptown"} or refresh live data.</small>
          </div>
        )}
      </div>
    </section>
  );
}

function NearbyStationGroupList({
  groups,
}: {
  groups: NearbyTransitGroup[];
}) {
  return (
    <div className="sr-station-group-list">
      <AnimatePresence initial={false}>
        {groups.map((group) => (
          <NearbyStationGroup key={group.id} group={group} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function NearbyStationGroup({
  group,
}: {
  group: NearbyTransitGroup;
}) {
  return (
    <motion.article
      className="sr-station-group"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <StationGroupHeader group={group} />
      <ul className="sr-station-arrival-list">
        <AnimatePresence initial={false}>
          {group.arrivals.map((arrival) => (
            <StationArrivalRow key={arrival.id} arrival={arrival} />
          ))}
        </AnimatePresence>
      </ul>
    </motion.article>
  );
}

function StationGroupHeader({
  group,
}: {
  group: NearbyTransitGroup;
}) {
  const meta = [
    typeof group.walkMinutes === "number"
      ? `${group.walkMinutes} min walk`
      : null,
    typeof group.distanceMiles === "number"
      ? `${group.distanceMiles.toFixed(1)} mi`
      : null,
  ].filter(Boolean);

  return (
    <header className="sr-station-header">
      <span className="sr-station-header__title">
        <strong>{group.name}</strong>
        {meta.length > 0 && (
          <span className="sr-station-header__walk">{meta.join(" · ")}</span>
        )}
      </span>
      <RouteBulletGroup lines={group.routeIds} size={19} limit={6} />
    </header>
  );
}

function StationArrivalRow({
  arrival,
}: {
  arrival: NearbyGroupedArrival;
}) {
  const routeId = arrival.routeIds[0] ?? "";
  const details = [arrival.servicePattern, arrival.via].filter(Boolean);

  return (
    <motion.li
      className="sr-station-arrival-row"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <span className="sr-station-arrival-row__media">
        <RouteBullet line={routeId} size={28} />
      </span>
      <span className="sr-station-arrival-row__copy">
        <strong>{arrival.destination}</strong>
        {details.length > 0 && <small>{details.join(" · ")}</small>}
      </span>
      <span className="sr-station-arrival-row__time">
        <strong>{formatArrivalMinutes(arrival.arrivalMinutes, "Soon")}</strong>
        <PredictionStatus
          predictionType={arrival.predictionType}
          predictionFreshness={arrival.predictionFreshness}
          alertSeverity={arrival.alertSeverity}
        />
      </span>
    </motion.li>
  );
}

function ArrivalRow({
  arrival,
}: {
  arrival: Arrival;
}) {
  const routeId = arrival.routeIds[0] ?? arrival.line;
  const details = [
    arrival.servicePattern,
    arrival.stopName,
    typeof arrival.walkMinutes === "number"
      ? `${arrival.walkMinutes} min walk`
      : null,
  ].filter(Boolean);

  return (
    <motion.li
      className="sr-arrival-row"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <span className="sr-arrival-row__media">
        {arrival.mode === "bus" ? (
          <BusChip route={routeId} />
        ) : (
          <RouteBullet line={routeId} size={30} />
        )}
      </span>
      <span className="sr-arrival-row__copy">
        <strong>{arrival.destination}</strong>
        {details.length > 0 && <small>{details.join(" · ")}</small>}
      </span>
      <span className="sr-arrival-row__time">
        <strong>{formatArrivalMinutes(arrival.arrivalMinutes, arrival.label)}</strong>
        <PredictionStatus
          predictionType={arrival.predictionType}
          predictionFreshness={arrival.predictionFreshness}
          alertSeverity={arrival.alertSeverity}
        />
      </span>
    </motion.li>
  );
}

function PredictionStatus({
  predictionType,
  predictionFreshness,
  alertSeverity,
}: {
  predictionType?: Arrival["predictionType"];
  predictionFreshness?: Arrival["predictionFreshness"];
  alertSeverity?: Arrival["alertSeverity"];
}) {
  const state =
    alertSeverity && alertSeverity !== "none"
      ? "warning"
      : predictionType === "scheduled" || predictionFreshness === "scheduled"
        ? "scheduled"
        : predictionFreshness === "stale"
          ? "stale"
          : "fresh";
  const label =
    state === "warning"
      ? "Affected by service alert"
      : state === "scheduled"
        ? "Scheduled estimate"
        : state === "stale"
          ? "Older live arrival prediction"
          : "Live arrival prediction";

  return (
    <span
      className="sr-prediction-status"
      data-state={state}
      aria-label={label}
      title={label}
    >
      <PredictionSignalIcon state={state} />
    </span>
  );
}

/* Angled broadcast arc: dot + quarter-arcs radiating up-and-right. Fresh
   live predictions show both arcs at full strength, stale dims the outer
   arc, scheduled estimates are the dot alone. */
function PredictionSignalIcon({
  state,
}: {
  state: "fresh" | "stale" | "scheduled" | "warning";
}) {
  const hasArcs = state !== "scheduled";
  const outerOpacity = state === "stale" ? 0.35 : 1;
  return (
    <svg
      className="sr-signal-icon"
      viewBox="0 0 24 24"
      width={16}
      height={16}
      fill="none"
      aria-hidden="true"
    >
      <g transform="rotate(-45 12 12)">
        <circle cx="12" cy="19" r="1.4" fill="currentColor" />
        {hasArcs && (
          <path
            d="M8.4 15.6a5 5 0 0 1 7.2 0"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        )}
        {hasArcs && (
          <path
            d="M5.3 12.5a9 9 0 0 1 13.4 0"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            opacity={outerOpacity}
          />
        )}
      </g>
    </svg>
  );
}

function RouteErrorPanel({
  onRetry,
  onClear,
}: {
  onRetry: () => void;
  onClear: () => void;
}) {
  return (
    <section className="sr-rail-section">
      <div className="sr-error-panel">
        <AlertTriangle size={20} strokeWidth={1.8} aria-hidden="true" />
        <div>
          <strong>No route found.</strong>
          <p>Try a more specific station, address, or neighborhood.</p>
        </div>
      </div>
      <div className="sr-error-actions">
        <button type="button" onClick={onRetry}>
          Try again
        </button>
        <button type="button" onClick={onClear}>
          Cancel
        </button>
      </div>
    </section>
  );
}

function SectionHeader({
  title,
  meta,
}: {
  title: string;
  meta?: ReactNode;
}) {
  return (
    <div className="sr-section-header">
      <h2>{title}</h2>
      {meta && <span>{meta}</span>}
    </div>
  );
}

function recommendedCandidateFromPlan(plan: RoutePlan): RecommendedRouteDisplay {
  const timing = timingFromSteps(plan.steps);
  return {
    walkMinutes: timing.walkMinutes,
    transfers: timing.transfers,
  };
}

function timingFromSteps(steps: RouteStep[]) {
  let walkMinutes = 0;
  let boardCount = 0;
  for (const step of steps) {
    const mins = parseMinutes(step.duration) ?? 0;
    if (step.type === "walk" || step.type === "exit" || step.type === "destination") {
      walkMinutes += mins;
    } else {
      // A transfer is a vehicle change: count every transit boarding (each
      // transit leg carries a line), never walking segments.
      if (step.line) boardCount += 1;
    }
  }
  return {
    walkMinutes: walkMinutes || undefined,
    transfers: Math.max(0, boardCount - 1),
  };
}

function parseMinutes(value: string | undefined) {
  if (!value) return undefined;
  const match = value.match(/-?\d+/);
  if (!match) return undefined;
  return Math.max(0, Number(match[0]));
}

function formatArrivalMinutes(minutes: number[], fallback: string) {
  if (minutes.length === 0) return fallback;
  if (minutes.length === 1) {
    const only = minutes[0];
    if (only <= 0) return "Now";
    if (only === 1) return "1 min";
    return `${only} min`;
  }
  const rendered = minutes
    .slice(0, 3)
    .map((mins) => (mins <= 0 ? "Now" : String(mins)));
  return `${rendered.join(", ")} min`.replace("Now min", "Now");
}
