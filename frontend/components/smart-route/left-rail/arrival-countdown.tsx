"use client";

import NumberFlow from "@number-flow/react";
import { useReducedMotion } from "motion/react";

type ArrivalCountdownValueProps = {
  minutes: number;
};

type ArrivalCountdownProps = {
  minutes: number[];
  fallback?: string;
  className?: string;
};

function normalizeMinute(value: number) {
  return Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
}

function ArrivalCountdownValue({ minutes }: ArrivalCountdownValueProps) {
  const shouldReduceMotion = useReducedMotion();
  const value = normalizeMinute(minutes);

  if (value <= 0) {
    return <span>Now</span>;
  }

  return (
    <NumberFlow
      value={value}
      trend={-1}
      animated={!shouldReduceMotion}
      respectMotionPreference
    />
  );
}

export function ArrivalCountdown({
  minutes,
  fallback = "Soon",
  className,
}: ArrivalCountdownProps) {
  const values = minutes.slice(0, 3).map(normalizeMinute);

  if (values.length === 0) {
    return <span className={className}>{fallback}</span>;
  }

  const hasMinuteLabel = values.some((value) => value > 0);

  return (
    <span className={className}>
      {values.map((value, index) => (
        <span key={index}>
          {index > 0 && <span>, </span>}
          <ArrivalCountdownValue minutes={value} />
        </span>
      ))}
      {hasMinuteLabel && <span> min</span>}
    </span>
  );
}

export function InlineArrivalCountdown({
  minutes,
  fallback = "soon",
}: {
  minutes: number | undefined;
  fallback?: string;
}) {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) {
    return <span>{fallback}</span>;
  }

  return (
    <span className="sr-arrival-countdown">
      <ArrivalCountdown minutes={[minutes]} fallback={fallback} />
    </span>
  );
}
