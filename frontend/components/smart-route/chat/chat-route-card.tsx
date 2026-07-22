"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — route card

   Renders one `route_card` SSE event: line bullets, hero ETA, meta line,
   and the model's one-line reason, in the chat tab's own token surface
   (`--sr-chat-surface`, `--sr-radius-card`). The whole card is a button —
   tapping it selects the card (`chat.selectCard`, for a follow-up like
   "actually the second one" to resolve server-side) and hands off to the
   caller for the card→map jump (stubbed here; wired by W-B).
   ════════════════════════════════════════════════════════════════════════ */

import { ChevronRight } from "lucide-react";
import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";
import { Button } from "@/components/ui/button";
import { LineBadge } from "./line-badge";

function formatClockTime(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
}

/** First departure / last arrival timestamp found on the card's steps, for
 *  turns that plan a future departure. Falls back to the card-level
 *  `depart_iso` for the departure side. */
function findLegTimes(card: RouteCardData): { departs: string | null; arrives: string | null } {
  let departs: string | null = card.depart_iso ? formatClockTime(card.depart_iso) : null;
  let arrives: string | null = null;
  for (const step of card.route) {
    const withTimes = step as { departure_time_iso?: string; arrival_time_iso?: string };
    if (!departs && withTimes.departure_time_iso) departs = formatClockTime(withTimes.departure_time_iso);
    if (withTimes.arrival_time_iso) {
      const formatted = formatClockTime(withTimes.arrival_time_iso);
      if (formatted) arrives = formatted;
    }
  }
  return { departs, arrives };
}

function legTimeLabel(departs: string | null, arrives: string | null): string | null {
  if (departs && arrives) return `${departs} to ${arrives}`;
  return departs ?? arrives;
}

export function ChatRouteCard({
  card,
  isSelected = false,
  landDelayMs = 0,
  onSelect,
}: {
  card: RouteCardData;
  isSelected?: boolean;
  /** Stagger offset for the "cards land" entrance (60ms per card, once). */
  landDelayMs?: number;
  onSelect?: (card: RouteCardData) => void;
}) {
  const isRecommended = card.role === "recommended";
  const { departs, arrives } = findLegTimes(card);
  const timeLabel = legTimeLabel(departs, arrives);
  const transfers = card.summary.transfers;

  return (
    <Button
      type="button"
      variant="ghost"
      className="sr-chat-route-card"
      data-role={card.role}
      data-selected={isSelected ? "true" : "false"}
      style={{ animationDelay: `${landDelayMs}ms` }}
      onClick={() => onSelect?.(card)}
      aria-label={`${isRecommended ? "Recommended route" : "Alternative route"}: ${card.summary.eta_minutes} minutes, ${card.destination.label}`}
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
        {isRecommended && <span className="sr-chat-route-card__pill">Recommended</span>}
        {card.leg_label && <span className="sr-chat-route-card__leg">{card.leg_label}</span>}
      </div>

      <div className="sr-chat-route-card__hero">
        <strong>{card.summary.eta_minutes} min</strong>
        <span className="sr-chat-route-card__dest">to {card.destination.label}</span>
      </div>

      <div className="sr-chat-route-card__meta">
        <span>
          {transfers} transfer{transfers === 1 ? "" : "s"}
        </span>
        {timeLabel && <span>{timeLabel}</span>}
      </div>

      {card.summary.reason && <p className="sr-chat-route-card__reason">{card.summary.reason}</p>}

      <span className="sr-chat-route-card__view-on-map">
        View on map
        <ChevronRight size={13} strokeWidth={2} aria-hidden="true" />
      </span>
    </Button>
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
      {recommended.map((card, index) => (
        <ChatRouteCard
          key={card.card_id}
          card={card}
          isSelected={card.card_id === selectedCardId}
          onSelect={onSelect}
          landDelayMs={index * 60}
        />
      ))}
      {alternatives.length > 0 && (
        <div className="sr-chat-route-cards__alternatives">
          {alternatives.map((card, index) => (
            <ChatRouteCard
              key={card.card_id}
              card={card}
              isSelected={card.card_id === selectedCardId}
              onSelect={onSelect}
              landDelayMs={(recommended.length + index) * 60}
            />
          ))}
        </div>
      )}
    </div>
  );
}
