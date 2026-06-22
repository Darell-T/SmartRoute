"use client";

import { useMemo, type CSSProperties } from "react";
import {
  AlertTriangle,
  Footprints,
  GitBranch,
  Radio,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import type {
  LiveArrival,
  RouteCandidate,
  RouteStep,
  ServiceAlert,
} from "@/types";
import { AnimatedList } from "@/components/ui/animated-list";
import { BlurFade } from "@/components/ui/blur-fade";
import { NumberTicker } from "@/components/ui/number-ticker";
import LiveSummaryOrb from "@/components/smart-route/live-summary-orb";
import { TrainBullet } from "@/components/smart-route/train-bullet";
import {
  mapStatusToOrbColor,
  normalizeNetworkStatus,
  type NetworkHealthStatus,
} from "@/components/smart-route/network-orb-color";
import { summarizeRoute, type RouteSummary } from "@/lib/smart-route";
import {
  deriveTransitRouteIds,
  isAlertForRouteIds,
  routeCandidateLabel,
} from "@/lib/route-planning";

export type RouteMode = "idle" | "searching" | "loading" | "active" | "error";

interface RouteMissionBriefRailProps {
  mode: RouteMode;
  status: NetworkHealthStatus;
  activeCandidate: RouteCandidate | null;
  routeCandidates: RouteCandidate[];
  summary: RouteSummary | null;
  recommendationText: string;
  thinkingText: string;
  errorText?: string | null;
  liveFeed: {
    arrivals: LiveArrival[];
  };
  alerts: ServiceAlert[];
  isSpeaking: boolean;
  accent: string;
  onSelectCandidate: (candidate: RouteCandidate) => void;
  onRetry: () => void;
  onClear: () => void;
}

type TimelineStep = {
  id: string;
  kind: "walk" | "train" | "bus" | "transfer";
  line?: string;
  title: string;
  detail: string;
  meta: string;
  min: number;
  clock: string;
};

function estimateStepMinutes(step: RouteStep) {
  if (step.type === "SUBWAY" || step.type === "BUS") {
    if (typeof step.minutes_until_arrival === "number") {
      return Math.max(1, Math.round(step.minutes_until_arrival));
    }
    return 8;
  }
  return 2;
}

function formatClockAfter(minutes: number) {
  return new Date(Date.now() + minutes * 60_000).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function stopCountLabel(step: RouteStep) {
  if (typeof step.stop_count === "number" && step.stop_count > 0) {
    return `${step.stop_count} stop${step.stop_count === 1 ? "" : "s"}`;
  }
  if (Array.isArray(step.intermediate_stops) && step.intermediate_stops.length > 0) {
    const count = step.intermediate_stops.length + 1;
    return `${count} stop${count === 1 ? "" : "s"}`;
  }
  return "live segment";
}

function buildTimeline(steps: RouteStep[]): TimelineStep[] {
  const timeline: TimelineStep[] = [];
  let elapsed = 0;
  let previousTransit: RouteStep | null = null;

  steps.forEach((step, index) => {
    if ((step.type === "SUBWAY" || step.type === "BUS") && previousTransit) {
      const station = step.departure_stop || previousTransit.arrival_stop || "Transfer";
      elapsed += 2;
      timeline.push({
        id: `transfer-${index}-${station}`,
        kind: "transfer",
        title: "Transfer",
        detail: station,
        meta: "Cross-platform transfer",
        min: 2,
        clock: formatClockAfter(elapsed),
      });
    }

    const min = estimateStepMinutes(step);
    elapsed += min;

    if (step.type === "WALK") {
      const nextTransit = steps
        .slice(index + 1)
        .find((candidate) => candidate.type === "SUBWAY" || candidate.type === "BUS");
      const isFinal = index === steps.length - 1;
      timeline.push({
        id: `walk-${index}`,
        kind: "walk",
        title: "Walk",
        detail: isFinal
          ? "To your destination"
          : `From your location to ${nextTransit?.departure_stop || "the station"}`,
        meta: `${min} min`,
        min,
        clock: formatClockAfter(elapsed),
      });
      previousTransit = null;
      return;
    }

    const line = step.train_line || step.route_id || (step.type === "BUS" ? "BUS" : "");
    const direction = step.direction ? ` ${step.direction}` : "";
    const detail =
      step.departure_stop && step.arrival_stop
        ? `${step.departure_stop} -> ${step.arrival_stop}`
        : step.arrival_stop || step.departure_stop || "Transit segment";
    timeline.push({
      id: `${step.type}-${line}-${index}-${detail}`,
      kind: step.type === "BUS" ? "bus" : "train",
      line,
      title: step.type === "BUS" ? `${line} Bus${direction}` : `${line} Train${direction}`,
      detail,
      meta: `${stopCountLabel(step)} · ${min} min`,
      min,
      clock: formatClockAfter(elapsed),
    });
    previousTransit = step;
  });

  return timeline;
}

function compactRecommendation(
  recommendationText: string,
  activeCandidate: RouteCandidate | null,
  summary: RouteSummary | null,
) {
  const source =
    recommendationText ||
    activeCandidate?.recommendation_reason ||
    summary?.reasonHeadline ||
    "SmartRoute is checking the strongest path.";
  const sentences = source
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean)
    .slice(0, 2);
  return sentences.join(" ");
}

function arrivalMinutes(arrival: LiveArrival) {
  if (typeof arrival.arrival_time !== "number") return null;
  if (arrival.arrival_time > 1_000_000_000) {
    return Math.max(0, Math.round((arrival.arrival_time - Date.now() / 1000) / 60));
  }
  return Math.max(0, Math.round(arrival.arrival_time));
}

function alertDetail(alert: ServiceAlert) {
  const body = alert.description || alert.header || "Route alert";
  return body.length > 96 ? `${body.slice(0, 94).trim()}...` : body;
}

function MissionStatusHeader({
  mode,
  status,
  isSpeaking,
  recommendation,
}: {
  mode: RouteMode;
  status: NetworkHealthStatus;
  isSpeaking: boolean;
  recommendation: string;
}) {
  const signal = normalizeNetworkStatus(status);
  const orbPhase = mode === "loading" || mode === "searching" ? "thinking" : "idle";

  return (
    <BlurFade className="sr-mission-brief__hero">
      <div className="sr-mission-brief__kicker">
        <span>Intelligence Hub</span>
        <span className="sr-mission-brief__active-dot">
          <i aria-hidden="true" />
          Route lens
        </span>
      </div>

      <div className="sr-mission-brief__hero-row">
        <div className="sr-mission-brief__orb" aria-hidden="true">
          <LiveSummaryOrb
            phase={orbPhase}
            compact
            contained
            speaking={isSpeaking}
            color={mapStatusToOrbColor(signal)}
          />
        </div>
        <div className="sr-mission-brief__copy">
          <div className="sr-mission-brief__jarvis">
            <Sparkles size={13} strokeWidth={1.8} aria-hidden="true" />
            ATLAS
          </div>
          <p>{recommendation}</p>
          <time>
            Today at{" "}
            {new Date().toLocaleTimeString("en-US", {
              hour: "numeric",
              minute: "2-digit",
              hour12: true,
            })}
          </time>
        </div>
      </div>
    </BlurFade>
  );
}

function RouteTimeline({ steps }: { steps: RouteStep[] }) {
  const timeline = useMemo(() => buildTimeline(steps), [steps]);

  return (
    <BlurFade className="sr-mission-section sr-mission-route">
      <div className="sr-mission-section__header">
        <span>Your Route</span>
        <em>Recommended</em>
      </div>
      <div className="sr-mission-route__timeline">
        {timeline.map((step) => (
          <article key={step.id} className="sr-mission-route__step" data-kind={step.kind}>
            <div className="sr-mission-route__node" aria-hidden="true">
              {step.kind === "train" && step.line ? (
                <TrainBullet line={step.line} size={31} />
              ) : step.kind === "bus" && step.line ? (
                <span className="sr-mission-route__bus">{step.line}</span>
              ) : step.kind === "transfer" ? (
                <GitBranch size={16} strokeWidth={1.7} />
              ) : (
                <Footprints size={15} strokeWidth={1.7} />
              )}
            </div>
            <div className="sr-mission-route__body">
              <div className="sr-mission-route__title-row">
                <strong>{step.title}</strong>
                <time>{step.clock}</time>
              </div>
              <p>{step.detail}</p>
              <span>{step.meta}</span>
            </div>
          </article>
        ))}
      </div>
    </BlurFade>
  );
}

function SummaryStrip({ summary }: { summary: RouteSummary }) {
  return (
    <BlurFade className="sr-mission-summary-strip">
      <div>
        <span>Total ETA</span>
        <strong>
          <NumberTicker value={summary.totalMin} suffix=" min" />
        </strong>
      </div>
      <div>
        <span>Depart</span>
        <strong className="sr-mission-summary-strip__live">Now</strong>
      </div>
      <div>
        <span>Reliability</span>
        <strong>
          <NumberTicker value={summary.confidence} suffix="%" />
        </strong>
        <small>{summary.stability}</small>
      </div>
    </BlurFade>
  );
}

function AlternateRoutes({
  activeCandidate,
  candidates,
  onSelectCandidate,
}: {
  activeCandidate: RouteCandidate | null;
  candidates: RouteCandidate[];
  onSelectCandidate: (candidate: RouteCandidate) => void;
}) {
  const alternates = candidates.filter((candidate) => candidate.id !== activeCandidate?.id);
  if (alternates.length === 0) return null;

  return (
    <BlurFade className="sr-mission-section sr-mission-alternates">
      <div className="sr-mission-section__header">
        <span>Alternate Routes</span>
        <em>Compare</em>
      </div>
      <AnimatedList className="sr-mission-alternates__list" delay={90} reverseOrder={false}>
        {alternates.map((candidate) => {
          const candidateSummary = summarizeRoute(
            candidate.steps,
            new Date(),
            candidate.total_minutes,
          );
          const lines = deriveTransitRouteIds(candidate.steps);
          return (
            <article key={candidate.id} className="sr-mission-alt-card">
              <div className="sr-mission-alt-card__lines" aria-hidden="true">
                {lines.slice(0, 2).map((line) => (
                  <TrainBullet key={line} line={line} size={28} />
                ))}
              </div>
              <div className="sr-mission-alt-card__copy">
                <strong>{routeCandidateLabel(candidate.steps)}</strong>
                <span>{candidate.rejection_reason || "Useful backup if the selected route changes."}</span>
              </div>
              <div className="sr-mission-alt-card__meta">
                <strong>{candidateSummary.totalMin} min</strong>
                <span>
                  {candidateSummary.transfers} transfer
                  {candidateSummary.transfers === 1 ? "" : "s"}
                </span>
              </div>
              <button type="button" onClick={() => onSelectCandidate(candidate)}>
                Use route
              </button>
            </article>
          );
        })}
      </AnimatedList>
    </BlurFade>
  );
}

function ScopedRouteTelemetry({
  liveFeed,
  alerts,
  routeIds,
}: {
  liveFeed: {
    arrivals: LiveArrival[];
  };
  alerts: ServiceAlert[];
  routeIds: string[];
}) {
  const routeSet = useMemo(() => new Set(routeIds.map((id) => id.toUpperCase())), [routeIds]);
  const scopedArrivals = useMemo(
    () =>
      liveFeed.arrivals
        .filter((arrival) => routeSet.has(String(arrival.route_id || "").toUpperCase()))
        .map((arrival) => ({ arrival, minutes: arrivalMinutes(arrival) }))
        .filter((item): item is { arrival: LiveArrival; minutes: number } => item.minutes != null)
        .sort((a, b) => a.minutes - b.minutes)
        .slice(0, 2),
    [liveFeed.arrivals, routeSet],
  );
  const scopedAlerts = useMemo(
    () => alerts.filter((alert) => isAlertForRouteIds(alert, routeIds)).slice(0, 2),
    [alerts, routeIds],
  );

  return (
    <BlurFade className="sr-mission-section sr-mission-live-context">
      <div className="sr-mission-section__header">
        <span>Live Context</span>
        <em>Scoped to route</em>
      </div>
      {scopedArrivals.length === 0 && scopedAlerts.length === 0 ? (
        <div className="sr-mission-live-context__empty">
          Route looks stable. No active alerts affecting this trip.
        </div>
      ) : (
        <AnimatedList className="sr-mission-live-context__list" delay={100} reverseOrder={false}>
          {scopedArrivals.map(({ arrival, minutes }) => (
            <article
              key={`${arrival.route_id}-${arrival.trip_id || arrival.stop_id}-${arrival.arrival_time}`}
              className="sr-mission-context-row"
            >
              <TrainBullet line={arrival.route_id} size={29} />
              <div>
                <strong>
                  {arrival.route_id} {arrival.direction || "route"}
                </strong>
                <span>{arrival.terminal_stop_name || "Selected route train"}</span>
              </div>
              <em>
                Arrives in <NumberTicker value={minutes} suffix=" min" />
              </em>
              <Radio size={15} strokeWidth={1.7} aria-hidden="true" />
            </article>
          ))}
          {scopedAlerts.map((alert, index) => (
            <article
              key={`${alert.header}-${index}`}
              className="sr-mission-context-row sr-mission-context-row--alert"
            >
              <span className="sr-mission-context-row__alert-icon" aria-hidden="true">
                <AlertTriangle size={16} strokeWidth={1.7} />
              </span>
              <div>
                <strong>{alert.header || "Route alert"}</strong>
                <span>{alertDetail(alert)}</span>
              </div>
              <em>MTA</em>
            </article>
          ))}
        </AnimatedList>
      )}
    </BlurFade>
  );
}

function LoadingMission({
  status,
  isSpeaking,
  thinkingText,
}: {
  status: NetworkHealthStatus;
  isSpeaking: boolean;
  thinkingText: string;
}) {
  return (
    <div className="sr-mission-brief" data-mode="loading">
      <MissionStatusHeader
        mode="loading"
        status={status}
        isSpeaking={isSpeaking}
        recommendation={thinkingText || "Analyzing route conditions... Checking live trains, service alerts, and transfer reliability."}
      />
      <div className="sr-mission-loading" aria-label="Route analysis loading">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

function ErrorMission({
  status,
  errorText,
  onRetry,
  onClear,
}: {
  status: NetworkHealthStatus;
  errorText?: string | null;
  onRetry: () => void;
  onClear: () => void;
}) {
  return (
    <div className="sr-mission-brief" data-mode="error">
      <MissionStatusHeader
        mode="error"
        status={status}
        isSpeaking={false}
        recommendation="ATLAS couldn't build a reliable route right now."
      />
      <div className="sr-mission-error">
        <p>{errorText || "MTA and traffic feeds may be unavailable."}</p>
        <div>
          <button type="button" onClick={onRetry}>
            Try again
          </button>
          <button type="button" onClick={onClear}>
            Clear
          </button>
        </div>
      </div>
    </div>
  );
}

export function RouteMissionBriefRail({
  mode,
  status,
  activeCandidate,
  routeCandidates,
  summary,
  recommendationText,
  thinkingText,
  errorText,
  liveFeed,
  alerts,
  isSpeaking,
  accent,
  onSelectCandidate,
  onRetry,
  onClear,
}: RouteMissionBriefRailProps) {
  if (mode === "loading" || mode === "searching") {
    return (
      <LoadingMission
        status={status}
        isSpeaking={isSpeaking}
        thinkingText={thinkingText}
      />
    );
  }

  if (mode === "error" && !activeCandidate) {
    return (
      <ErrorMission
        status={status}
        errorText={errorText}
        onRetry={onRetry}
        onClear={onClear}
      />
    );
  }

  if (!activeCandidate || !summary) return null;

  const routeIds = deriveTransitRouteIds(activeCandidate.steps);
  const recommendation = compactRecommendation(recommendationText, activeCandidate, summary);

  return (
    <div
      className="sr-mission-brief"
      data-mode="active"
      style={{ "--sr-mission-accent": accent } as CSSProperties}
    >
      <MissionStatusHeader
        mode="active"
        status={status}
        isSpeaking={isSpeaking}
        recommendation={recommendation}
      />
      <RouteTimeline steps={activeCandidate.steps} />
      <SummaryStrip summary={summary} />
      <AlternateRoutes
        activeCandidate={activeCandidate}
        candidates={routeCandidates}
        onSelectCandidate={onSelectCandidate}
      />
      <ScopedRouteTelemetry
        liveFeed={liveFeed}
        alerts={alerts}
        routeIds={routeIds}
      />
      <button type="button" className="sr-mission-brief__clear" onClick={onClear}>
        <RotateCcw size={14} strokeWidth={1.7} aria-hidden="true" />
        Return to network mode
      </button>
    </div>
  );
}
