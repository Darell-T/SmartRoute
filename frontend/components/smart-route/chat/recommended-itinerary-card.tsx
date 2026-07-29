"use client";

import { useId, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { BorderBeam } from "border-beam";
import { Bus, Map as MapIcon, NavArrowDown } from "iconoir-react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowRightArrowLeft,
  faLocationDot,
} from "@fortawesome/free-solid-svg-icons";
import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";
import { getRouteColor } from "@/lib/mta-colors";
import {
  TrainBullet,
  SUBWAY_BULLET_ROUTES,
} from "@/components/smart-route/train-bullet";
import {
  buildItineraryViewModel,
  buildMergedItineraryViewModel,
  warnUnsupportedRouteId,
  type ItineraryEvent,
  type ItineraryViewModel,
} from "./itinerary-view-model";
import { WalkingIcon } from "./walking-icon";

const LAYOUT_EASE = [0.22, 1, 0.36, 1] as const;

function JourneyTitle({ names, id }: { names: string[]; id: string }) {
  return (
    <h2 id={id} className="sr-itinerary-card__title" aria-label={names.join(" to ")}>
      {names.map((name, index) => (
        <span
          key={`${name}-${index}`}
          className="sr-itinerary-card__title-part"
          aria-hidden="true"
        >
          {index > 0 ? (
            <span className="sr-itinerary-card__title-arrow">→</span>
          ) : null}
          <span>{name}</span>
        </span>
      ))}
    </h2>
  );
}

function RouteGlyph({
  routeId,
  kind,
}: {
  routeId: string;
  kind: ItineraryEvent["kind"];
}) {
  const normalized = routeId.trim().toUpperCase();
  if (!normalized) return null;
  if (kind === "bus") {
    return (
      <span className="sr-itinerary-card__bus-glyph" aria-hidden="true">
        <Bus width={17} height={17} strokeWidth={1.8} fill="currentColor" />
      </span>
    );
  }
  if (!SUBWAY_BULLET_ROUTES.has(normalized)) {
    const looksLikeBus = /[A-Z]{1,3}\d/.test(normalized) || normalized.length > 2;
    if (!looksLikeBus) warnUnsupportedRouteId(normalized);
  }
  return <TrainBullet line={normalized} size={30} />;
}

function intermediateStops(event: ItineraryEvent): string[] {
  const stops = [...(event.stops ?? [])];
  if (event.fromLabel && stops[0] === event.fromLabel) stops.shift();
  if (event.toLabel && stops.at(-1) === event.toLabel) stops.pop();
  return stops;
}

function StopChain({
  event,
  expanded,
  rideLabel,
  canExpand,
  onToggle,
  reduceMotion,
}: {
  event: ItineraryEvent;
  expanded: boolean;
  rideLabel: string;
  canExpand: boolean;
  onToggle: () => void;
  reduceMotion: boolean;
}) {
  const stops = intermediateStops(event);
  const disclosure = rideLabel ? (
    canExpand ? (
      <button
        type="button"
        className="sr-itinerary-card__disclosure"
        aria-expanded={expanded}
        aria-controls={`${event.id}-stops`}
        onClick={onToggle}
      >
        <span className="sr-itinerary-card__chain-summary-marker" aria-hidden="true" />
        <span>Ride {rideLabel}</span>
        <motion.span
          className="sr-itinerary-card__disclosure-icon"
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={
            reduceMotion
              ? { duration: 0 }
              : { duration: 0.3, ease: LAYOUT_EASE }
          }
        >
          <NavArrowDown width={14} height={14} strokeWidth={1.8} />
        </motion.span>
      </button>
    ) : (
      <p className="sr-itinerary-card__ride-summary">
        <span className="sr-itinerary-card__chain-summary-marker" aria-hidden="true" />
        <span>Ride {rideLabel}</span>
      </p>
    )
  ) : null;

  return (
    <div
      className="sr-itinerary-card__stop-chain"
      data-expanded={expanded ? "true" : "false"}
      style={
        {
          "--sr-route-color":
            event.kind === "bus"
              ? "#5f8fd9"
              : getRouteColor(event.routeIds[0] ?? ""),
        } as React.CSSProperties
      }
    >
      <div className="sr-itinerary-card__chain-track">
        <div className="sr-itinerary-card__chain-row">
          <span className="sr-itinerary-card__chain-marker sr-itinerary-card__chain-marker--start" />
          <span className="sr-itinerary-card__station">{event.fromLabel ?? "Board"}</span>
        </div>

        {disclosure}

        <AnimatePresence initial={false}>
          {expanded && stops.length > 0 ? (
            <motion.ol
              id={`${event.id}-stops`}
              className="sr-itinerary-card__intermediate-stops"
              initial={reduceMotion ? false : { opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
              transition={
                reduceMotion
                  ? { duration: 0 }
                  : { duration: 0.3, ease: LAYOUT_EASE }
              }
            >
              {stops.map((stop, index) => (
                <motion.li
                  key={`${event.id}-${stop}-${index}`}
                  initial={reduceMotion ? false : { opacity: 0, y: -2 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={
                    reduceMotion
                      ? { duration: 0 }
                      : { duration: 0.16, delay: index * 0.018 }
                  }
                >
                  <span className="sr-itinerary-card__chain-marker sr-itinerary-card__chain-marker--mid" />
                  <span>{stop}</span>
                </motion.li>
              ))}
            </motion.ol>
          ) : null}
        </AnimatePresence>

        <div className="sr-itinerary-card__chain-row">
          <span className="sr-itinerary-card__chain-marker sr-itinerary-card__chain-marker--end" />
          <span className="sr-itinerary-card__station">{event.toLabel ?? event.title}</span>
        </div>
      </div>
    </div>
  );
}

function TransitLeg({
  event,
  expanded,
  onToggle,
  reduceMotion,
}: {
  event: ItineraryEvent;
  expanded: boolean;
  onToggle: () => void;
  reduceMotion: boolean;
}) {
  const stopNames = intermediateStops(event);
  const canExpand = stopNames.length > 0;
  const stopsLabel =
    typeof event.stopCount === "number"
      ? `${event.stopCount} ${event.stopCount === 1 ? "stop" : "stops"}`
      : null;
  const rideLabel = [stopsLabel, event.durationLabel].filter(Boolean).join(", ");

  return (
    <section className="sr-itinerary-card__leg" aria-label={`${event.kind} leg`}>
      <div className="sr-itinerary-card__leg-glyph">
        <RouteGlyph routeId={event.routeIds[0] ?? ""} kind={event.kind} />
      </div>
      <div className="sr-itinerary-card__leg-body">
        <div className="sr-itinerary-card__leg-heading">
          {event.kind === "bus" && event.routeIds[0] ? (
            <span className="sr-itinerary-card__bus-route">
              {event.routeIds[0].toUpperCase()}
            </span>
          ) : null}
          <span>{event.fromLabel ?? "Board"}</span>
          <span className="sr-itinerary-card__leg-arrow" aria-hidden="true">→</span>
          <span>{event.toLabel ?? event.title}</span>
        </div>

        <StopChain
          event={event}
          expanded={expanded}
          rideLabel={rideLabel}
          canExpand={canExpand}
          onToggle={onToggle}
          reduceMotion={reduceMotion}
        />
      </div>
    </section>
  );
}

function WalkingLeg({ event }: { event: ItineraryEvent }) {
  return (
    <section className="sr-itinerary-card__walk" aria-label="Walking directions">
      <span className="sr-itinerary-card__walk-icon" aria-hidden="true">
        <WalkingIcon />
      </span>
      <div className="sr-itinerary-card__walk-copy">
        <div className="sr-itinerary-card__walk-heading">
          <span>{event.fromLabel ?? "Walk"}</span>
          <span className="sr-itinerary-card__leg-arrow" aria-hidden="true">→</span>
          <span>{event.toLabel ?? event.title}</span>
        </div>
        {event.durationLabel ? (
          <p className="sr-itinerary-card__walk-meta">{event.durationLabel}</p>
        ) : null}
      </div>
    </section>
  );
}

function WaypointStop({ event }: { event: ItineraryEvent }) {
  return (
    <section className="sr-itinerary-card__waypoint" aria-label="Planned stop">
      <span className="sr-itinerary-card__waypoint-icon" aria-hidden="true">
        <FontAwesomeIcon icon={faLocationDot} />
      </span>
      <div>
        <p className="sr-itinerary-card__waypoint-title">{event.title}</p>
        <p className="sr-itinerary-card__waypoint-meta">
          {[event.subtitle, event.sourceLabel].filter(Boolean).join(" · ")}
        </p>
      </div>
    </section>
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
    <BorderBeam
      size="pulse-inner"
      colorVariant="mono"
      theme="auto"
      staticColors
      strength={0.32}
      brightness={0.9}
      duration={4.8}
      active={!reduceMotion}
      borderRadius={16}
      className="sr-itinerary-card-beam"
    >
      <motion.article
        layout
        className="sr-itinerary-card"
        data-selected={isSelected ? "true" : "false"}
        data-has-final-walk={
          model.events.at(-1)?.kind === "walk" ? "true" : "false"
        }
        aria-labelledby={titleId}
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={
          reduceMotion
            ? { duration: 0 }
            : {
                layout: { duration: 0.3, ease: LAYOUT_EASE },
                opacity: { duration: 0.26, delay: landDelayMs / 1000 },
                y: { duration: 0.3, delay: landDelayMs / 1000, ease: LAYOUT_EASE },
              }
        }
      >
        <header className="sr-itinerary-card__header">
          <JourneyTitle names={model.placeNames} id={titleId} />
          {model.arrivalLabel ? (
            <p className="sr-itinerary-card__arrive">
              Arrive around {model.arrivalLabel}
            </p>
          ) : null}
          {model.firstLegArrivalLabel ? (
            <p className="sr-itinerary-card__arrive">
              {model.firstLegArrivalLabel}
            </p>
          ) : null}
        </header>

        <div className="sr-itinerary-card__hero" aria-label="Trip summary">
          <p className="sr-itinerary-card__duration">
            <span className="sr-itinerary-card__duration-value">
              {model.durationLabel}
            </span>
          </p>
          <p className="sr-itinerary-card__meta">
            <FontAwesomeIcon
              icon={faArrowRightArrowLeft}
              className="sr-itinerary-card__meta-icon"
              aria-hidden="true"
            />
            <span>
              {model.metaParts.map((part, index) => (
                <span key={part}>
                  {index > 0 ? (
                    <span className="sr-itinerary-card__meta-sep" aria-hidden="true">
                      {" "}
                      ·{" "}
                    </span>
                  ) : null}
                  {part}
                </span>
              ))}
            </span>
          </p>
        </div>

        <div className="sr-itinerary-card__legs">
          {model.events.map((event) => {
            if (event.kind === "subway" || event.kind === "bus") {
              return (
                <TransitLeg
                  key={event.id}
                  event={event}
                  expanded={expandedLegIds.has(event.id)}
                  onToggle={() => toggleLeg(event.id)}
                  reduceMotion={reduceMotion}
                />
              );
            }
            if (event.kind === "walk") {
              return <WalkingLeg key={event.id} event={event} />;
            }
            if (event.kind === "waypoint" || event.kind === "pickup") {
              return <WaypointStop key={event.id} event={event} />;
            }
            return null;
          })}
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
    </BorderBeam>
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
