"use client";

import { Fragment, useEffect, useState, type RefObject } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { BusChip, LocationPin, RouteBullet, StepIcon, TransitText } from "../atoms";
import { InlineArrivalCountdown } from "../arrival-countdown";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import { formatDurationLabel, type RecommendedRouteDisplay } from "./display-compat";
import { PredictionStatus } from "./nearby";
import { LINE_COLORS } from "../types";
import type { RouteDetailStep, RoutePlan, RouteStripSegment } from "../types";

function leaveByCopy(label: string | undefined): string | null {
  if (!label) return null;
  if (label === "now") return "Leave now";
  return `Leave by ${label}`;
}

function recommendedFootMeta(
  plan: RoutePlan,
  candidate: RecommendedRouteDisplay,
): string {
  const transfers = plan.transferCount ?? candidate.transfers ?? 0;
  return [
    `${transfers} transfer${transfers === 1 ? "" : "s"}`,
    candidate.walkMinutes != null && Number.isFinite(candidate.walkMinutes)
      ? `${candidate.walkMinutes} min walk`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function RecommendedRouteHero({
  plan,
  etaLabel,
  leaveByLabel,
  hasNextDeparture,
}: {
  plan: RoutePlan;
  etaLabel: string | null;
  leaveByLabel: string | null;
  hasNextDeparture: boolean;
}) {
  return (
    <>
      <div className="sr-recommended-route__hero">
        <strong className="sr-recommended-route__duration">
          {formatDurationLabel(plan.totalTime)}
        </strong>
        {etaLabel && <span className="sr-recommended-route__eta">{etaLabel}</span>}
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
      {plan.journeyPlaces && plan.journeyPlaces.length > 2 && (
        <p className="sr-recommended-route__journey" aria-label="Journey stops">
          {plan.journeyPlaces.join(" → ")}
        </p>
      )}
      {plan.rationale ? <TypedRouteReasoning text={plan.rationale} /> : null}
    </>
  );
}

export function RecommendedRouteCard({
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
  const hasDetails = (plan.detailSteps?.length ?? 0) > 0;
  const etaLabel = plan.eta && plan.eta !== "Live" ? `${plan.eta} arrival` : null;
  const leaveByLabel = leaveByCopy(plan.leaveByLabel);
  const hasNextDeparture =
    plan.nextDepartureMinutes != null && Number.isFinite(plan.nextDepartureMinutes);
  const meta = recommendedFootMeta(plan, candidate);

  return (
    <article ref={cardRef} className="sr-recommended-route smart-route-liquid-card">
      <div className="sr-recommended-route__top">
        <CandidateStatusBadge
          status={plan.isAlternativeRoute ? "selected" : "winner"}
        />
      </div>
      <RecommendedRouteHero
        plan={plan}
        etaLabel={etaLabel}
        leaveByLabel={leaveByLabel}
        hasNextDeparture={hasNextDeparture}
      />
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

export function RouteDirections({
  plan,
  destination,
}: {
  plan: RoutePlan;
  destination?: string;
}) {
  return (
    <RouteDetailsChain
      steps={plan.detailSteps ?? []}
      destination={destination}
    />
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

function RecommendedNextDeparture({
  routeId,
  minutes,
}: {
  routeId: string;
  minutes: number | undefined;
}) {
  if (minutes == null || !Number.isFinite(minutes)) return null;

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
    <div className="sr-reasoning-inset" data-typing={isTyping ? "true" : "false"}>
      <p className="sr-ai-reasoning" aria-live="polite">
        <span>
          <TransitText
            text={markRouteTokensForTransitText(visibleText)}
            bulletSize={15}
          />
        </span>
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

function isTransitLineContext(text: string, offset: number, length: number) {
  // Bare digits/letters are far more common in reasoning prose than genuine
  // subway references, so require nearby train/line context before rendering.
  const before = text.slice(Math.max(0, offset - 16), offset).toLowerCase();
  const after = text
    .slice(offset + length, offset + length + 18)
    .toLowerCase();
  return (
    /\b(the|take|via|next|board)\s+$/.test(before) ||
    /^\s+(train|line|service|express|local)\b/.test(after)
  );
}

export function RouteStepStrip({ segments }: { segments: RouteStripSegment[] }) {
  // A compact summary; full instructions remain in the canonical detail chain.
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
                segment.minutes != null && Number.isFinite(segment.minutes)
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

const RIDE_MOTION = { duration: 0.3, ease: [0.22, 1, 0.36, 1] as const };

function rideLineColor(step: RouteDetailStep): string {
  if (step.mode === "bus") return "#38445c";
  return (step.routeId && LINE_COLORS[step.routeId]) || "var(--sr-rule-bright)";
}

function RideMetaToggle({
  rideMeta,
  canExpand,
  expanded,
  stopListId,
  onToggle,
  reduceMotion,
}: {
  rideMeta?: string;
  canExpand: boolean;
  expanded: boolean;
  stopListId: string;
  onToggle: () => void;
  reduceMotion: boolean;
}) {
  if (!rideMeta) return null;
  if (!canExpand) return <span className="sr-detail-ride__meta">{rideMeta}</span>;
  return (
    <button
      type="button"
      className="sr-detail-ride__disclosure"
      aria-expanded={expanded}
      aria-controls={stopListId}
      onClick={onToggle}
    >
      <span>{rideMeta}</span>
      <motion.span
        className="sr-detail-ride__chevron"
        animate={{ rotate: expanded ? 180 : 0 }}
        transition={reduceMotion ? { duration: 0 } : RIDE_MOTION}
      >
        <ChevronDown size={14} strokeWidth={1.8} aria-hidden="true" />
      </motion.span>
    </button>
  );
}

function RideStopList({
  expanded,
  rideStops,
  stopListId,
  reduceMotion,
}: {
  expanded: boolean;
  rideStops: string[];
  stopListId: string;
  reduceMotion: boolean;
}) {
  return (
    <AnimatePresence initial={false}>
      {expanded && rideStops.length > 0 ? (
        <motion.ol
          id={stopListId}
          className="sr-detail-ride__stops"
          initial={reduceMotion ? false : { opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
          transition={reduceMotion ? { duration: 0 } : RIDE_MOTION}
        >
          {rideStops.map((stop, stopIndex) => (
            <li key={`${stopListId}-${stop}-${stopIndex}`}>{stop}</li>
          ))}
        </motion.ol>
      ) : null}
    </AnimatePresence>
  );
}

function RideTransferNote({ step }: { step: RouteDetailStep }) {
  if (!step.transferTo) return null;
  return (
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
  );
}

function DetailRideStep({
  step,
  index,
  expanded,
  onToggle,
  reduceMotion,
}: {
  step: RouteDetailStep;
  index: number;
  expanded: boolean;
  onToggle: (index: number) => void;
  reduceMotion: boolean;
}) {
  const rideStops = step.stops ?? [];
  const stopListId = `sr-ride-stops-${index}`;
  return (
    <li className="sr-detail-ride">
      <span
        className="sr-detail-ride__line"
        style={{ background: rideLineColor(step) }}
        aria-hidden="true"
      />
      <span className="sr-detail-ride__copy">
        {step.fromStop && <strong>{step.fromStop}</strong>}
        <RideMetaToggle
          rideMeta={step.rideMeta}
          canExpand={rideStops.length > 0}
          expanded={expanded}
          stopListId={stopListId}
          onToggle={() => onToggle(index)}
          reduceMotion={reduceMotion}
        />
        <RideStopList
          expanded={expanded}
          rideStops={rideStops}
          stopListId={stopListId}
          reduceMotion={reduceMotion}
        />
        {step.toStop && <strong>{step.toStop}</strong>}
        <RideTransferNote step={step} />
      </span>
    </li>
  );
}

function DetailVehicleIcon({ step }: { step: RouteDetailStep }) {
  if (step.kind !== "board" || !step.routeId) return <StepIcon type="walk" />;
  if (step.mode === "bus") {
    return (
      <span className="sr-detail-step__vehicle">
        <BusChip route={step.routeId} />
        <StepIcon type="bus" />
      </span>
    );
  }
  return (
    <span className="sr-detail-step__vehicle">
      <RouteBullet line={step.routeId} size={22} />
      <StepIcon type="ride" />
    </span>
  );
}

function DetailGenericStep({ step }: { step: RouteDetailStep }) {
  return (
    <li className="sr-detail-step">
      <span className="sr-detail-step__icon">
        <DetailVehicleIcon step={step} />
      </span>
      <span className="sr-detail-step__copy">
        <strong>{step.title}</strong>
        {step.subtitle && <small>{step.subtitle}</small>}
        {step.note && (
          <small className="sr-detail-step__note">
            {step.note}
            {step.live && (
              <PredictionStatus predictionType="live" predictionFreshness="fresh" />
            )}
          </small>
        )}
      </span>
    </li>
  );
}

function DetailChainStep({
  step,
  index,
  expanded,
  onToggle,
  reduceMotion,
}: {
  step: RouteDetailStep;
  index: number;
  expanded: boolean;
  onToggle: (index: number) => void;
  reduceMotion: boolean;
}) {
  if (step.kind === "segment") {
    return (
      <li className="sr-detail-step sr-detail-step--segment">
        <span className="sr-detail-step__copy">
          <strong>{step.title}</strong>
        </span>
      </li>
    );
  }
  if (step.kind === "dwell") {
    return (
      <li className="sr-detail-step sr-detail-step--dwell">
        <span className="sr-detail-step__icon">
          <LocationPin tone="start" size={18} />
        </span>
        <span className="sr-detail-step__copy">
          <strong>{step.title}</strong>
          {step.subtitle && <small>{step.subtitle}</small>}
        </span>
      </li>
    );
  }
  if (step.kind === "ride") {
    return (
      <DetailRideStep
        step={step}
        index={index}
        expanded={expanded}
        onToggle={onToggle}
        reduceMotion={reduceMotion}
      />
    );
  }
  return <DetailGenericStep step={step} />;
}

function RouteDetailsChain({
  steps,
  destination,
}: {
  steps: RouteDetailStep[];
  destination?: string;
}) {
  const [expandedRideIds, setExpandedRideIds] = useState<Set<number>>(
    () => new Set(),
  );
  const reduceMotion = useReducedMotion() ?? false;
  const cleanedDestination = destination?.trim();

  const toggleRide = (index: number) => {
    setExpandedRideIds((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

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
      {steps.map((step, index) => (
        <DetailChainStep
          key={index}
          step={step}
          index={index}
          expanded={expandedRideIds.has(index)}
          onToggle={toggleRide}
          reduceMotion={reduceMotion}
        />
      ))}
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
