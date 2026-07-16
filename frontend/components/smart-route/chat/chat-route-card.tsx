"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — route card

   Renders one `route_card` SSE event: line bullets, ETA, transfers, and the
   model's one-line reason, in the same Cupertino card language as the left
   rail's recommended-route card (route-view.tsx). The whole card is a
   button — tapping it is "tap-to-view-on-map" (the note at the end of the
   plan says this replaces today's "Use" button); it also calls
   `chat.selectCard(card.card_id)` so a follow-up like "actually the second
   one" resolves against the right card server-side.
   ════════════════════════════════════════════════════════════════════════ */

import { RouteBullet, BusChip } from "@/components/smart-route/left-rail/atoms";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";

function formatClockTime(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
}

/** First departure / last arrival timestamp found on the card's steps, for
 *  turns that plan a future departure (design correction #1 in the plan:
 *  cards render absolute times when a `departure_time` was set). Falls back
 *  to the card-level `depart_iso` for the departure side. */
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

function LineBadge({ line }: { line: string }) {
  const routeId = line.toUpperCase();
  return SUBWAY_BULLET_ROUTES.has(routeId) ? (
    <RouteBullet key={routeId} line={routeId} size={22} />
  ) : (
    <BusChip key={routeId} route={routeId} />
  );
}

export function ChatRouteCard({
  card,
  onSelect,
}: {
  card: RouteCardData;
  onSelect?: (card: RouteCardData) => void;
}) {
  const isRecommended = card.role === "recommended";
  const { departs, arrives } = findLegTimes(card);
  const transfers = card.summary.transfers;

  return (
    <button
      type="button"
      className="sr-chat-route-card"
      data-role={card.role}
      onClick={() => onSelect?.(card)}
      aria-label={`${isRecommended ? "Recommended route" : "Alternative route"}: ${card.summary.eta_minutes} minutes, ${card.destination.label}`}
    >
      <div className="sr-chat-route-card__top">
        <span className="sr-chat-route-card__lines">
          {card.summary.lines.map((line) => (
            <LineBadge key={line} line={line} />
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
        {departs && <span>Departs {departs}</span>}
        {arrives && <span>Arrives {arrives}</span>}
      </div>

      {card.summary.reason && <p className="sr-chat-route-card__reason">{card.summary.reason}</p>}
    </button>
  );
}

export function ChatRouteCardList({
  cards,
  onSelect,
}: {
  cards: RouteCardData[];
  onSelect?: (card: RouteCardData) => void;
}) {
  if (cards.length === 0) return null;
  const recommended = cards.filter((card) => card.role === "recommended");
  const alternatives = cards.filter((card) => card.role !== "recommended");

  return (
    <div className="sr-chat-route-cards">
      {recommended.map((card) => (
        <ChatRouteCard key={card.card_id} card={card} onSelect={onSelect} />
      ))}
      {alternatives.length > 0 && (
        <div className="sr-chat-route-cards__alternatives">
          {alternatives.map((card) => (
            <ChatRouteCard key={card.card_id} card={card} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}
