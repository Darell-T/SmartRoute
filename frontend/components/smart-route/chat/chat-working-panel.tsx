"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — working panel

   The stock AI Elements Reasoning presentation (components/ai-elements/
   reasoning.tsx — vendored, Streamdown stripped), unthemed beyond the six
   chat tokens: "Thinking…" with a shimmer while the turn streams, auto-open,
   auto-collapse to "Worked for Ns" one second after the turn ends. Inside:
   quiet tool rows (lucide status glyph + server-provided label + duration),
   13px, lowercase, no color — the plan is explicit these are not a second
   surface competing with route cards for attention.

   Superseded `tool-chip.tsx` (deleted) — tool rows now live inside this
   collapsible instead of as a standalone chip row above the prose.
   ════════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import type { ToolChip as ToolChipData } from "@/lib/use-agent-chat";

function ToolRow({ chip }: { chip: ToolChipData }) {
  return (
    <div className="sr-chat-tool-row" data-status={chip.status}>
      {chip.status === "running" ? (
        <Loader2 size={13} strokeWidth={2} className="sr-chat-tool-row__spinner" aria-hidden="true" />
      ) : chip.status === "ok" ? (
        <Check size={13} strokeWidth={2} aria-hidden="true" />
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
  isStreaming,
}: {
  toolChips: ToolChipData[];
  isStreaming: boolean;
}) {
  const everStreamed = useEverStreamed(isStreaming);
  const elapsedSeconds = useElapsedSeconds(isStreaming);
  const hasStarted = everStreamed || toolChips.length > 0;
  if (!hasStarted) return null;

  return (
    <Reasoning className="sr-chat-working-panel" isStreaming={isStreaming} duration={elapsedSeconds}>
      <ReasoningTrigger className="sr-chat-working-panel__trigger">
        {isStreaming ? (
          <Shimmer>Thinking…</Shimmer>
        ) : (
          `Worked for ${elapsedSeconds ?? 1} second${elapsedSeconds === 1 ? "" : "s"}`
        )}
      </ReasoningTrigger>
      <ReasoningContent className="sr-chat-working-panel__content">
        {toolChips.map((chip) => (
          <ToolRow key={chip.id} chip={chip} />
        ))}
      </ReasoningContent>
    </Reasoning>
  );
}
