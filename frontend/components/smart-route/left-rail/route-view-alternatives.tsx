"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { formatDurationLabel } from "./route-display-compat";
import { RouteStepStrip } from "./route-view-itinerary";
import type { Alternative } from "./types";

export function AlternateRoutesCollapsible({
  alternatives,
  onSelectAlternative,
}: {
  alternatives: Alternative[];
  onSelectAlternative?: (candidateId: string) => void;
}) {
  // The recommended card owns the first viewport, so alternatives can begin
  // open for comparison while this explicit control still collapses them.
  const [open, setOpen] = useState(true);
  const shouldReduceMotion = useReducedMotion();
  const hiddenState = shouldReduceMotion
    ? { opacity: 0 }
    : { opacity: 0, height: 0 };
  const visibleState = shouldReduceMotion
    ? { opacity: 1 }
    : { opacity: 1, height: "auto" };

  return (
    <motion.div className="sr-alternates" layout>
      <button
        type="button"
        className="sr-alternates__trigger"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="sr-alternates__title">Other routes</span>
        <span className="sr-alternates__meta">
          {alternatives.length} route{alternatives.length === 1 ? "" : "s"}
          <ChevronDown size={17} strokeWidth={1.8} aria-hidden="true" />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="sr-alternates__content"
            layout
            initial={hiddenState}
            animate={visibleState}
            exit={hiddenState}
            transition={{
              duration: shouldReduceMotion ? 0.01 : 0.24,
              ease: "easeOut",
            }}
          >
            <ul className="sr-alt-list" aria-label="Alternate routes">
              <AnimatePresence initial={false}>
                {alternatives.map((alternative, index) => (
                  <AlternateRouteCard
                    key={alternative.id ?? `${alternative.line}-${index}`}
                    alternative={alternative}
                    onSelectAlternative={onSelectAlternative}
                  />
                ))}
              </AnimatePresence>
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function alternatePath(alternative: Alternative): string | undefined {
  if (alternative.fromStop && alternative.toStop) {
    return `${alternative.fromStop} → ${alternative.toStop}`;
  }
  return alternative.dest;
}

function alternateLeaves(alternative: Alternative): string | null {
  if (alternative.leavesLabel) return `Leaves ${alternative.leavesLabel}`;
  if (alternative.arriveLabel) return `Arrives ${alternative.arriveLabel}`;
  return null;
}

function altMotion(reduce: boolean | null) {
  return {
    enter: reduce ? { opacity: 0 } : { opacity: 0, y: 4 },
    shown: reduce ? { opacity: 1 } : { opacity: 1, y: 0 },
    leave: reduce ? { opacity: 0 } : { opacity: 0, y: -4 },
  };
}

function AlternateRouteCopy({
  alternative,
  path,
  leaves,
  duration,
}: {
  alternative: Alternative;
  path?: string;
  leaves: string | null;
  duration: string;
}) {
  const reason = alternative.reason?.trim();
  return (
    <div className="sr-alt-row__body">
      <div className="sr-alt-row__head">
        <strong className="sr-alt-row__duration">{duration}</strong>
        {leaves && <span className="sr-alt-row__leaves">{leaves}</span>}
      </div>
      {alternative.strip && alternative.strip.length > 0 ? (
        <RouteStepStrip segments={alternative.strip} />
      ) : (
        path && <span className="sr-alt-row__path">{path}</span>
      )}
      {reason && <span className="sr-alt-row__reason">{reason}</span>}
    </div>
  );
}

function AlternateRouteCard({
  alternative,
  onSelectAlternative,
}: {
  alternative: Alternative;
  onSelectAlternative?: (candidateId: string) => void;
}) {
  const shouldReduceMotion = useReducedMotion();
  const canUse = Boolean(alternative.id && onSelectAlternative);
  const path = alternatePath(alternative);
  const leaves = alternateLeaves(alternative);
  const duration =
    typeof alternative.totalMinutes === "number"
      ? formatDurationLabel(`${alternative.totalMinutes} min`)
      : "Live";
  const motionState = altMotion(shouldReduceMotion);

  return (
    <motion.li
      className="sr-alt-row"
      layout
      initial={motionState.enter}
      animate={motionState.shown}
      exit={motionState.leave}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <AlternateRouteCopy
        alternative={alternative}
        path={path}
        leaves={leaves}
        duration={duration}
      />
      {canUse && (
        <button
          type="button"
          className="sr-use-button"
          onClick={() => onSelectAlternative?.(alternative.id!)}
          aria-label={`Use this route instead: ${path || alternative.dest}`}
        >
          Use
        </button>
      )}
    </motion.li>
  );
}
