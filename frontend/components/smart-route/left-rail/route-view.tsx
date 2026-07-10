"use client";

import {
  Fragment,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import {
  AnimatePresence,
  LayoutGroup,
  motion,
  useReducedMotion,
} from "motion/react";
import {
  AlertTriangle,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Mic,
  RefreshCw,
  X,
} from "lucide-react";
import type { MapboxSearchSuggestion } from "@/lib/mapbox-search";
import type { LiveFeedIncident } from "@/types/api";
import { useDestinationSearch } from "@/lib/use-destination-search";
import {
  DestinationSuggestions,
  destinationSuggestionOptionId,
} from "./destination-suggestions";
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
  TransitText,
} from "./atoms";
import { ArrivalCountdown, InlineArrivalCountdown } from "./arrival-countdown";
import { SpiralFillLoader } from "@/components/smart-route/ui/spiral-fill-loader";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
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
                    destination={search?.inputValue}
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

    // The idle/planning/results/error switch above uses
    // `mode="wait"`, so on the "entered result" trigger the card can
    // mount a beat after this effect fires (once the previous phase's
    // exit animation finishes) — poll a few frames for the ref instead
    // of assuming it is already attached, the way the "plan changed"
    // trigger safely can. Once found, hold the two requestAnimationFrames
    // the design calls for so AnimatePresence/layout have settled before
    // measuring.
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

  // getBoundingClientRect delta + current scrollTop, rather than
  // offsetTop, since offsetParent chains through positioned ancestors
  // (the sticky search block, LayoutGroup wrappers) here.
  const cardRect = card.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  const cardOffsetTopWithinScroller =
    cardRect.top - scrollerRect.top + scroller.scrollTop;

  const searchBlock = scroller.querySelector<HTMLElement>(".sr-route-search");
  const stickySearchHeight = searchBlock
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

type DestinationInputActionState =
  | "empty"
  | "submit"
  | "stop"
  | "finalizing"
  | "clear";

type SpeechRecognitionAlternativeLike = {
  transcript: string;
};

type SpeechRecognitionResultLike = {
  readonly length: number;
  readonly isFinal: boolean;
  item(index: number): SpeechRecognitionAlternativeLike;
  [index: number]: SpeechRecognitionAlternativeLike;
};

type SpeechRecognitionResultListLike = {
  readonly length: number;
  item(index: number): SpeechRecognitionResultLike;
  [index: number]: SpeechRecognitionResultLike;
};

type SpeechRecognitionEventLike = Event & {
  results: SpeechRecognitionResultListLike;
};

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type SpeechRecognitionWindow = Window &
  typeof globalThis & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const speechWindow = window as SpeechRecognitionWindow;
  return (
    speechWindow.SpeechRecognition ??
    speechWindow.webkitSpeechRecognition ??
    null
  );
}

function DestinationInput({
  search,
  onDemoSubmit,
  onFocusChange,
}: {
  search?: RailSearchProps;
  onDemoSubmit: (query: string) => void;
  onFocusChange?: (focused: boolean) => void;
}) {
  const [localValue, setLocalValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [speechRecognitionCtor, setSpeechRecognitionCtor] =
    useState<SpeechRecognitionConstructor | null>(null);
  const [isListening, setIsListening] = useState(false);
  const speechRecognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const suggestionsId = useId();
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

  useEffect(() => {
    const supportCheck = window.setTimeout(() => {
      const recognitionCtor = getSpeechRecognitionConstructor();
      setSpeechRecognitionCtor(() => recognitionCtor);
    }, 0);
    return () => {
      window.clearTimeout(supportCheck);
      speechRecognitionRef.current?.abort();
      speechRecognitionRef.current = null;
    };
  }, []);

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
    onFocusChange?.(false);
    search?.onSubmit(label, selection ?? null);
  }

  function submitSearch() {
    const query = cleanDestinationSubmit(value);
    if (!query) return;
    clearSuggestions();
    resetSession();
    setFocused(false);
    onFocusChange?.(false);
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
      controlledSearch.onClear();
      return;
    }
    setLocalValue("");
  }

  function stopRoutePlanning() {
    clearSuggestions();
    resetSession();
    setFocused(false);
    onFocusChange?.(false);
    controlledSearch?.onCancelPlanning();
  }

  function startVoiceInput() {
    if (!speechRecognitionCtor || isListening) {
      speechRecognitionRef.current?.stop();
      return;
    }

    const recognition = new speechRecognitionCtor();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const transcriptParts: string[] = [];
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index] ?? event.results.item(index);
        const alternative = result[0] ?? result.item(0);
        if (alternative?.transcript)
          transcriptParts.push(alternative.transcript);
      }
      const transcript = cleanDestinationDraft(transcriptParts.join(" "));
      if (!transcript) return;
      setValue(transcript);
      setFocused(true);
      onFocusChange?.(true);
    };
    recognition.onerror = () => {
      setIsListening(false);
      speechRecognitionRef.current = null;
    };
    recognition.onend = () => {
      setIsListening(false);
      speechRecognitionRef.current = null;
    };

    speechRecognitionRef.current = recognition;
    setIsListening(true);
    try {
      recognition.start();
    } catch {
      setIsListening(false);
      speechRecognitionRef.current = null;
    }
  }

  const planningPhase = search?.planningPhase ?? "idle";
  const isPlanning = planningPhase !== "idle" || Boolean(search?.isLoading);
  const busy = Boolean(isPlanning || isResolving);
  const hasSearchContent = cleanDestinationSubmit(value).length > 0;
  const showClearAction = Boolean(controlledSearch?.hasActiveRoute && !busy);
  const actionState: DestinationInputActionState = showClearAction
    ? "clear"
    : planningPhase === "cancellable"
      ? "stop"
      : planningPhase === "finalizing" || isResolving || search?.isLoading
        ? "finalizing"
        : hasSearchContent
          ? "submit"
          : "empty";
  const canUseVoice =
    speechRecognitionCtor !== null &&
    !busy &&
    !showClearAction &&
    actionState !== "clear";
  const actionDisabled =
    actionState === "empty" || actionState === "finalizing";
  const actionLabel =
    actionState === "clear"
      ? "Clear route"
      : actionState === "stop"
        ? "Stop route planning"
        : actionState === "finalizing"
          ? "Finalizing route"
          : "Search route";
  const actionFilled =
    actionState === "submit" ||
    actionState === "stop" ||
    actionState === "clear";
  const suggestionsOpen = wired && focused && suggestions.length > 0;

  return (
    <section className="sr-rail-section sr-route-search">
      <form
        className="sr-input-group"
        onSubmit={(event) => {
          event.preventDefault();
          if (actionState === "submit") submitSearch();
        }}
      >
        <input
          aria-label="Search destination or address"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={suggestionsOpen}
          aria-controls={suggestionsOpen ? suggestionsId : undefined}
          aria-activedescendant={
            suggestionsOpen
              ? destinationSuggestionOptionId(suggestionsId, highlightedIndex)
              : undefined
          }
          value={displayValue}
          onChange={(event) => setValue(event.target.value)}
          onFocus={() => {
            setFocused(true);
            onFocusChange?.(true);
          }}
          onBlur={() =>
            window.setTimeout(() => {
              setFocused(false);
              onFocusChange?.(false);
            }, 140)
          }
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
          placeholder="Where are we headed?"
          autoComplete="off"
          disabled={busy}
          title={displayValue || undefined}
        />
        {canUseVoice && (
          <button
            type="button"
            className="sr-input-voice"
            aria-label={
              isListening ? "Listening for destination" : "Use voice input"
            }
            data-listening={isListening ? "true" : "false"}
            onClick={startVoiceInput}
          >
            <Mic size={20} strokeWidth={1.9} aria-hidden="true" />
          </button>
        )}
        <motion.button
          type={actionState === "submit" ? "submit" : "button"}
          className="sr-input-submit"
          aria-label={actionLabel}
          disabled={actionDisabled}
          data-filled={actionFilled ? "true" : "false"}
          data-action-state={actionState}
          onClick={() => {
            if (actionState === "clear") {
              clearSearch();
            } else if (actionState === "stop") {
              stopRoutePlanning();
            }
          }}
          animate={{
            backgroundColor: actionFilled
              ? "rgba(255,255,255,0.96)"
              : "rgba(255,255,255,0.12)",
            color: actionFilled
              ? "rgba(8,12,18,0.96)"
              : "rgba(255,255,255,0.72)",
          }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          whileTap={!actionDisabled ? { scale: 0.96 } : undefined}
        >
          {actionState === "clear" ? (
            <X size={20} strokeWidth={2.1} aria-hidden="true" />
          ) : actionState === "stop" || actionState === "finalizing" ? (
            <span className="sr-input-stop-icon" aria-hidden="true" />
          ) : (
            <ArrowUp size={21} strokeWidth={2.25} aria-hidden="true" />
          )}
        </motion.button>
      </form>

      <DestinationSuggestions
        id={suggestionsId}
        open={suggestionsOpen}
        suggestions={suggestions}
        highlightedIndex={highlightedIndex}
        onHighlight={setHighlightedIndex}
        onSelect={(suggestion) => void chooseSuggestion(suggestion)}
      />
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
            <SpiralFillLoader className="shrink-0" />
            <Shimmer as="span" duration={2.2}>
              Finding routes...
            </Shimmer>
          </span>
        </ReasoningTrigger>
        <ReasoningContent className="sr-reasoning__content">
          {cleanedDestination ? (
            <span
              className="sr-reasoning-destination"
              title={cleanedDestination}
            >
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

function CandidateStatusBadge({ status }: { status: "winner" | "selected" }) {
  const label = status === "winner" ? "Recommended" : "Selected";

  return (
    <span
      className="sr-status-badge"
      data-tone={status === "winner" ? "recommended" : "selected"}
    >
      {label}
    </span>
  );
}

function RecommendedRouteCard({
  candidate,
  plan,
  destination,
  cardRef,
}: {
  candidate: RecommendedRouteDisplay;
  plan: RoutePlan;
  destination?: string;
  cardRef?: RefObject<HTMLElement | null>;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const transfers = plan.transferCount ?? candidate.transfers ?? 0;
  const hasDetails = (plan.detailSteps?.length ?? 0) > 0;
  // Hero line: duration is the LargeTitle, arrival time rides the same
  // baseline row ("24 min · 3:42 PM arrival"), Apple Maps-style. Leave-by
  // gets its own Subheadline line below, only when the backend supplies a
  // departure to plan around.
  const etaLabel =
    plan.eta && plan.eta !== "Live" ? `${plan.eta} arrival` : null;
  const leaveByLabel = plan.leaveByLabel
    ? plan.leaveByLabel === "now"
      ? "Leave now"
      : `Leave by ${plan.leaveByLabel}`
    : null;
  const hasNextDeparture =
    typeof plan.nextDepartureMinutes === "number" &&
    Number.isFinite(plan.nextDepartureMinutes);
  const meta = [
    `${transfers} transfer${transfers === 1 ? "" : "s"}`,
    typeof candidate.walkMinutes === "number"
      ? `${candidate.walkMinutes} min walk`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article
      ref={cardRef}
      className="sr-recommended-route smart-route-liquid-card"
    >
      <div className="sr-recommended-route__top">
        <CandidateStatusBadge
          status={plan.isAlternativeRoute ? "selected" : "winner"}
        />
      </div>
      <div className="sr-recommended-route__hero">
        <strong className="sr-recommended-route__duration">
          {formatDurationLabel(plan.totalTime)}
        </strong>
        {etaLabel && (
          <span className="sr-recommended-route__eta">{etaLabel}</span>
        )}
      </div>
      {leaveByLabel && (
        <span className="sr-recommended-route__leaveby">{leaveByLabel}</span>
      )}
      {hasNextDeparture && (
        <RecommendedNextDeparture
          routeId={plan.pickedLine}
          minutes={plan.nextDepartureMinutes}
        />
      )}
      {plan.strip && plan.strip.length > 0 && (
        <RouteStepStrip segments={plan.strip} />
      )}
      <TypedRouteReasoning text={plan.rationale || "Best available route."} />
      <div className="sr-recommended-route__footer">
        <span>{meta}</span>
        {hasDetails && (
          <button
            type="button"
            className="sr-details-toggle"
            aria-expanded={detailsOpen}
            onClick={() => setDetailsOpen((value) => !value)}
          >
            Details
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

function RecommendedNextDeparture({
  routeId,
  minutes,
}: {
  routeId: string;
  minutes: number | undefined;
}) {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) return null;

  const line = routeId ? routeId.toUpperCase() : "train";
  const value = Math.max(0, Math.round(minutes));

  return (
    <span className="sr-recommended-route__next">
      Next {line}
      {value <= 0 ? (
        " now"
      ) : (
        <>
          {" in "}
          <InlineArrivalCountdown minutes={value} />
        </>
      )}
    </span>
  );
}

function TypedRouteReasoning({ text }: { text: string }) {
  const [visibleText, setVisibleText] = useState("");
  const cleaned = text.trim();

  useEffect(() => {
    let animationFrame = 0;
    let timer: number | undefined;

    if (!cleaned) {
      animationFrame = window.requestAnimationFrame(() => {
        setVisibleText("");
      });
      return () => window.cancelAnimationFrame(animationFrame);
    }

    const motionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const prefersReducedMotion = Boolean(motionQuery?.matches);
    if (prefersReducedMotion || cleaned.length <= 8) {
      animationFrame = window.requestAnimationFrame(() => {
        setVisibleText(cleaned);
      });
      return () => window.cancelAnimationFrame(animationFrame);
    }

    let index = 0;
    const charactersPerTick = cleaned.length > 150 ? 2 : 1;
    const typingDelayMs = cleaned.length > 150 ? 42 : 34;
    animationFrame = window.requestAnimationFrame(() => {
      setVisibleText("");
      timer = window.setInterval(() => {
        index = Math.min(cleaned.length, index + charactersPerTick);
        setVisibleText(cleaned.slice(0, index));
        if (index >= cleaned.length && typeof timer === "number") {
          window.clearInterval(timer);
        }
      }, typingDelayMs);
    });

    return () => {
      window.cancelAnimationFrame(animationFrame);
      if (typeof timer === "number") {
        window.clearInterval(timer);
      }
    };
  }, [cleaned]);

  const isTyping = visibleText.length < cleaned.length;

  return (
    <div
      className="sr-reasoning-inset"
      data-typing={isTyping ? "true" : "false"}
    >
      <p className="sr-ai-reasoning" aria-live="polite">
        <span>
          <TransitText
            text={markRouteTokensForTransitText(visibleText)}
            bulletSize={15}
          />
        </span>
        {isTyping && (
          <span className="sr-ai-reasoning__cursor" aria-hidden="true" />
        )}
      </p>
    </div>
  );
}

const ROUTE_REASON_TOKEN = /\b(6X|7X|SIR|SI|FS|GS|FX|[1-7]|[A-Z])\b/g;

function markRouteTokensForTransitText(text: string) {
  return text.replace(ROUTE_REASON_TOKEN, (token, _match, offset: number) => {
    const routeId = token.toUpperCase();
    if (!SUBWAY_BULLET_ROUTES.has(routeId)) return token;
    if (!isTransitLineContext(text, offset, token.length)) return token;
    return `[${routeId}]`;
  });
}

// Bare digits/letters (e.g. "2" in "2 minutes") are far more common in AI
// reasoning prose than genuine subway line references, so a token is only
// treated as a line reference when nearby words actually talk about a
// train/line — never on the strength of the token alone.
function isTransitLineContext(text: string, offset: number, length: number) {
  const before = text.slice(Math.max(0, offset - 16), offset).toLowerCase();
  const after = text
    .slice(offset + length, offset + length + 18)
    .toLowerCase();
  return (
    /\b(the|take|via|next|board)\s+$/.test(before) ||
    /^\s+(train|line|service|express|local)\b/.test(after)
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
                <RouteBullet line={segment.routeId} size={22} />
              )}
              <StepIcon
                type={segment.mode === "bus" ? "bus" : "ride"}
                size={16}
              />
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
  // Open by default: Apple Maps stacks route options immediately, and the
  // scroll choreography guarantees the recommended card owns the first
  // viewport — open alternates cost nothing above the fold and invite
  // comparison below it. The header row still collapses the section.
  const [open, setOpen] = useState(true);
  const shouldReduceMotion = useReducedMotion();
  const hiddenState = shouldReduceMotion
    ? { opacity: 0 }
    : { opacity: 0, height: 0 };
  const visibleState = shouldReduceMotion
    ? { opacity: 1 }
    : { opacity: 1, height: "auto" };

  return (
    <motion.div className="sr-alternates" layout>
      <button
        type="button"
        className="sr-alternates__trigger"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="sr-alternates__title">Other routes</span>
        <span className="sr-alternates__meta">
          {alternatives.length} route{alternatives.length === 1 ? "" : "s"}
          <ChevronDown size={17} strokeWidth={1.8} aria-hidden="true" />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="sr-alternates__content"
            layout
            initial={hiddenState}
            animate={visibleState}
            exit={hiddenState}
            transition={{
              duration: shouldReduceMotion ? 0.01 : 0.24,
              ease: "easeOut",
            }}
          >
            <ul className="sr-alt-list" aria-label="Alternate routes">
              <AnimatePresence initial={false}>
                {alternatives.map((alternative, index) => (
                  <AlternateRouteCard
                    key={alternative.id ?? `${alternative.line}-${index}`}
                    alternative={alternative}
                    onSelectAlternative={onSelectAlternative}
                  />
                ))}
              </AnimatePresence>
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
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
  const shouldReduceMotion = useReducedMotion();
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
    <motion.li
      className="sr-alt-row"
      layout
      initial={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: 4 }}
      animate={shouldReduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: -4 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <div className="sr-alt-row__body">
        <div className="sr-alt-row__head">
          <strong className="sr-alt-row__duration">
            {typeof alternative.totalMinutes === "number"
              ? formatDurationLabel(`${alternative.totalMinutes} min`)
              : "Live"}
          </strong>
          {leaves && <span className="sr-alt-row__leaves">{leaves}</span>}
        </div>
        {alternative.strip && alternative.strip.length > 0 ? (
          <RouteStepStrip segments={alternative.strip} />
        ) : (
          path && <span className="sr-alt-row__path">{path}</span>
        )}
        {reason && <span className="sr-alt-row__reason">{reason}</span>}
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
    </motion.li>
  );
}

function NearbyTransitPanel({
  station: _station,
  arrivals: _arrivals,
  nearbyTransitGroups,
  nearbyBusArrivals,
  way,
  onWayChange,
  onRequestRailExpand,
}: {
  station: Station;
  arrivals: Arrival[];
  nearbyTransitGroups: NearbyTransitGroup[];
  nearbyBusArrivals: Arrival[];
  way: ArrivalFilter;
  onWayChange: (next: ArrivalFilter) => void;
  onRequestRailExpand?: () => void;
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
            onClick={() => {
              onRequestRailExpand?.();
              onWayChange(value as ArrivalFilter);
            }}
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
            <small>
              Try {way === "uptown" ? "Downtown" : "Uptown"} or refresh live
              data.
            </small>
          </div>
        )}
      </div>
    </section>
  );
}

function NearbyStationGroupList({ groups }: { groups: NearbyTransitGroup[] }) {
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

function NearbyStationGroup({ group }: { group: NearbyTransitGroup }) {
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

function StationGroupHeader({ group }: { group: NearbyTransitGroup }) {
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

function StationArrivalRow({ arrival }: { arrival: NearbyGroupedArrival }) {
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
        <strong>
          <ArrivalCountdown
            minutes={arrival.arrivalMinutes}
            fallback="Soon"
            className="sr-arrival-countdown"
          />
        </strong>
        <PredictionStatus
          predictionType={arrival.predictionType}
          predictionFreshness={arrival.predictionFreshness}
          alertSeverity={arrival.alertSeverity}
        />
      </span>
    </motion.li>
  );
}

function ArrivalRow({ arrival }: { arrival: Arrival }) {
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
        <strong>
          <ArrivalCountdown
            minutes={arrival.arrivalMinutes}
            fallback={arrival.label}
            className="sr-arrival-countdown"
          />
        </strong>
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

function SectionHeader({ title, meta }: { title: string; meta?: ReactNode }) {
  return (
    <div className="sr-section-header">
      <h2>{title}</h2>
      {meta && <span>{meta}</span>}
    </div>
  );
}

function recommendedCandidateFromPlan(
  plan: RoutePlan,
): RecommendedRouteDisplay {
  const timing = timingFromSteps(plan.steps);
  return {
    walkMinutes: timing.walkMinutes,
    transfers: timing.transfers,
  };
}

function routeResultKey(plan: RoutePlan) {
  return [
    plan.isAlternativeRoute ? "selected" : "recommended",
    plan.pickedLine || "walk",
    plan.headsign || "",
    plan.totalTime || "",
  ].join(":");
}

function timingFromSteps(steps: RouteStep[]) {
  let walkMinutes = 0;
  let boardCount = 0;
  for (const step of steps) {
    const mins = parseMinutes(step.duration) ?? 0;
    if (
      step.type === "walk" ||
      step.type === "exit" ||
      step.type === "destination"
    ) {
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
