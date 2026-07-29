"use client";

/* Vendored from Vercel AI Elements ("shimmer"), source:
   https://github.com/vercel/ai-elements/blob/main/packages/elements/src/shimmer.tsx
   License: Apache-2.0 (https://github.com/vercel/ai-elements/blob/main/LICENSE)

   Local tweaks: gradient stops map to the rail's Platform White tiers, the
   sweep is disabled entirely under prefers-reduced-motion instead of merely
   slowed, and the polymorphic `as` prop is narrowed to a fixed tag set so
   motion components are created once at module scope, never during render.
   Reused as-is for the chat tab's "Thinking…" reasoning trigger label. */

import type { CSSProperties, ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";

const MOTION_TAGS = {
  span: motion.span,
  div: motion.div,
  p: motion.p,
} as const;

export type ShimmerProps = {
  as?: keyof typeof MOTION_TAGS;
  duration?: number;
  className?: string;
  children: ReactNode;
};

const SHIMMER_TEXT_STYLE: CSSProperties = {
  display: "inline-block",
  backgroundImage:
    "var(--shimmer-gradient, linear-gradient(90deg, rgba(255,255,255,.92) 0%, rgba(255,255,255,.40) 50%, rgba(255,255,255,.92) 100%))",
  backgroundSize: "200% 100%",
  WebkitBackgroundClip: "text",
  backgroundClip: "text",
  color: "transparent",
};

export function Shimmer({
  as = "span",
  duration = 2,
  className,
  children,
}: ShimmerProps) {
  const reduceMotion = useReducedMotion();
  const MotionComponent = MOTION_TAGS[as];
  const StaticComponent = as;

  if (reduceMotion) {
    return <StaticComponent className={className}>{children}</StaticComponent>;
  }

  return (
    <MotionComponent
      className={className}
      style={SHIMMER_TEXT_STYLE}
      animate={{ backgroundPosition: ["150% center", "-50% center"] }}
      transition={{ duration, ease: "linear", repeat: Infinity }}
    >
      {children}
    </MotionComponent>
  );
}
