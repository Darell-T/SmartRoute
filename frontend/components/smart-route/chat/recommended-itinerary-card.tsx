"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — recommended itinerary card

   Quiet dark data card with an optional liquid-metal perimeter shell.
   Official MTA bullets, condensed journey chunks, restrained Open on map.
   ════════════════════════════════════════════════════════════════════════ */

import { useId, useMemo } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Map as MapIcon } from "lucide-react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faPersonWalking, faArrowRightArrowLeft } from "@fortawesome/free-solid-svg-icons";
import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";
import { TrainBullet, SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import {
  buildItineraryViewModel,
  buildMergedItineraryViewModel,
  isSupportedSubwayRoute,
  warnUnsupportedRouteId,
  type ItineraryEvent,
  type ItineraryViewModel,
} from "./itinerary-view-model";
import { LiquidMetalShell } from "./liquid-metal-shell";

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

  if (event.routeIds.length === 0) {
    return <span className="sr-itinerary-card__mode-spacer" aria-hidden="true" />;
  }

  return (
    <span className="sr-itinerary-card__bullets">
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

function PreviewRow({
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
  return (
    <motion.li
      className="sr-itinerary-card__event"
      data-kind={event.kind}
      data-first={isFirst ? "true" : "false"}
      data-last={isLast ? "true" : "false"}
      initial={reduceMotion ? false : { opacity: 0, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, delay: reduceMotion ? 0 : 0.04 + index * 0.03, ease: EASE_OUT }}
    >
      <span className="sr-itinerary-card__rail" aria-hidden="true">
        <span className="sr-itinerary-card__node" />
      </span>
      <div className="sr-itinerary-card__event-main">
        <div className="sr-itinerary-card__event-lead">
          <EventModeVisual event={event} />
          <div className="sr-itinerary-card__event-copy">
            <span className="sr-itinerary-card__event-title">{event.title}</span>
            {event.subtitle ? (
              <span className="sr-itinerary-card__event-sub">{event.subtitle}</span>
            ) : null}
          </div>
        </div>
        {event.durationLabel && (
          <span className="sr-itinerary-card__event-duration">{event.durationLabel}</span>
        )}
      </div>
    </motion.li>
  );
}

function ItineraryCardShell({
  model,
  isSelected,
  landDelayMs,
  onPrimaryAction,
}: {
  model: ItineraryViewModel;
  isSelected: boolean;
  landDelayMs: number;
  onPrimaryAction?: () => void;
}) {
  const reduceMotion = useReducedMotion() ?? false;
  const titleId = useId();
  const useMetal = model.recommended && !model.invalid;

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

  const card = (
    <motion.article
      className="sr-itinerary-card"
      data-role={model.recommended ? "recommended" : "alternative"}
      data-selected={isSelected ? "true" : "false"}
      data-metal={useMetal ? "true" : "false"}
      aria-labelledby={titleId}
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.32,
        delay: reduceMotion ? 0 : landDelayMs / 1000,
        ease: EASE_OUT,
      }}
    >
      <header className="sr-itinerary-card__header">
        {model.recommended && (
          <span className="sr-itinerary-card__badge">Recommended</span>
        )}
        <JourneyTitle names={model.placeNames} id={titleId} />
        {model.arrivalLabel && (
          <p className="sr-itinerary-card__arrive">
            Arrive {model.arrivalLabel}
          </p>
        )}
      </header>

      <div className="sr-itinerary-card__hero">
        <p className="sr-itinerary-card__duration">
          <span className="sr-itinerary-card__duration-value">{model.durationLabel}</span>
        </p>
        {model.metaParts.length > 0 && (
          <p className="sr-itinerary-card__meta">
            <FontAwesomeIcon
              icon={faArrowRightArrowLeft}
              className="sr-itinerary-card__meta-icon"
              aria-hidden="true"
            />
            <span>
              {model.metaParts.map((part, index) => (
                <span key={part}>
                  {index > 0 && (
                    <span className="sr-itinerary-card__meta-sep" aria-hidden="true">
                      {" "}
                      ·{" "}
                    </span>
                  )}
                  {part}
                </span>
              ))}
            </span>
          </p>
        )}
      </div>

      {model.events.length > 0 && (
        <ol className="sr-itinerary-card__timeline" aria-label="Route preview">
          {model.events.map((event, index) => (
            <PreviewRow
              key={event.id}
              event={event}
              index={index}
              isFirst={index === 0}
              isLast={index === model.events.length - 1}
              reduceMotion={reduceMotion}
            />
          ))}
        </ol>
      )}

      <footer className="sr-itinerary-card__actions">
        <button
          type="button"
          className="sr-itinerary-card__map-btn"
          onClick={onPrimaryAction}
        >
          <MapIcon size={14} strokeWidth={1.75} aria-hidden="true" />
          {model.primaryActionLabel}
        </button>
      </footer>
    </motion.article>
  );

  if (!useMetal) return card;

  return <LiquidMetalShell>{card}</LiquidMetalShell>;
}

export function RecommendedItineraryCard({
  card,
  isSelected = false,
  landDelayMs = 0,
  onSelect,
  primaryActionLabel = "Open on map",
}: {
  card: RouteCardData;
  isSelected?: boolean;
  landDelayMs?: number;
  onSelect?: (card: RouteCardData) => void;
  primaryActionLabel?: string;
}) {
  const model = useMemo(
    () =>
      buildItineraryViewModel(card, {
        primaryActionLabel,
        secondaryActionLabel: "View steps",
      }),
    [card, primaryActionLabel],
  );

  return (
    <ItineraryCardShell
      model={model}
      isSelected={isSelected}
      landDelayMs={landDelayMs}
      onPrimaryAction={() => onSelect?.(card)}
    />
  );
}

export function RecommendedItineraryFromCards({
  cards,
  selectedCardId,
  landDelayMs = 0,
  onSelect,
  primaryActionLabel = "Open on map",
}: {
  cards: RouteCardData[];
  selectedCardId?: string | null;
  landDelayMs?: number;
  onSelect?: (card: RouteCardData) => void;
  primaryActionLabel?: string;
}) {
  const model = useMemo(
    () =>
      buildMergedItineraryViewModel(cards, {
        primaryActionLabel,
        secondaryActionLabel: "View steps",
      }),
    [cards, primaryActionLabel],
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
    />
  );
}

export function ItineraryCardSkeleton() {
  const reduceMotion = useReducedMotion() ?? false;
  return (
    <motion.div
      className="sr-itinerary-card sr-itinerary-card--skeleton"
      aria-hidden="true"
      initial={reduceMotion ? false : { opacity: 0.5 }}
      animate={reduceMotion ? undefined : { opacity: [0.5, 0.78, 0.5] }}
      transition={reduceMotion ? undefined : { duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
    >
      <div className="sr-itinerary-card__skel-pill" />
      <div className="sr-itinerary-card__skel-line sr-itinerary-card__skel-line--lg" />
      <div className="sr-itinerary-card__skel-line sr-itinerary-card__skel-line--xl" />
      <div className="sr-itinerary-card__skel-line" />
      <div className="sr-itinerary-card__skel-line" />
    </motion.div>
  );
}
