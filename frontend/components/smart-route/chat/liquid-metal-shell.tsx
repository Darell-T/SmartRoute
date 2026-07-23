"use client";

/* ════════════════════════════════════════════════════════════════════════
   Liquid-metal perimeter shell

   Premium graphite/chrome frame for recommended cards. Inspired by the
   material behavior of metal.jakubantalik.com, implemented as a lightweight
   CSS conic-gradient rim (no WebGL). The frame lives outside the content
   surface — the interior stays flat and readable.

   Technique: padding-box gradient border. Parent paints the rotating metal
   conic gradient; the inner surface covers the center with a matte fill.

   Reduced motion / static: freezes the rim as a still metallic border.
   ════════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { useReducedMotion } from "motion/react";

export function LiquidMetalShell({
  children,
  className,
  active = true,
}: {
  children: ReactNode;
  className?: string;
  /** When false, renders children without the metallic frame. */
  active?: boolean;
}) {
  const reduceMotion = useReducedMotion() ?? false;

  if (!active) {
    return <>{children}</>;
  }

  return (
    <div
      className={["sr-liquid-metal", className].filter(Boolean).join(" ")}
      data-reduced-motion={reduceMotion ? "true" : "false"}
    >
      {/* Optional specular sheen layer for richer metal motion. */}
      <span className="sr-liquid-metal__sheen" aria-hidden="true" />
      <div className="sr-liquid-metal__surface">{children}</div>
    </div>
  );
}
