"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — "Near You" line-bullet row

   The empty state's proof of live data: up to 6 real MTA bullets for the
   routes near the rider, plus a "…" overflow chip. Tapping a bullet appends
   a local (no-model-call) arrivals turn to the thread; tapping the row
   label or the overflow chip jumps to the Live Map tab for the full feed.
   ════════════════════════════════════════════════════════════════════════ */

import { LineBadge } from "./line-badge";

const MAX_VISIBLE_BULLETS = 6;

export function NearYouRow({
  routeIds,
  onSelectRoute,
  onOpenLiveMap,
}: {
  routeIds: string[];
  onSelectRoute: (routeId: string) => void;
  onOpenLiveMap: () => void;
}) {
  if (routeIds.length === 0) return null;

  const visible = routeIds.slice(0, MAX_VISIBLE_BULLETS);
  const overflowCount = routeIds.length - visible.length;

  return (
    <div className="sr-chat-near-you">
      <button type="button" className="sr-chat-near-you__label" onClick={onOpenLiveMap}>
        Near you
      </button>
      <div className="sr-chat-near-you__bullets">
        {visible.map((routeId) => (
          <button
            key={routeId}
            type="button"
            className="sr-chat-near-you__bullet"
            aria-label={`${routeId} arrivals near you`}
            onClick={() => onSelectRoute(routeId)}
          >
            <LineBadge line={routeId} size={22} />
          </button>
        ))}
        {overflowCount > 0 && (
          <button
            type="button"
            className="sr-chat-near-you__overflow"
            aria-label="See all nearby routes on the live map"
            onClick={onOpenLiveMap}
          >
            …
          </button>
        )}
      </div>
    </div>
  );
}
