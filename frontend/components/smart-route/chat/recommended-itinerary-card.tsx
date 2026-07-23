"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — recommended itinerary card

   Flat charcoal transit itinerary embedded in the conversation. One card
   represents a complete journey (including multi-stop chains). Official MTA
   bullets come from TrainBullet / public/mta-bullets SVGs. Entrance uses
   Motion with reduced-motion collapse.
   ════════════════════════════════════════════════════════════════════════ */

import { useId, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronRight } from "lucide-react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faPersonWalking } from "@fortawesome/free-solid-svg-icons";
import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";
import { TrainBullet, SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import {
  buildItineraryViewModel,
  buildMergedItineraryViewModel,
  isSupportedSubwayRoute,
  shouldCollapseEvents,
  warnUnsupportedRouteId,
  type ItineraryEvent,
  type ItineraryViewModel,
} from "./itinerary-view-model";

const EASE_OUT = [0.16, 1, 0.3, 1] as const;

function JourneyTitle({ names, id }: { names: string[]; id: string }) {
  if (names.length === 0) return null;
  return (
    <h3 id={id} className="sr-itinerary-card__title" aria-label={names.join(" to ")}>
      {names.map((name, index) => (
        <span key={`${name}-${index}`} className="sr-itinerary-card__title-part" aria-hidden="true">
          {index > 0 && <span className="sr-itinerary-card__title-arrow">→</span>}
          <span>{name}</span>
        </span>
      ))}
    </h3>
  );
}

function RouteGlyph({ routeId, size = 20 }: { routeId: string; size?: number }) {
  const normalized = routeId.trim().toUpperCase();
  if (!normalized) return null;

  if (SUBWAY_BULLET_ROUTES.has(normalized) || isSupportedSubwayRoute(normalized)) {
    return <TrainBullet line={normalized} size={size} />;
  }

  // Bus-style rectangular chip for bus routes; restrained fallback otherwise.
  const looksLikeBus = /[A-Z]{1,3}\d/.test(normalized) || normalized.length > 2;
  if (looksLikeBus) {
    return <TrainBullet line={normalized} size={size} title={`${normalized} bus`} />;
  }

  warnUnsupportedRouteId(normalized);
  return (
    <span
      className="sr-itinerary-card__route-fallback"
      style={{ width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.45)) }}
      aria-label={`${normalized} route`}
      title={`${normalized} route`}
    >
      {normalized.slice(0, 2)}
    </span>
  );
}

function EventModeVisual({ event }: { event: ItineraryEvent }) {
  if (event.kind === "walk") {
    return (
      <span className="sr-itinerary-card__mode-icon" aria-label="Walking">
        <FontAwesomeIcon icon={faPersonWalking} aria-hidden="true" />
      </span>
    );
  }

  if (event.kind === "pickup") {
    return <span className="sr-itinerary-card__pickup-mark" aria-hidden="true" />;
  }

  if (event.routeIds.length === 0) {
    return <span className="sr-itinerary-card__mode-dot" aria-hidden="true" />;
  }

  return (
    <span className="sr-itinerary-card__bullets" aria-hidden={false}>
      {event.routeIds.map((routeId, index) => (
        <span key={`${event.id}-${routeId}-${index}`} className="sr-itinerary-card__bullet-item">
          {index > 0 && (
            <span className="sr-itinerary-card__bullet-arrow" aria-hidden="true">
              →
            </span>
          )}
          <RouteGlyph routeId={routeId} size={20} />
        </span>
      ))}
    </span>
  );
}

function TimelineEvent({
  event,
  isFirst,
  isLast,
  index,
  reduceMotion,
}: {
  event: ItineraryEvent;
  isFirst: boolean;
  isLast: boolean;
  index: number;
  reduceMotion: boolean;
}) {
  const nodeClass =
    event.kind === "pickup"
      ? "sr-itinerary-card__node sr-itinerary-card__node--waypoint"
      : isFirst
        ? "sr-itinerary-card__node sr-itinerary-card__node--start"
        : isLast
          ? "sr-itinerary-card__node sr-itinerary-card__node--end"
          : "sr-itinerary-card__node";

  return (
    <motion.li
      className="sr-itinerary-card__event"
      data-kind={event.kind}
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, delay: reduceMotion ? 0 : 0.08 + index * 0.04, ease: EASE_OUT }}
    >
      <span className="sr-itinerary-card__rail" aria-hidden="true">
        <span className={nodeClass} />
      </span>
      <div className="sr-itinerary-card__event-body">
        <div className="sr-itinerary-card__event-main">
          <div className="sr-itinerary-card__event-lead">
            <EventModeVisual event={event} />
            {event.kind === "pickup" ? (
              <span className="sr-itinerary-card__event-title">{event.title}</span>
            ) : event.kind === "walk" ? (
              <span className="sr-itinerary-card__event-title sr-itinerary-card__event-title--walk">
                {event.title}
              </span>
            ) : null}
          </div>
          {event.durationLabel && (
            <span className="sr-itinerary-card__event-duration">{event.durationLabel}</span>
          )}
        </div>
        {event.kind !== "walk" && event.kind !== "pickup" && (
          <p className="sr-itinerary-card__event-path">{event.title}</p>
        )}
        {event.subtitle && <p className="sr-itinerary-card__event-sub">{event.subtitle}</p>}
        {event.kind === "pickup" && !event.subtitle && null}
      </div>
    </motion.li>
  );
}

function ItineraryCardShell({
  model,
  isSelected,
  landDelayMs,
  onPrimaryAction,
  onViewDetails,
}: {
  model: ItineraryViewModel;
  isSelected: boolean;
  landDelayMs: number;
  onPrimaryAction?: () => void;
  onViewDetails?: () => void;
}) {
  const reduceMotion = useReducedMotion() ?? false;
  const titleId = useId();
  const [expanded, setExpanded] = useState(false);

  const collapse = shouldCollapseEvents(model.events.length);
  const visibleEvents =
    collapse && !expanded ? model.events.slice(0, 4) : model.events;
  const hiddenCount = model.events.length - visibleEvents.length;

  if (model.invalid) {
    return (
      <article
        className="sr-itinerary-card sr-itinerary-card--invalid"
        data-selected={isSelected ? "true" : "false"}
        aria-labelledby={titleId}
      >
        <p id={titleId} className="sr-itinerary-card__invalid-msg">
          {model.invalidReason ?? "This itinerary is unavailable."}
        </p>
      </article>
    );
  }

function handleViewDetails() {
    if (collapse && !expanded) {
      setExpanded(true);
      return;
    }
    onViewDetails?.();
  }

  return (
    <motion.article
      className="sr-itinerary-card"
      data-role={model.recommended ? "recommended" : "alternative"}
      data-selected={isSelected ? "true" : "false"}
      aria-labelledby={titleId}
      initial={reduceMotion ? false : { opacity: 0, y: 14, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{
        duration: 0.42,
        delay: reduceMotion ? 0 : landDelayMs / 1000,
        ease: EASE_OUT,
      }}
    >
      {model.recommended && (
        <span className="sr-itinerary-card__badge">Recommended</span>
      )}

      <div className="sr-itinerary-card__summary">
        <JourneyTitle names={model.placeNames} id={titleId} />

        {model.arrivalLabel && (
          <p className="sr-itinerary-card__arrive">Arrive {model.arrivalLabel}</p>
        )}

        <p className="sr-itinerary-card__duration">
          <span className="sr-itinerary-card__duration-value">{model.durationLabel}</span>
        </p>

        {model.metaParts.length > 0 && (
          <p className="sr-itinerary-card__meta">
            {model.metaParts.map((part, index) => (
              <span key={part}>
                {index > 0 && (
                  <span className="sr-itinerary-card__meta-sep" aria-hidden="true">
                    {" "}
                    ·{" "}
                  </span>
                )}
                <span>{part}</span>
              </span>
            ))}
          </p>
        )}
      </div>

      {visibleEvents.length > 0 && (
        <>
          <div className="sr-itinerary-card__divider" role="presentation" />
          <ol className="sr-itinerary-card__timeline" aria-label="Journey steps">
            <AnimatePresence initial={false}>
              {visibleEvents.map((event, index) => (
                <TimelineEvent
                  key={event.id}
                  event={event}
                  index={index}
                  isFirst={index === 0}
                  isLast={index === visibleEvents.length - 1 && hiddenCount === 0}
                  reduceMotion={reduceMotion}
                />
              ))}
            </AnimatePresence>
          </ol>
          {hiddenCount > 0 && (
            <p className="sr-itinerary-card__more">
              {hiddenCount} more step{hiddenCount === 1 ? "" : "s"}
            </p>
          )}
        </>
      )}

      <div className="sr-itinerary-card__divider" role="presentation" />

      {model.rationale.length > 0 && (
        <div className="sr-itinerary-card__rationale">
          <p className="sr-itinerary-card__rationale-label">Why this route</p>
          <p className="sr-itinerary-card__rationale-text">
            {model.rationale.join(" · ")}
          </p>
        </div>
      )}

      <div className="sr-itinerary-card__actions">
        <button
          type="button"
          className="sr-itinerary-card__secondary"
          onClick={handleViewDetails}
        >
          {collapse && !expanded ? "View itinerary" : model.secondaryActionLabel}
          <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="sr-itinerary-card__primary"
          onClick={onPrimaryAction}
        >
          {model.primaryActionLabel}
        </button>
      </div>
    </motion.article>
  );
}

export function RecommendedItineraryCard({
  card,
  isSelected = false,
  landDelayMs = 0,
  onSelect,
  primaryActionLabel = "Open on map",
  secondaryActionLabel = "View itinerary",
}: {
  card: RouteCardData;
  isSelected?: boolean;
  landDelayMs?: number;
  onSelect?: (card: RouteCardData) => void;
  primaryActionLabel?: string;
  secondaryActionLabel?: string;
}) {
  const model = useMemo(
    () =>
      buildItineraryViewModel(card, {
        primaryActionLabel,
        secondaryActionLabel,
      }),
    [card, primaryActionLabel, secondaryActionLabel],
  );

  return (
    <ItineraryCardShell
      model={model}
      isSelected={isSelected}
      landDelayMs={landDelayMs}
      onPrimaryAction={() => onSelect?.(card)}
      onViewDetails={() => onSelect?.(card)}
    />
  );
}

export function RecommendedItineraryFromCards({
  cards,
  selectedCardId,
  landDelayMs = 0,
  onSelect,
  primaryActionLabel = "Open on map",
  secondaryActionLabel = "View itinerary",
}: {
  cards: RouteCardData[];
  selectedCardId?: string | null;
  landDelayMs?: number;
  onSelect?: (card: RouteCardData) => void;
  primaryActionLabel?: string;
  secondaryActionLabel?: string;
}) {
  const model = useMemo(
    () =>
      buildMergedItineraryViewModel(cards, {
        primaryActionLabel,
        secondaryActionLabel,
      }),
    [cards, primaryActionLabel, secondaryActionLabel],
  );

  if (!model) return null;

  const primaryCard =
    cards.find((c) => c.card_id === model.primaryCardId) ?? cards[cards.length - 1];
  const isSelected =
    Boolean(selectedCardId) && model.sourceCardIds.includes(selectedCardId!);

  return (
    <ItineraryCardShell
      model={model}
      isSelected={isSelected}
      landDelayMs={landDelayMs}
      onPrimaryAction={() => primaryCard && onSelect?.(primaryCard)}
      onViewDetails={() => primaryCard && onSelect?.(primaryCard)}
    />
  );
}

export function ItineraryCardSkeleton() {
  const reduceMotion = useReducedMotion() ?? false;
  return (
    <motion.div
      className="sr-itinerary-card sr-itinerary-card--skeleton"
      aria-hidden="true"
      initial={reduceMotion ? false : { opacity: 0.55 }}
      animate={reduceMotion ? undefined : { opacity: [0.55, 0.85, 0.55] }}
      transition={reduceMotion ? undefined : { duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
    >
      <div className="sr-itinerary-card__skel-pill" />
      <div className="sr-itinerary-card__skel-line sr-itinerary-card__skel-line--lg" />
      <div className="sr-itinerary-card__skel-line sr-itinerary-card__skel-line--sm" />
      <div className="sr-itinerary-card__skel-line sr-itinerary-card__skel-line--xl" />
      <div className="sr-itinerary-card__divider" />
      <div className="sr-itinerary-card__skel-line" />
      <div className="sr-itinerary-card__skel-line" />
    </motion.div>
  );
}
