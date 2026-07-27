"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — arrivals card

   Renders a local (display-only) arrivals turn's payload — the "tap a Near
   You bullet" flow. Same card family as ChatRouteCard: `--sr-chat-surface`,
   hairline, `--sr-radius-card`. No model call, no streaming: this is
   already-in-memory live-feed data, rendered instantly.
   ════════════════════════════════════════════════════════════════════════ */

import { MapPin } from "iconoir-react";
import { motion, useReducedMotion } from "motion/react";
import type { ArrivalsTurnPayload } from "@/lib/use-agent-chat";
import { LineBadge } from "./line-badge";
import { WalkingIcon } from "./walking-icon";

function arrivalStatusLabel(arrivals: ArrivalsTurnPayload): string | null {
  const status =
    arrivals.sourceStatus === "live"
      ? "Live"
      : arrivals.sourceStatus === "scheduled"
        ? "Scheduled"
        : arrivals.sourceStatus === "stale"
          ? "Stale"
          : null;
  if (!status) return null;
  if (!arrivals.updatedAt) return status;
  const updatedAt = new Date(arrivals.updatedAt);
  if (Number.isNaN(updatedAt.getTime())) return status;
  const clock = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
  }).format(updatedAt);
  return `${status} · updated ${clock}`;
}

export function ChatArrivalsCard({
  arrivals,
  onSeeOnMap,
}: {
  arrivals: ArrivalsTurnPayload;
  onSeeOnMap?: () => void;
}) {
  const reduceMotion = useReducedMotion() ?? false;
  const hasArrivals = arrivals.groups.length > 0;
  const emptyCopy =
    arrivals.sourceStatus === "provider_unavailable"
      ? "Live predictions are temporarily unavailable."
      : arrivals.sourceStatus === "stale"
        ? "The latest predictions are stale."
        : arrivals.sourceStatus === "stop_not_resolved"
          ? "Choose a more specific station."
          : "No current predictions for this stop.";
  const catchable = arrivals.catchability?.catchable_arrival_minutes;
  const statusLabel = arrivalStatusLabel(arrivals);

  return (
    <div className="sr-chat-arrivals-card">
      <div className="sr-chat-arrivals-card__header">
        <LineBadge line={arrivals.routeId} size={20} />
        <div className="sr-chat-arrivals-card__station-copy">
          <span className="sr-chat-arrivals-card__station">{arrivals.stationName}</span>
          {arrivals.stationGuidance ? (
            <span className="sr-chat-arrivals-card__guidance">
              <WalkingIcon className="sr-chat-arrivals-card__walk-icon" />
              {arrivals.stationGuidance}
            </span>
          ) : null}
          {statusLabel ? (
            <span className="sr-chat-arrivals-card__guidance">
              {statusLabel}
            </span>
          ) : null}
        </div>
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
          <p className="sr-chat-arrivals-card__empty">{emptyCopy}</p>
        )}
        {typeof catchable === "number" ? (
          <p className="sr-chat-arrivals-card__empty">
            {`The ${catchable} min arrival is the first one with enough walking time.`}
          </p>
        ) : null}
      </div>

      {onSeeOnMap ? (
        <motion.button
          type="button"
          className="sr-itinerary-card__map-btn sr-chat-arrivals-card__footer"
          aria-label="Open in Live Feed"
          onClick={onSeeOnMap}
          whileTap={reduceMotion ? undefined : { scale: 0.985 }}
          transition={{ duration: reduceMotion ? 0 : 0.12 }}
        >
          <MapPin width={20} height={20} strokeWidth={1.6} aria-hidden="true" />
          Open in Live Feed
        </motion.button>
      ) : null}
    </div>
  );
}
