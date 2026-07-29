"use client";

import { useMemo, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { BusChip, RouteBullet, RouteBulletGroup } from "./atoms";
import { ArrivalCountdown } from "./arrival-countdown";
import type {
  Arrival,
  Direction,
  NearbyGroupedArrival,
  NearbyTransitGroup,
  Station,
} from "./types";

type ArrivalFilter = Direction;

export function NearbyTransitPanel({
  station: _station,
  arrivals: _arrivals,
  nearbyTransitGroups,
  nearbyBusArrivals,
  way,
  onWayChange,
  onRequestRailExpand,
}: {
  station: Station;
  arrivals: Arrival[];
  nearbyTransitGroups: NearbyTransitGroup[];
  nearbyBusArrivals: Arrival[];
  way: ArrivalFilter;
  onWayChange: (next: ArrivalFilter) => void;
  onRequestRailExpand?: () => void;
}) {
  const groups = useMemo(
    () =>
      nearbyTransitGroups
        .map((group) => ({
          ...group,
          arrivals: group.arrivals.filter(
            (arrival) =>
              arrival.direction === way || arrival.direction === "unknown",
          ),
        }))
        .filter((group) => group.arrivals.length > 0),
    [nearbyTransitGroups, way],
  );
  const busRows = nearbyBusArrivals;
  const isEmpty = groups.length === 0 && busRows.length === 0;

  return (
    <section className="sr-nearby sr-rail-section">
      <SectionHeader
        title="Nearby transit"
        meta={
          <span className="sr-inline-meta">
            Updated just now
            <RefreshCw size={13} strokeWidth={1.8} aria-hidden="true" />
          </span>
        }
      />
      <div
        className="sr-toggle-group"
        data-way={way}
        role="radiogroup"
        aria-label="Arrival direction"
      >
        <span className="sr-toggle-pill" aria-hidden="true" />
        {(
          [
            ["uptown", "Uptown"],
            ["downtown", "Downtown"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={way === value}
            data-active={way === value ? "true" : "false"}
            onClick={() => {
              onRequestRailExpand?.();
              onWayChange(value as ArrivalFilter);
            }}
          >
            <span>{label}</span>
          </button>
        ))}
      </div>
      <div className="sr-nearby-scroll">
        <NearbyStationGroupList groups={groups} />
        {busRows.length > 0 && (
          <section className="sr-nearby-buses" aria-label="Nearby buses">
            <h3 className="sr-nearby-subhead">Nearby buses</h3>
            <ul className="sr-arrival-list sr-arrival-list--buses">
              <AnimatePresence initial={false}>
                {busRows.map((arrival) => (
                  <ArrivalRow key={arrival.id} arrival={arrival} />
                ))}
              </AnimatePresence>
            </ul>
          </section>
        )}
        {isEmpty && (
          <div className="sr-empty-row">
            <strong>No {way} subway arrivals nearby</strong>
            <small>
              Try {way === "uptown" ? "Downtown" : "Uptown"} or refresh live
              data.
            </small>
          </div>
        )}
      </div>
    </section>
  );
}

function NearbyStationGroupList({ groups }: { groups: NearbyTransitGroup[] }) {
  return (
    <div className="sr-station-group-list">
      <AnimatePresence initial={false}>
        {groups.map((group) => (
          <NearbyStationGroup key={group.id} group={group} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function NearbyStationGroup({ group }: { group: NearbyTransitGroup }) {
  return (
    <motion.article
      className="sr-station-group"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <StationGroupHeader group={group} />
      <ul className="sr-station-arrival-list">
        <AnimatePresence initial={false}>
          {group.arrivals.map((arrival) => (
            <StationArrivalRow key={arrival.id} arrival={arrival} />
          ))}
        </AnimatePresence>
      </ul>
    </motion.article>
  );
}

function StationGroupHeader({ group }: { group: NearbyTransitGroup }) {
  const meta = [
    typeof group.walkMinutes === "number"
      ? `${group.walkMinutes} min walk`
      : null,
    typeof group.distanceMiles === "number"
      ? `${group.distanceMiles.toFixed(1)} mi`
      : null,
  ].filter(Boolean);

  return (
    <header className="sr-station-header">
      <span className="sr-station-header__title">
        <strong>{group.name}</strong>
        {meta.length > 0 && (
          <span className="sr-station-header__walk">{meta.join(" · ")}</span>
        )}
      </span>
      <RouteBulletGroup lines={group.routeIds} size={19} limit={6} />
    </header>
  );
}

function StationArrivalRow({ arrival }: { arrival: NearbyGroupedArrival }) {
  const routeId = arrival.routeIds[0] ?? "";
  const details = [arrival.servicePattern, arrival.via].filter(Boolean);

  return (
    <motion.li
      className="sr-station-arrival-row"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <span className="sr-station-arrival-row__media">
        <RouteBullet line={routeId} size={28} />
      </span>
      <span className="sr-station-arrival-row__copy">
        <strong>{arrival.destination}</strong>
        {details.length > 0 && <small>{details.join(" · ")}</small>}
      </span>
      <span className="sr-station-arrival-row__time">
        <strong>
          <ArrivalCountdown
            minutes={arrival.arrivalMinutes}
            fallback="Soon"
            className="sr-arrival-countdown"
          />
        </strong>
        <PredictionStatus
          predictionType={arrival.predictionType}
          predictionFreshness={arrival.predictionFreshness}
          alertSeverity={arrival.alertSeverity}
        />
      </span>
    </motion.li>
  );
}

function ArrivalRow({ arrival }: { arrival: Arrival }) {
  const routeId = arrival.routeIds[0] ?? arrival.line;
  const details = [
    arrival.servicePattern,
    arrival.stopName,
    typeof arrival.walkMinutes === "number"
      ? `${arrival.walkMinutes} min walk`
      : null,
  ].filter(Boolean);

  return (
    <motion.li
      className="sr-arrival-row"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <span className="sr-arrival-row__media">
        {arrival.mode === "bus" ? (
          <BusChip route={routeId} />
        ) : (
          <RouteBullet line={routeId} size={30} />
        )}
      </span>
      <span className="sr-arrival-row__copy">
        <strong>{arrival.destination}</strong>
        {details.length > 0 && <small>{details.join(" · ")}</small>}
      </span>
      <span className="sr-arrival-row__time">
        <strong>
          <ArrivalCountdown
            minutes={arrival.arrivalMinutes}
            fallback={arrival.label}
            className="sr-arrival-countdown"
          />
        </strong>
        <PredictionStatus
          predictionType={arrival.predictionType}
          predictionFreshness={arrival.predictionFreshness}
          alertSeverity={arrival.alertSeverity}
        />
      </span>
    </motion.li>
  );
}

export function PredictionStatus({
  predictionType,
  predictionFreshness,
  alertSeverity,
}: {
  predictionType?: Arrival["predictionType"];
  predictionFreshness?: Arrival["predictionFreshness"];
  alertSeverity?: Arrival["alertSeverity"];
}) {
  const state =
    alertSeverity && alertSeverity !== "none"
      ? "warning"
      : predictionType === "scheduled" || predictionFreshness === "scheduled"
        ? "scheduled"
        : predictionFreshness === "stale"
          ? "stale"
          : "fresh";
  const label =
    state === "warning"
      ? "Affected by service alert"
      : state === "scheduled"
        ? "Scheduled estimate"
        : state === "stale"
          ? "Older live arrival prediction"
          : "Live arrival prediction";

  return (
    <span
      className="sr-prediction-status"
      data-state={state}
      aria-label={label}
      title={label}
    >
      <PredictionSignalIcon state={state} />
    </span>
  );
}

function PredictionSignalIcon({
  state,
}: {
  state: "fresh" | "stale" | "scheduled" | "warning";
}) {
  // Fresh predictions show both arcs; stale dims the outer arc; scheduled is
  // the dot alone.
  const hasArcs = state !== "scheduled";
  const outerOpacity = state === "stale" ? 0.35 : 1;
  return (
    <svg
      className="sr-signal-icon"
      viewBox="0 0 24 24"
      width={16}
      height={16}
      fill="none"
      aria-hidden="true"
    >
      <g transform="rotate(-45 12 12)">
        <circle cx="12" cy="19" r="1.4" fill="currentColor" />
        {hasArcs && (
          <path
            d="M8.4 15.6a5 5 0 0 1 7.2 0"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        )}
        {hasArcs && (
          <path
            d="M5.3 12.5a9 9 0 0 1 13.4 0"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            opacity={outerOpacity}
          />
        )}
      </g>
    </svg>
  );
}

function SectionHeader({ title, meta }: { title: string; meta?: ReactNode }) {
  return (
    <div className="sr-section-header">
      <h2>{title}</h2>
      {meta && <span>{meta}</span>}
    </div>
  );
}
