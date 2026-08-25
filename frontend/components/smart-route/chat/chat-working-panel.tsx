"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — working panel

   The stock AI Elements Reasoning presentation (components/ai-elements/
   reasoning.tsx — vendored, Streamdown stripped), unthemed beyond the six
   semantic route stages with a quiet transition while the turn streams, auto-open,
   auto-collapse to "Worked for Ns" one second after the turn ends. Inside:
   quiet tool rows (lucide status glyph + server-provided label + duration),
   13px, lowercase, no color — the plan is explicit these are not a second
   surface competing with route cards for attention.

   Superseded `tool-chip.tsx` (deleted) — tool rows now live inside this
   collapsible instead of as a standalone chip row above the prose.
   ════════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import type { ToolChip as ToolChipData } from "@/lib/use-agent-chat";
import { isHiddenActivityTool, isSearchActivityTool } from "@/lib/agent-route-tools";

const PROGRESS_COPY = {
  finding_routes: "Finding viable routes",
  checking_live_conditions: "Checking live service and current incidents",
  comparing_options: "Deliberating between the best options",
} as const;

export function workingPanelTriggerLabel({
  isStreaming,
  reasoning,
  progress,
  toolChips,
}: {
  isStreaming: boolean;
  reasoning: string;
  progress?: { stage: keyof typeof PROGRESS_COPY; status: "active" | "complete" };
  toolChips: ToolChipData[];
}): string {
  const progressLabel = progress?.status === "active" ? PROGRESS_COPY[progress.stage] : null;
  const activeSearch = toolChips.findLast(
    (chip) => isSearchActivityTool(chip.tool) && chip.status === "running",
  );
  if (progressLabel) return progressLabel;
  if (activeSearch) return activeSearch.label || "Thinking through your request…";
  const intentLabel = reasoning.split("\n")[0]?.trim();
  if (intentLabel) return intentLabel;
  return isStreaming ? "Thinking through your request…" : "Done";
}

export function workingPanelDetailText(reasoning: string, triggerLabel: string): string {
  const seen = new Set<string>();
  const trigger = triggerLabel.trim();
  return reasoning
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => {
      if (!line || line === trigger || seen.has(line)) return false;
      seen.add(line);
      return true;
    })
    .join("\n");
}

function ToolRow({ chip }: { chip: ToolChipData }) {
  return (
    <div className="sr-chat-tool-row" data-status={chip.status}>
      {chip.status === "running" ? (
        <Loader2 size={13} strokeWidth={2} className="sr-chat-tool-row__spinner" aria-hidden="true" />
      ) : chip.status === "ok" ? (
        <span className="sr-chat-tool-row__state-dot" aria-hidden="true" />
      ) : (
        <X size={13} strokeWidth={2} aria-hidden="true" />
      )}
      <span className="sr-chat-tool-row__label">
        {chip.label}
        {typeof chip.durationMs === "number" ? ` · ${(chip.durationMs / 1000).toFixed(1)}s` : ""}
      </span>
    </div>
  );
}

/** "Has this turn ever streamed" — purely derived from the `isStreaming`
 *  prop, so it uses React's documented adjust-state-during-render pattern
 *  (same technique the vendored `Reasoning` component itself uses for its
 *  open/close state): a same-render `setState` on a prop-edge, no effect.
 *  Keeps the collapsed "Worked for Ns" summary visible after `done` instead
 *  of the panel vanishing the instant streaming ends. */
function useEverStreamed(isStreaming: boolean): boolean {
  const [prevStreaming, setPrevStreaming] = useState(isStreaming);
  const [everStreamed, setEverStreamed] = useState(isStreaming);

  if (prevStreaming !== isStreaming) {
    setPrevStreaming(isStreaming);
    if (isStreaming) setEverStreamed(true);
  }

  return everStreamed;
}

/** Seconds the turn has been (or was) streaming, `undefined` until it's had
 *  at least one full open/close cycle. Rounds up to at least 1s so a
 *  same-tick response never reads as "Worked for 0 seconds." Unlike
 *  `useEverStreamed`, this genuinely needs a `useEffect` — the wall clock is
 *  an external system, not something derivable from props during render
 *  (`Date.now()` is impure and may not be called from a render body). */
function useElapsedSeconds(isStreaming: boolean): number | undefined {
  const startedAtRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState<number | undefined>(undefined);

  useEffect(() => {
    if (isStreaming) {
      startedAtRef.current = Date.now();
      return;
    }
    if (startedAtRef.current !== null) {
      setElapsed(Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000)));
      startedAtRef.current = null;
    }
  }, [isStreaming]);

  return elapsed;
}

export function ChatWorkingPanel({
  toolChips,
  progress,
  reasoning,
  isStreaming,
}: {
  toolChips: ToolChipData[];
  progress?: { stage: keyof typeof PROGRESS_COPY; status: "active" | "complete" };
  reasoning: string;
  isStreaming: boolean;
}) {
  const everStreamed = useEverStreamed(isStreaming);
  const elapsedSeconds = useElapsedSeconds(isStreaming);
  const reduceMotion = useReducedMotion() ?? false;
  const hasStarted = everStreamed || toolChips.length > 0 || reasoning.length > 0;
  const streamingLabel = workingPanelTriggerLabel({
    isStreaming,
    reasoning,
    progress,
    toolChips,
  });
  const detailText = workingPanelDetailText(reasoning, streamingLabel);
  const visibleToolChips = toolChips.filter(
    (chip) =>
      !isHiddenActivityTool(chip.tool)
      && !(chip.status === "running" && chip.label === streamingLabel),
  );
  if (!hasStarted) return null;

  return (
    <Reasoning
      className="sr-chat-working-panel"
      isStreaming={isStreaming}
      duration={elapsedSeconds}
      aria-live="polite"
      aria-busy={isStreaming}
    >
      <ReasoningTrigger className="sr-chat-working-panel__trigger">
        {isStreaming ? (
          progress?.status === "active" || toolChips.some(
            (chip) => isSearchActivityTool(chip.tool) && chip.status === "running",
          ) ? (
            <AnimatePresence initial={false} mode="wait">
              <motion.span
                key={streamingLabel}
                className="sr-chat-working-panel__semantic-stage"
                initial={reduceMotion ? false : { opacity: 0, y: 3 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0, y: -3 }}
                transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
              >
                {streamingLabel}
              </motion.span>
            </AnimatePresence>
          ) : (
            <Shimmer className="sr-chat-working-panel__shimmer" duration={1.35}>
              {streamingLabel}
            </Shimmer>
          )
        ) : (
          elapsedSeconds
            ? `Thought for ${elapsedSeconds}s`
            : "Done"
        )}
      </ReasoningTrigger>
      <ReasoningContent className="sr-chat-working-panel__content">
        {detailText ? (
          <p className="sr-chat-working-panel__reasoning">{detailText}</p>
        ) : null}
        {visibleToolChips.map((chip) => (
          <ToolRow key={chip.id} chip={chip} />
        ))}
      </ReasoningContent>
    </Reasoning>
  );
}
