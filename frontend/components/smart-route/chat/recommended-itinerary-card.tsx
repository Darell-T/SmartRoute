"use client";

import { useId, useMemo, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Map as MapIcon } from "iconoir-react";
import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";
import {
  buildItineraryViewModel,
  buildMergedItineraryViewModel,
  type ItineraryViewModel,
} from "./itinerary-view-model";
import { ItineraryLeg, JourneyTitle, LAYOUT_EASE } from "./itinerary-card-legs";

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
  const [expandedLegIds, setExpandedLegIds] = useState<Set<string>>(() => new Set());

  if (model.invalid) {
    return (
      <article
        className="sr-itinerary-card sr-itinerary-card--invalid"
        aria-labelledby={titleId}
      >
        <p id={titleId} className="sr-itinerary-card__invalid-msg">
          {model.invalidReason ?? "This itinerary is unavailable."}
        </p>
      </article>
    );
  }

  const toggleLeg = (eventId: string) => {
    setExpandedLegIds((current) => {
      const next = new Set(current);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      return next;
    });
  };

  return (
    <motion.article
      layout
      className="sr-itinerary-card"
      data-selected={isSelected ? "true" : "false"}
      data-has-final-walk={
        model.events.at(-1)?.kind === "walk" ? "true" : "false"
      }
      aria-labelledby={titleId}
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        reduceMotion
          ? { duration: 0 }
          : {
              layout: { duration: 0.26, ease: LAYOUT_EASE },
              opacity: { duration: 0.22, delay: landDelayMs / 1000 },
              y: { duration: 0.26, delay: landDelayMs / 1000, ease: LAYOUT_EASE },
            }
      }
    >
      <header className="sr-itinerary-card__header">
        <JourneyTitle names={model.placeNames} id={titleId} />
        {model.arrivalLabel || model.firstLegArrivalLabel ? (
          <p className="sr-itinerary-card__arrive">
            {model.arrivalLabel ? `Arrive around ${model.arrivalLabel}` : null}
            {model.arrivalLabel && model.firstLegArrivalLabel ? (
              <span className="sr-itinerary-card__meta-sep" aria-hidden="true">
                {" "}·{" "}
              </span>
            ) : null}
            {model.firstLegArrivalLabel}
          </p>
        ) : null}
      </header>

      <div className="sr-itinerary-card__hero">
        <p className="sr-itinerary-card__summary" aria-label="Trip summary">
          <span className="sr-itinerary-card__duration-value">
            {model.durationLabel}
          </span>
          {model.metaParts.length > 0 ? (
            <span className="sr-itinerary-card__meta">
              <span className="sr-itinerary-card__meta-sep" aria-hidden="true">
                {" "}·{" "}
              </span>
              {model.metaParts.map((part, index) => (
                <span key={part}>
                  {index > 0 ? (
                    <span
                      className="sr-itinerary-card__meta-sep"
                      aria-hidden="true"
                    >
                      {" "}·{" "}
                    </span>
                  ) : null}
                  {part}
                </span>
              ))}
            </span>
          ) : null}
        </p>
      </div>

      <div className="sr-itinerary-card__legs">
        {model.events.map((event) => (
          <ItineraryLeg
            key={event.id}
            event={event}
            expanded={expandedLegIds.has(event.id)}
            onToggle={() => toggleLeg(event.id)}
            reduceMotion={reduceMotion}
          />
        ))}
      </div>

      <footer className="sr-itinerary-card__actions">
        <motion.button
          type="button"
          className="sr-itinerary-card__map-btn"
          aria-label={model.primaryActionLabel}
          disabled={!onPrimaryAction}
          onClick={onPrimaryAction}
          whileTap={reduceMotion || !onPrimaryAction ? undefined : { scale: 0.985 }}
          transition={{ duration: reduceMotion ? 0 : 0.12 }}
        >
          <MapIcon width={20} height={20} strokeWidth={1.6} aria-hidden="true" />
          <span>{model.primaryActionLabel}</span>
        </motion.button>
      </footer>
    </motion.article>
  );
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
  const model = useMemo(() => {
    const options = { primaryActionLabel, secondaryActionLabel: "View steps" };
    const canonical = cards.find((card) => card.itinerary);
    return canonical
      ? buildItineraryViewModel(canonical, options)
      : buildMergedItineraryViewModel(cards, options);
  }, [cards, primaryActionLabel]);
  if (!model) return null;

  const primaryCard =
    cards.find((card) => card.card_id === model.primaryCardId) ?? cards.at(-1);
  const isSelected = Boolean(
    selectedCardId && model.sourceCardIds.includes(selectedCardId),
  );
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
      transition={
        reduceMotion
          ? undefined
          : { duration: 1.5, repeat: Infinity, ease: "easeInOut" }
      }
    >
      <div className="sr-itinerary-card__skel-pill" />
      <div className="sr-itinerary-card__skel-line sr-itinerary-card__skel-line--lg" />
      <div className="sr-itinerary-card__skel-line sr-itinerary-card__skel-line--xl" />
      <div className="sr-itinerary-card__skel-line" />
      <div className="sr-itinerary-card__skel-line" />
    </motion.div>
  );
}
