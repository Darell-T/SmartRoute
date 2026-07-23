"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — route card list

   Recommended journeys use the redesigned itinerary card (flat charcoal,
   timeline, official MTA bullets). Alternatives stay compact so the chat
   column is not dominated by repeated full itineraries.
   ════════════════════════════════════════════════════════════════════════ */

import { ChevronRight } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";
import { Button } from "@/components/ui/button";
import { LineBadge } from "./line-badge";
import { RecommendedItineraryFromCards } from "./recommended-itinerary-card";
import { formatClockTime, formatDurationMinutes } from "./itinerary-view-model";

const EASE_OUT = [0.16, 1, 0.3, 1] as const;

function findArrivalLabel(card: RouteCardData): string | null {
  for (let i = card.route.length - 1; i >= 0; i -= 1) {
    const step = card.route[i] as { arrival_time_iso?: string };
    const label = formatClockTime(step.arrival_time_iso);
    if (label) return label;
  }
  return null;
}

/** Compact alternative card — same family as the recommended itinerary,
 *  deliberately lighter so riders can scan options quickly. */
export function ChatRouteCard({
  card,
  isSelected = false,
  landDelayMs = 0,
  onSelect,
}: {
  card: RouteCardData;
  isSelected?: boolean;
  landDelayMs?: number;
  onSelect?: (card: RouteCardData) => void;
}) {
  const reduceMotion = useReducedMotion() ?? false;
  const arrives = findArrivalLabel(card);
  const transfers = card.summary.transfers;
  const duration = formatDurationMinutes(card.summary.eta_minutes);

  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.32,
        delay: reduceMotion ? 0 : landDelayMs / 1000,
        ease: EASE_OUT,
      }}
    >
      <Button
        type="button"
        variant="ghost"
        className="sr-chat-route-card"
        data-role={card.role}
        data-selected={isSelected ? "true" : "false"}
        onClick={() => onSelect?.(card)}
        aria-label={`Alternative route: ${duration}, ${card.destination.label}`}
      >
        <div className="sr-chat-route-card__top">
          <span className="sr-chat-route-card__lines">
            {card.summary.lines.map((line, index) => (
              <span key={line} className="sr-chat-route-card__line-item">
                {index > 0 && (
                  <ChevronRight
                    size={12}
                    strokeWidth={2}
                    className="sr-chat-route-card__leg-chevron"
                    aria-hidden="true"
                  />
                )}
                <LineBadge line={line} size={20} />
              </span>
            ))}
          </span>
          {card.leg_label && <span className="sr-chat-route-card__leg">{card.leg_label}</span>}
        </div>

        <div className="sr-chat-route-card__hero">
          <strong>{duration}</strong>
          <span className="sr-chat-route-card__dest">to {card.destination.label}</span>
        </div>

        <div className="sr-chat-route-card__meta">
          <span>
            {transfers} transfer{transfers === 1 ? "" : "s"}
          </span>
          {arrives && <span>Arrive {arrives}</span>}
        </div>

        {card.summary.reason && (
          <p className="sr-chat-route-card__reason">{card.summary.reason}</p>
        )}

        <span className="sr-chat-route-card__view-on-map">
          Open on map
          <ChevronRight size={13} strokeWidth={2} aria-hidden="true" />
        </span>
      </Button>
    </motion.div>
  );
}

export function ChatRouteCardList({
  cards,
  selectedCardId,
  onSelect,
}: {
  cards: RouteCardData[];
  selectedCardId?: string | null;
  onSelect?: (card: RouteCardData) => void;
}) {
  if (cards.length === 0) return null;

  const recommended = cards.filter((card) => card.role === "recommended");
  const alternatives = cards.filter((card) => card.role !== "recommended");

  return (
    <div className="sr-chat-route-cards">
      {recommended.length > 0 && (
        <RecommendedItineraryFromCards
          cards={recommended}
          selectedCardId={selectedCardId}
          onSelect={onSelect}
          landDelayMs={0}
          primaryActionLabel="Open on map"
          secondaryActionLabel="View steps"
        />
      )}
      {alternatives.length > 0 && (
        <div className="sr-chat-route-cards__alternatives">
          {alternatives.map((card, index) => (
            <ChatRouteCard
              key={card.card_id}
              card={card}
              isSelected={card.card_id === selectedCardId}
              onSelect={onSelect}
              landDelayMs={(index + 1) * 50}
            />
          ))}
        </div>
      )}
    </div>
  );
}
