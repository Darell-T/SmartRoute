"use client";

import { Fragment, useEffect, useState, type RefObject } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { BusChip, LocationPin, RouteBullet, StepIcon, TransitText } from "./atoms";
import { InlineArrivalCountdown } from "./arrival-countdown";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import { formatDurationLabel, type RecommendedRouteDisplay } from "./route-display-compat";
import { PredictionStatus } from "./route-view-nearby";
import { LINE_COLORS } from "./types";
import type { RouteDetailStep, RoutePlan, RouteStripSegment } from "./types";

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
  const transfers = plan.transferCount ?? candidate.transfers ?? 0;
  const hasDetails = (plan.detailSteps?.length ?? 0) > 0;
  // Hero line: duration is the LargeTitle, arrival time rides the same
  // baseline row. Leave-by only appears when the backend supplies it.
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
    <article ref={cardRef} className="sr-recommended-route smart-route-liquid-card">
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
      {plan.journeyPlaces && plan.journeyPlaces.length > 2 && (
        <p className="sr-recommended-route__journey" aria-label="Journey stops">
          {plan.journeyPlaces.join(" → ")}
        </p>
      )}
      {plan.rationale ? <TypedRouteReasoning text={plan.rationale} /> : null}
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

function RouteDetailsChain({
  steps,
  destination,
}: {
  steps: RouteDetailStep[];
  destination?: string;
}) {
  // Start → walk → board → ride → walk → Arrive stays in backend order.
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
        if (step.kind === "segment") {
          return (
            <li key={index} className="sr-detail-step sr-detail-step--segment">
              <span className="sr-detail-step__copy">
                <strong>{step.title}</strong>
              </span>
            </li>
          );
        }
        if (step.kind === "dwell") {
          return (
            <li key={index} className="sr-detail-step sr-detail-step--dwell">
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
