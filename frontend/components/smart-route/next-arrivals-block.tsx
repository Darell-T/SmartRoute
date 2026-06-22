"use client";

import { useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import type {
  FocusedLiveDirection,
  LiveDirectionSummaryRow,
  NearestStop,
} from "@/types";
import { LiveStopCard } from "./live-stop-card";
import { Eyebrow } from "./eyebrow";

type Direction = "UPTOWN" | "DOWNTOWN";

interface Props {
  nearestStop: NearestStop | null;
  directionRows: LiveDirectionSummaryRow[];
  updatedAt?: number | null;
  focusedDirection?: FocusedLiveDirection | null;
  onSelectDirection?: (selection: FocusedLiveDirection) => void;
  onResetDirection?: () => void;
  onFeedActivity?: () => void;
}

const DIRECTION_OPTIONS: Array<{
  value: Direction;
  label: string;
  Icon: typeof ArrowUp;
}> = [
  { value: "UPTOWN", label: "Uptown", Icon: ArrowUp },
  { value: "DOWNTOWN", label: "Downtown", Icon: ArrowDown },
];

function walkingTimeLabel(distanceMeters?: number | null) {
  if (typeof distanceMeters !== "number" || !Number.isFinite(distanceMeters)) {
    return null;
  }

  const roundedDistance = Math.max(0, Math.round(distanceMeters));
  const seconds = roundedDistance / 1.4;
  const minutes = Math.round(seconds / 60);
  const walkLabel = minutes <= 0 ? "<1 min walk" : `${minutes} min walk`;
  return `${walkLabel} · ${roundedDistance}m`;
}

function freshnessLabel(updatedAt?: number | null) {
  if (!updatedAt) return "Awaiting feed";
  const ageSeconds = Math.max(0, Math.floor(Date.now() / 1000 - updatedAt));
  if (ageSeconds < 8) return "Updated just now";
  if (ageSeconds < 60) return `Updated ${ageSeconds}s ago`;
  if (ageSeconds < 3600) return `Updated ${Math.round(ageSeconds / 60)}m ago`;

  const date = new Date(updatedAt * 1000);
  return `As of ${date.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  })}`;
}

export function NextArrivalsBlock({
  nearestStop,
  directionRows,
  updatedAt,
  focusedDirection,
  onSelectDirection,
  onResetDirection,
  onFeedActivity,
}: Props) {
  const [direction, setDirection] = useState<Direction>("UPTOWN");
  const proximity = walkingTimeLabel(nearestStop?.distance_m);
  const freshness = freshnessLabel(updatedAt);

  function handleDirectionChange(nextDirection: Direction) {
    setDirection(nextDirection);
    onFeedActivity?.();
    onResetDirection?.();
  }

  return (
    <section className="sr-arrivals-block">
      <div className="sr-arrivals-block__header">
        <div>
          <Eyebrow>Next Arrivals</Eyebrow>
          <div className="sr-arrivals-block__station">
            {nearestStop?.stop_name ?? "Locating nearest stop"}
          </div>
        </div>
        <div className="sr-arrivals-block__meta">
          {proximity ? (
            <span className="sr-arrivals-block__distance">{proximity}</span>
          ) : null}
          <span className="sr-arrivals-block__freshness">
            <span aria-hidden="true" />
            {freshness}
          </span>
        </div>
      </div>

      <div
        className="sr-arrivals-block__segmented"
        role="tablist"
        aria-label="Arrival direction"
        data-direction={direction.toLowerCase()}
      >
        <span className="sr-arrivals-block__segmented-indicator" aria-hidden="true" />
        {DIRECTION_OPTIONS.map(({ value, label, Icon }) => {
          const active = value === direction;
          return (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={active}
              className="sr-arrivals-block__segment"
              data-active={active ? "true" : "false"}
              onClick={() => handleDirectionChange(value)}
            >
              <Icon size={16} strokeWidth={1.5} aria-hidden="true" />
              <span>{label}</span>
            </button>
          );
        })}
      </div>

      <LiveStopCard
        nearestStop={nearestStop}
        directionRows={directionRows}
        direction={direction}
        focusedDirection={focusedDirection}
        onSelectDirection={onSelectDirection}
        onResetDirection={onResetDirection}
        variant="rail"
      />
    </section>
  );
}
