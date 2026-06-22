"use client";

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";

interface NumberTickerProps {
  value: number;
  suffix?: string;
  className?: string;
  duration?: number;
}

export function NumberTicker({
  value,
  suffix = "",
  className,
  duration = 520,
}: NumberTickerProps) {
  const reduceMotion = useReducedMotion();
  const [displayValue, setDisplayValue] = useState(value);
  const previousValueRef = useRef(value);

  useEffect(() => {
    if (reduceMotion) {
      setDisplayValue(value);
      previousValueRef.current = value;
      return;
    }

    const start = previousValueRef.current;
    const delta = value - start;
    if (delta === 0) return;

    let frame = 0;
    const startedAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min((now - startedAt) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplayValue(Math.round(start + delta * eased));
      if (progress < 1) {
        frame = requestAnimationFrame(tick);
      } else {
        previousValueRef.current = value;
      }
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [duration, reduceMotion, value]);

  return (
    <span className={className}>
      {displayValue}
      {suffix}
    </span>
  );
}
