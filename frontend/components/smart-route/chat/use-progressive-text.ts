"use client";

import { useEffect, useRef, useState } from "react";

type ProgressiveTextResult = {
  displayedText: string;
  isCaughtUp: boolean;
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
  const shouldAnimateRef = useRef(text.length === 0);
  const [visibleLength, setVisibleLength] = useState(text.length);

  useEffect(() => {
    if (visibleLength > text.length) {
      setVisibleLength(text.length);
      return;
    }

    if (!shouldAnimateRef.current || reduceMotion) {
      if (visibleLength !== text.length) setVisibleLength(text.length);
      return;
    }

    if (visibleLength >= text.length) return;

    const timer = window.setTimeout(() => {
      setVisibleLength((current) => nextRevealLength(current, text.length));
    }, 18);

    return () => window.clearTimeout(timer);
  }, [reduceMotion, text.length, visibleLength]);

  const clampedLength = Math.min(visibleLength, text.length);
  return {
    displayedText: text.slice(0, clampedLength),
    isCaughtUp: clampedLength >= text.length,
  };
}
