"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — arrivals card

   Renders a local (display-only) arrivals turn's payload — the "tap a Near
   You bullet" flow. Same card family as ChatRouteCard: `--sr-chat-surface`,
   hairline, `--sr-radius-card`. No model call, no streaming: this is
   already-in-memory live-feed data, rendered instantly.
   ════════════════════════════════════════════════════════════════════════ */

import { MapPin, Walking } from "iconoir-react";
import type { ArrivalsTurnPayload } from "@/lib/use-agent-chat";
import { Button } from "@/components/ui/button";
import { LineBadge } from "./line-badge";

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
              <Walking width={14} height={14} strokeWidth={1.6} aria-hidden="true" />
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
        <Button
          type="button"
          variant="ghost"
          className="sr-chat-arrivals-card__footer"
          onClick={onSeeOnMap}
        >
          <MapPin width={15} height={15} strokeWidth={1.7} aria-hidden="true" />
          Open in Live Feed
        </Button>
      ) : null}
    </div>
  );
}
