"use client";

import {
  CircleCheck,
  Clock3,
  LoaderCircle,
  MapPin,
  RadioTower,
  TriangleAlert,
} from "lucide-react";
import { ArrivalCountdown } from "@/components/smart-route/left-rail/arrival-countdown";
import { LineBadge } from "./line-badge";
import type { HomeNearbyModel } from "./near-you";

const CONDITION_ICONS = {
  clear: CircleCheck,
  alert: TriangleAlert,
  loading: Clock3,
  unavailable: RadioTower,
} as const;

const ARRIVAL_SKELETONS = ["first", "second", "third"] as const;

export function HomeNearYou({
  model,
  onOpenLiveMap,
}: {
  model: HomeNearbyModel;
  onOpenLiveMap: () => void;
}) {
  const ConditionIcon = CONDITION_ICONS[model.condition.state];

  return (
    <section
      className="sr-home-nearby"
      data-arrivals-state={model.arrivalsState}
      data-arrival-count={model.arrivals.length}
      data-has-issue={model.issue ? "true" : "false"}
      data-location-state={model.locationState}
      aria-labelledby="sr-home-nearby-title"
    >
      <header className="sr-home-nearby__header">
        <MapPin
          className="sr-home-nearby__location-icon"
          size={18}
          strokeWidth={1.8}
          aria-hidden="true"
        />
        <h3 id="sr-home-nearby-title">{model.locationLabel}</h3>
        {model.stationName ? (
          <>
            <span className="sr-home-nearby__separator" aria-hidden="true">
              ·
            </span>
            <span className="sr-home-nearby__station">{model.stationName}</span>
          </>
        ) : null}
      </header>

      <div className="sr-home-nearby__arrivals">
        {model.arrivalsState === "outside_service_area" ? (
          <div className="sr-home-nearby__service-area" role="status">
            <RadioTower size={18} strokeWidth={1.7} aria-hidden="true" />
            <span>{model.locationNotice}</span>
          </div>
        ) : model.arrivalsState === "ready" ? (
          model.arrivals.map((arrival) => (
            <button
              key={arrival.id}
              type="button"
              className="sr-home-nearby__arrival"
              aria-label={`${arrival.routeId} to ${arrival.destination}, open on transit map`}
              onClick={onOpenLiveMap}
            >
              <LineBadge line={arrival.routeId} size={38} />
              <span className="sr-home-nearby__arrival-copy">
                <strong>
                  <ArrivalCountdown
                    minutes={arrival.minutes}
                    fallback="Soon"
                  />
                </strong>
                <small title={arrival.destination}>{arrival.destination}</small>
              </span>
            </button>
          ))
        ) : (
          <div className="sr-home-nearby__arrival-fallback" role="status">
            <div className="sr-home-nearby__skeletons" aria-hidden="true">
              {ARRIVAL_SKELETONS.map((skeleton) => (
                <span
                  key={skeleton}
                  className="sr-home-nearby__skeleton-arrival"
                >
                  <span className="sr-home-nearby__skeleton-bullet" />
                  <span className="sr-home-nearby__skeleton-copy">
                    <span />
                    <span />
                  </span>
                </span>
              ))}
            </div>
            <span className="sr-home-nearby__fallback-status">
              {model.arrivalsState === "loading" ? (
                <LoaderCircle
                  className="sr-home-nearby__loading-spinner"
                  size={15}
                  strokeWidth={1.7}
                  aria-hidden="true"
                />
              ) : (
                <RadioTower
                  size={15}
                  strokeWidth={1.7}
                  aria-hidden="true"
                />
              )}
              {model.arrivalsState === "loading"
                ? "Updating nearby arrivals\u2026"
                : "Nearby arrivals unavailable"}
            </span>
          </div>
        )}
      </div>

      {model.arrivalsState !== "outside_service_area" ? (
        <div
          className="sr-home-nearby__condition"
          data-state={model.condition.state}
          role={model.condition.state === "alert" ? "status" : undefined}
        >
          <ConditionIcon size={18} strokeWidth={1.7} aria-hidden="true" />
          <span title={model.condition.label}>{model.condition.label}</span>
        </div>
      ) : null}

      {model.arrivalsState !== "outside_service_area" && model.issue ? (
        <button
          type="button"
          className="sr-home-nearby__issue"
          data-confidence={model.issue.confidence}
          aria-label={`${model.issue.label}. Open details on transit map`}
          onClick={onOpenLiveMap}
        >
          <TriangleAlert size={18} strokeWidth={1.7} aria-hidden="true" />
          <span>{model.issue.label}</span>
        </button>
      ) : null}
    </section>
  );
}
