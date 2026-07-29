"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { SpiralFillLoader } from "@/components/smart-route/ui/spiral-fill-loader";
import { cleanDestinationSubmit } from "./route-view-actions";
import type { RouteReasoningInsight } from "./types";

export function RoutePlanningReasoning({
  destination,
  insights,
}: {
  destination: string;
  insights: RouteReasoningInsight[];
}) {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt);
    }, 250);
    return () => window.clearInterval(timer);
  }, []);

  // Evaluation insights surface one at a time. Every line is backed by a
  // real fact — missing facts simply never queue a line.
  const revealCount = Math.min(
    insights.length,
    1 + Math.floor(elapsedMs / 1_400),
    5,
  );
  const visibleLines = insights.slice(0, revealCount);
  const cleanedDestination = cleanDestinationSubmit(destination);
  return (
    <Reasoning className="sr-reasoning" isStreaming>
      <ReasoningTrigger className="sr-reasoning__trigger">
        <span className="sr-reasoning__status">
          <SpiralFillLoader className="shrink-0" />
          <Shimmer as="span" duration={2.2}>
            Finding routes...
          </Shimmer>
        </span>
      </ReasoningTrigger>
      <ReasoningContent className="sr-reasoning__content">
        {cleanedDestination ? (
          <span className="sr-reasoning-destination" title={cleanedDestination}>
            {cleanedDestination}
          </span>
        ) : null}
        <ol className="sr-reasoning-lines">
          <AnimatePresence initial={false}>
            {visibleLines.map((insight, index) => {
              const isLatest = index === visibleLines.length - 1;
              return (
                <motion.li
                  key={insight.id}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -3 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  data-age={isLatest ? "current" : "older"}
                >
                  {isLatest ? (
                    <Shimmer as="span" duration={2.6}>
                      {insight.text}
                    </Shimmer>
                  ) : (
                    insight.text
                  )}
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ol>
      </ReasoningContent>
    </Reasoning>
  );
}

export function RouteErrorPanel({
  onRetry,
  onClear,
}: {
  onRetry: () => void;
  onClear: () => void;
}) {
  return (
    <section className="sr-rail-section">
      <div className="sr-error-panel">
        <AlertTriangle size={20} strokeWidth={1.8} aria-hidden="true" />
        <div>
          <strong>No route found.</strong>
          <p>Try a more specific station, address, or neighborhood.</p>
        </div>
      </div>
      <div className="sr-error-actions">
        <button type="button" onClick={onRetry}>
          Try again
        </button>
        <button type="button" onClick={onClear}>
          Cancel
        </button>
      </div>
    </section>
  );
}
