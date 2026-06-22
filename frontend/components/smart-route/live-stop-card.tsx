"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type {
  FocusedLiveDirection,
  LiveArrival,
  LiveDirectionSummaryRow,
  NearestStop,
} from "@/types";
import { TrainBullet } from "@/components/smart-route/train-bullet";
import { AnimatedList } from "@/components/ui/animated-list";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";

interface Props {
  nearestStop: NearestStop | null;
  directionRows: LiveDirectionSummaryRow[];
  direction?: "UPTOWN" | "DOWNTOWN";
  focusedDirection?: FocusedLiveDirection | null;
  onSelectDirection?: (selection: FocusedLiveDirection) => void;
  onResetDirection?: () => void;
  mapRevealSuppressed?: boolean;
  variant?: "card" | "rail";
}

const VISIBLE_ROW_CAP = 4;
const NESTED_TIME_CAP = 4;
const LIVE_FEED_EASE = [0.16, 1, 0.3, 1] as const;

function isBusRow(row: LiveDirectionSummaryRow) {
  return row.arrivals.some((arrival) => arrival.mode === "bus");
}

function diffMinutes(arrivalTime: number) {
  const seconds = arrivalTime - Math.floor(Date.now() / 1000);
  return Math.max(0, Math.round(seconds / 60));
}

function arrivalLabel(arrivalTime: number) {
  const minutes = diffMinutes(arrivalTime);
  if (minutes <= 0) return "Now";
  if (minutes === 1) return "1 min";
  return `${minutes} min`;
}

function arrivalCompact(arrivalTime: number) {
  const minutes = diffMinutes(arrivalTime);
  if (minutes <= 0) return "Now";
  return `${minutes} min`;
}

function statusFromArrival(arrival: LiveArrival & { arrival_time: number }) {
  const delay = arrival.delay ?? 0;
  // Backend reports delays in seconds. Anything > 90s reads as Delayed in NYC subway operations.
  if (delay >= 90) return "Delayed" as const;
  return "On time" as const;
}

function selectionForRow(row: LiveDirectionSummaryRow): FocusedLiveDirection {
  return {
    routeId: row.routeId,
    direction: row.direction,
    terminalKey: row.terminalKey,
  };
}

function arrivalTimeKey(
  row: LiveDirectionSummaryRow,
  arrival: LiveArrival & { arrival_time: number },
) {
  return [
    row.routeId,
    row.direction,
    row.terminalKey,
    arrival.trip_id ?? arrival.stop_id ?? arrival.terminal_stop_id ?? "arrival",
    arrival.arrival_time,
  ].join("-");
}

function usePromotionPulse(value: string) {
  const previousRef = useRef(value);
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (previousRef.current === value) return;
    previousRef.current = value;
    setActive(true);
    const id = window.setTimeout(() => setActive(false), 820);
    return () => window.clearTimeout(id);
  }, [value]);

  return active;
}

function shortenTerminal(label: string) {
  // "Forest Hills - 71 Av" → "71 Av"; "168 St" → "168 St"; default to last segment
  const trimmed = label.trim();
  if (!trimmed) return trimmed;
  const parts = trimmed.split(/\s+[-–—]\s+/);
  return (parts[parts.length - 1] || trimmed).trim();
}

function directionTitle(direction: string) {
  if (direction === "UPTOWN") return "Uptown";
  if (direction === "DOWNTOWN") return "Downtown";
  return direction.charAt(0).toUpperCase() + direction.slice(1).toLowerCase();
}

function destinationTitle(row: LiveDirectionSummaryRow) {
  const direction = directionTitle(row.direction);
  const destination = row.destinationLabel || shortenTerminal(row.terminalLabel);
  if (!destination || destination.toLowerCase() === direction.toLowerCase()) {
    return "";
  }
  return destination;
}

function selectVisibleRows(rows: LiveDirectionSummaryRow[]) {
  const sorted = [...rows].sort((a, b) => a.nextArrivalTime - b.nextArrivalTime);
  const visible = sorted.slice(0, VISIBLE_ROW_CAP);

  if (!visible.some(isBusRow)) {
    const firstBus = sorted.find(isBusRow);
    if (firstBus) {
      if (visible.length >= VISIBLE_ROW_CAP) {
        visible[visible.length - 1] = firstBus;
      } else {
        visible.push(firstBus);
      }
    }
  }

  return visible.sort((a, b) => a.nextArrivalTime - b.nextArrivalTime);
}

export function LiveStopCard({
  nearestStop,
  directionRows,
  direction,
  focusedDirection = null,
  onSelectDirection,
  onResetDirection,
  variant = "card",
}: Props) {
  // Filter by direction (when locked from parent), otherwise show all
  const filteredRows = useMemo(
    () =>
      direction
        ? directionRows.filter((row) => row.direction === direction || isBusRow(row))
        : directionRows,
    [direction, directionRows],
  );

  // Sort earliest-arriving first, but keep at least one nearby bus visible
  // when the live feed has bus arrivals inside the radius.
  const visibleRows = useMemo(
    () => selectVisibleRows(filteredRows),
    [filteredRows],
  );

  const stationName = nearestStop?.stop_name ?? "";
  const openValue = focusedDirection
    ? `${focusedDirection.routeId}-${focusedDirection.terminalKey}`
    : "";

  function handleAccordionChange(nextValue: string) {
    if (!nextValue) {
      onResetDirection?.();
      return;
    }
    const matched = visibleRows.find(
      (row) => `${row.routeId}-${row.terminalKey}` === nextValue,
    );
    if (matched && onSelectDirection) {
      onSelectDirection(selectionForRow(matched));
    }
  }

  // Empty state
  if (!nearestStop && directionRows.length === 0) {
    if (variant === "rail" && direction) {
      return (
        <div className="sr-arrivals-list sr-arrivals-list--empty">
          Locating nearest stop…
        </div>
      );
    }
    return variant === "rail" ? (
      <section className="sr-intel-section sr-intel-section--nearest">
        <div className="sr-intel-empty">
          Waiting for location signal to anchor the nearest live platform feed.
        </div>
      </section>
    ) : (
      <Card variant="ghost">
        <CardContent className="px-4 py-5 text-center text-sm text-white/40">
          Locating nearest stop…
        </CardContent>
      </Card>
    );
  }

  if (visibleRows.length === 0) {
    return (
      <div className="sr-arrivals-list sr-arrivals-list--empty">
        No {direction ? direction.toLowerCase() : ""} arrivals scheduled right now.
      </div>
    );
  }

  const list = (
    <Accordion
      type="single"
      collapsible
      value={openValue}
      onValueChange={handleAccordionChange}
      className="sr-arrivals-list"
    >
      {visibleRows.map((row) => (
        <ArrivalGroupRow
          key={`${row.routeId}-${row.terminalKey}`}
          row={row}
          stationName={stationName}
          selected={`${row.routeId}-${row.terminalKey}` === openValue}
        />
      ))}
    </Accordion>
  );

  // Variant-aware wrapping
  if (variant === "rail" && direction) {
    return list;
  }

  if (variant === "rail") {
    return (
      <section className="sr-intel-section sr-intel-section--nearest">
        {list}
      </section>
    );
  }

  return (
    <Card variant="ghost" className="overflow-hidden animate-[srCardIn_280ms_ease-out]">
      {nearestStop ? (
        <CardHeader className="flex-row items-start gap-3 px-4 pt-3.5 pb-2.5 border-b border-white/[0.05] space-y-0">
          <div className="min-w-0 flex-1">
            <p
              className="text-white font-semibold leading-tight"
              style={{
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 13,
              }}
            >
              {nearestStop.stop_name}
            </p>
            <p
              className="mt-0.5 text-white/42"
              style={{
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 10.5,
              }}
            >
              {Math.round(nearestStop.distance_m)}m away
            </p>
          </div>
        </CardHeader>
      ) : null}
      <CardContent className="px-4 pt-3 pb-3.5">{list}</CardContent>
    </Card>
  );
}

function ArrivalGroupRow({
  row,
  stationName,
  selected,
}: {
  row: LiveDirectionSummaryRow;
  stationName: string;
  selected: boolean;
}) {
  const [primary, ...rest] = row.arrivals;
  const primaryKey = primary ? arrivalTimeKey(row, primary) : `${row.key}-empty`;
  const primaryPromoted = usePromotionPulse(primaryKey);
  const reduceMotion = useReducedMotion();

  if (!primary) return null;

  const status = statusFromArrival(primary);
  const destination = destinationTitle(row);
  const itemValue = `${row.routeId}-${row.terminalKey}`;
  const additional = rest.slice(0, NESTED_TIME_CAP);

  return (
    <AccordionItem
      value={itemValue}
      className="sr-arrival-group sr-arrival-group--no-border"
      data-selected={selected ? "true" : "false"}
      data-updating={primaryPromoted ? "true" : "false"}
    >
      <AccordionTrigger
        className="sr-arrival-group__trigger"
        data-selected={selected ? "true" : "false"}
      >
        <span className="sr-arrival-group__bullet shrink-0">
          <TrainBullet line={row.routeId} size={28} />
        </span>

        <span className="sr-arrival-group__main">
          <span className="sr-arrival-group__title">
            {directionTitle(row.direction)}
            {destination ? (
              <>
                <span aria-hidden="true">{" · "}</span>
                {destination}
              </>
            ) : null}
          </span>
          {stationName ? (
            <span className="sr-arrival-group__subtitle">
              Departing {stationName}
            </span>
          ) : null}
        </span>

        <span className="sr-arrival-group__time-cluster">
          <span
            className="sr-arrival-group__time"
            data-status={status === "Delayed" ? "delayed" : "on-time"}
            data-updating={primaryPromoted ? "true" : "false"}
          >
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span
                key={primaryKey}
                className="sr-arrival-group__time-value"
                initial={reduceMotion ? { opacity: 0.82 } : { opacity: 0, y: 6 }}
                animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
                exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -7 }}
                transition={{
                  duration: reduceMotion ? 0.08 : 0.34,
                  ease: LIVE_FEED_EASE,
                }}
              >
                {arrivalLabel(primary.arrival_time)}
              </motion.span>
            </AnimatePresence>
          </span>
          <span
            className="sr-arrival-group__status"
            data-status={status === "Delayed" ? "delayed" : "on-time"}
          >
            {status}
          </span>
        </span>

      </AccordionTrigger>

      <AccordionContent className="sr-arrival-group__content">
        {additional.length === 0 ? (
          <div className="sr-arrival-group__nested-empty">
            No additional times scheduled.
          </div>
        ) : (
          <div className="sr-arrival-group__nested-wrap">
            <div className="sr-arrival-group__nested-heading">
              Next {isBusRow(row) ? "buses" : "trains"}
            </div>
            <AnimatedList
              className="sr-arrival-group__nested"
              delay={85}
              reverseOrder={false}
            >
              {additional.map((arr) => {
                const arrStatus = statusFromArrival(arr);
                return (
                  <div
                    key={arrivalTimeKey(row, arr)}
                    className="sr-arrival-group__nested-item"
                    data-entering="true"
                  >
                    <span
                      className="sr-arrival-group__nested-time"
                      data-status={arrStatus === "Delayed" ? "delayed" : "on-time"}
                    >
                      {arrivalCompact(arr.arrival_time)}
                    </span>
                    <span
                      className="sr-arrival-group__nested-status"
                      data-status={arrStatus === "Delayed" ? "delayed" : "on-time"}
                    >
                      {arrStatus}
                    </span>
                  </div>
                );
              })}
            </AnimatedList>
          </div>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}
