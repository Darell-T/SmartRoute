"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — arrivals card

   Renders a local (display-only) arrivals turn's payload — the "tap a Near
   You bullet" flow. Same card family as ChatRouteCard: `--sr-chat-surface`,
   hairline, `--sr-radius-card`. No model call, no streaming: this is
   already-in-memory live-feed data, rendered instantly.
   ════════════════════════════════════════════════════════════════════════ */

import { ChevronRight } from "lucide-react";
import type { ArrivalsTurnPayload } from "@/lib/use-agent-chat";
import { LineBadge } from "./line-badge";

export function ChatArrivalsCard({
  arrivals,
  onSeeOnMap,
}: {
  arrivals: ArrivalsTurnPayload;
  onSeeOnMap?: () => void;
}) {
  const hasArrivals = arrivals.groups.length > 0;

  return (
    <div className="sr-chat-arrivals-card">
      <div className="sr-chat-arrivals-card__header">
        <LineBadge line={arrivals.routeId} size={22} />
        <span className="sr-chat-arrivals-card__station">{arrivals.stationName}</span>
      </div>

      <div className="sr-chat-arrivals-card__body">
        {hasArrivals ? (
          arrivals.groups.map((group) => (
            <div key={group.direction} className="sr-chat-arrivals-card__group">
              <span className="sr-chat-arrivals-card__direction">{group.label}</span>
              <span className="sr-chat-arrivals-card__minutes">
                {group.minutes.map((m) => `${m} min`).join(", ")}
              </span>
            </div>
          ))
        ) : (
          <p className="sr-chat-arrivals-card__empty">No arrivals reported nearby.</p>
        )}
      </div>

      <button type="button" className="sr-chat-arrivals-card__footer" onClick={onSeeOnMap}>
        See on map
        <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
      </button>
    </div>
  );
}
