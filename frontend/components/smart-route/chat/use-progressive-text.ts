"use client";

import { useEffect, useState } from "react";

type ProgressiveTextResult = {
  displayedText: string;
  isCaughtUp: boolean;
};

export type ProgressiveRevealState = {
  visibleLength: number;
  targetLength: number;
};

/** Keep short answers natural while allowing large, bursty SSE payloads to
 * catch up quickly enough that the interface never feels artificially slow. */
export function nextRevealLength(current: number, target: number): number {
  const remaining = Math.max(0, target - current);
  let step = 1;
  if (remaining > 280) step = 10;
  else if (remaining > 120) step = 5;
  else if (remaining > 48) step = 3;
  return Math.min(target, current + step);
}

export function clampRevealLength(visibleLength: number, textLength: number): number {
  return Math.min(visibleLength, textLength);
}

export function preserveRevealLength(
  visibleLength: number,
  priorTextLength: number,
  textLength: number,
): number {
  return Math.min(visibleLength, priorTextLength, textLength);
}

export function reconcileProgressiveReveal(
  state: ProgressiveRevealState,
  nextTextLength: number,
): ProgressiveRevealState {
  return nextTextLength < state.targetLength
    ? { visibleLength: Math.min(state.visibleLength, nextTextLength), targetLength: nextTextLength }
    : state;
}

/**
 * Reveals text that arrives after mount, but never replays an already-finished
 * conversation when the user returns to the chat tab. This is intentionally a
 * small presentation layer over the real SSE response, not a fake response
 * generator.
 */
export function useProgressiveText(
  text: string,
  reduceMotion = false,
): ProgressiveTextResult {
  const [shouldAnimate] = useState(() => text.length === 0);
  const [previousText, setPreviousText] = useState(text);
  const [revealState, setRevealState] = useState<ProgressiveRevealState>(() => ({
    visibleLength: text.length,
    targetLength: text.length,
  }));
  if (previousText !== text) {
    setPreviousText(text);
    setRevealState((state) => reconcileProgressiveReveal(state, text.length));
  }
  const shouldReveal = shouldAnimate && !reduceMotion;
  const displayedLength = shouldReveal
    ? preserveRevealLength(revealState.visibleLength, revealState.targetLength, text.length)
    : text.length;

  useEffect(() => {
    if (!shouldReveal) return;
    if (displayedLength >= text.length && revealState.targetLength === text.length) return;

    const timer = window.setTimeout(() => {
      setRevealState(() => ({
        visibleLength: nextRevealLength(displayedLength, text.length),
        targetLength: text.length,
      }));
    }, 18);

    return () => window.clearTimeout(timer);
  }, [displayedLength, revealState.targetLength, shouldReveal, text.length]);

  return {
    displayedText: text.slice(0, displayedLength),
    isCaughtUp: displayedLength >= text.length,
  };
}
