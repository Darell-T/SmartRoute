"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail atoms

   The smallest pieces of vocabulary: meta labels (mono caps), pulsing dots,
   MTA line bullets, and the Apple-Maps-style stepper pictographs. All atoms
   are pure-presentation; no data fetching, no global state.
   ════════════════════════════════════════════════════════════════════════ */

import type { CSSProperties, ReactNode } from "react";
import {
  RAIL_TONE_COLORS,
  type RailToneKey,
} from "./types";
import { TrainBullet } from "@/components/smart-route/train-bullet";

/* ── Meta ─────────────────────────────────────────────────────
   All-caps mono label used throughout the rail (section heads, status
   pills, timestamps, "WHERE TO" prompt). Tone defaults to muted; pass
   `tone="ink"` for the strongest contrast, `tone="cyan"` for active. */
interface MetaProps {
  children: ReactNode;
  tone?: RailToneKey;
  style?: CSSProperties;
  suppressHydrationWarning?: boolean;
}

export function Meta({
  children,
  tone = "muted",
  style,
  suppressHydrationWarning,
}: MetaProps) {
  return (
    <span
      suppressHydrationWarning={suppressHydrationWarning}
      style={{
        fontFamily: "var(--sr-mono)",
        fontSize: 10.5,
        letterSpacing: "0.16em",
        textTransform: "uppercase",
        color: RAIL_TONE_COLORS[tone],
        ...style,
      }}
    >
      {children}
    </span>
  );
}

/* ── Dot ─────────────────────────────────────────────────────
   Pulsing status indicator. Used for live signal, severity flags, narration
   waveform anchors. `pulse` enables the srPulse ripple keyframe. */
interface DotProps {
  color?: string;
  size?: number;
  style?: CSSProperties;
  pulse?: boolean;
}

export function Dot({
  color = "var(--sr-muted)",
  size = 6,
  style,
  pulse = false,
}: DotProps) {
  return (
    <span
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        background: color,
        color, // currentColor for srPulse box-shadow
        boxShadow: pulse ? `0 0 0 0 ${color}` : "none",
        animation: pulse ? "srPulse 1.8s ease-out infinite" : "none",
        flexShrink: 0,
        ...style,
      }}
    />
  );
}

/* ── LineBullet ──────────────────────────────────────────────
   Wrapper around the project's existing `TrainBullet` that loads the
   authentic MTA SVG from `/mta-bullets/{line}.svg`. The SVG already bakes
   in the official color + letterform, so no further fill overrides needed.
   The `dark` set in `types.ts` (N/Q/R/W/L) is informational — the SVG
   file itself uses dark ink against the bright yellow/gray backgrounds. */
interface LineBulletProps {
  line: string;
  size?: number;
  title?: string;
}

export function LineBullet({ line, size = 22, title }: LineBulletProps) {
  return <TrainBullet line={line} size={size} title={title} />;
}

/* ── BusChip ─────────────────────────────────────────────────
   MTA buses use rectangular route badges (M34-SBS, B41…), not circular
   bullets. Rendered in MTA bus blue with the rail's mono face so bus rows
   read instantly distinct from subway rows in the arrivals board. */
export function BusChip({ route, title }: { route: string; title?: string }) {
  const label = title || `${route} bus`;
  return (
    <span
      title={label}
      aria-label={label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 28,
        height: 18,
        padding: "0 5px",
        borderRadius: 3,
        background: "#0039A6",
        color: "#ffffff",
        fontFamily: "var(--sr-mono)",
        fontSize: route.length > 4 ? 8.5 : 10,
        fontWeight: 700,
        letterSpacing: "0.02em",
        whiteSpace: "nowrap",
        flexShrink: 0,
      }}
    >
      {route.toUpperCase()}
    </span>
  );
}

/* ── StepIcon ────────────────────────────────────────────────
   Apple-Maps-style stepper pictographs. The shapes are the prototype's
   originals — silhouetted walker, subway car with twin windows + door +
   track marks, exit doorway with arrow, map pin. The icon color is driven
   by the parent so the active step (cyan node) reads as filled white-ink
   while mid-route steps invert to cyan-on-surface. */
type StepType = "walk" | "board" | "ride" | "exit" | "destination" | "arrive";

export function StepIcon({
  type,
  color = "currentColor",
}: {
  type: StepType;
  color?: string;
}) {
  switch (type) {
    case "walk":
      return (
        <svg
          viewBox="0 0 20 20"
          width="14"
          height="14"
          style={{ display: "block" }}
          aria-hidden="true"
        >
          <circle cx="11.5" cy="3.2" r="1.8" fill={color} />
          <path
            d="M11.5 5.5 L9 11 M9 11 L7 16 M9 11 L11.5 14.5 L11 18 M11.5 6.5 L13.5 10 M11.5 6.5 L9 8.5"
            stroke={color}
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </svg>
      );
    case "board":
    case "ride":
      return (
        <svg
          viewBox="0 0 20 20"
          width="14"
          height="14"
          style={{ display: "block" }}
          aria-hidden="true"
        >
          {/* subway car silhouette */}
          <path
            d="M5 4 Q5 2.5 6.5 2.5 L13.5 2.5 Q15 2.5 15 4 L15 14.5 Q15 15.5 14 15.5 L6 15.5 Q5 15.5 5 14.5 Z"
            fill={color}
          />
          {/* twin windows */}
          <rect x="6.5" y="4.5" width="2.5" height="2" fill="var(--sr-surface)" />
          <rect x="11" y="4.5" width="2.5" height="2" fill="var(--sr-surface)" />
          {/* door */}
          <line
            x1="10"
            y1="7.5"
            x2="10"
            y2="13"
            stroke="var(--sr-surface)"
            strokeWidth="0.8"
          />
          {/* headlights */}
          <circle cx="7.2" cy="13.5" r="0.7" fill="var(--sr-surface)" />
          <circle cx="12.8" cy="13.5" r="0.7" fill="var(--sr-surface)" />
          {/* track marks */}
          <line
            x1="4"
            y1="17.5"
            x2="7.5"
            y2="17.5"
            stroke={color}
            strokeWidth="1"
            strokeLinecap="round"
          />
          <line
            x1="12.5"
            y1="17.5"
            x2="16"
            y2="17.5"
            stroke={color}
            strokeWidth="1"
            strokeLinecap="round"
          />
        </svg>
      );
    case "exit":
      return (
        <svg
          viewBox="0 0 20 20"
          width="14"
          height="14"
          style={{ display: "block" }}
          aria-hidden="true"
        >
          {/* doorway */}
          <path
            d="M11 4 L16 4 L16 16 L11 16"
            fill="none"
            stroke={color}
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* arrow exiting */}
          <path
            d="M3 10 L12 10"
            fill="none"
            stroke={color}
            strokeWidth="1.6"
            strokeLinecap="round"
          />
          <path
            d="M9 7 L12 10 L9 13"
            fill="none"
            stroke={color}
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "destination":
      return (
        <svg
          viewBox="0 0 20 20"
          width="14"
          height="14"
          style={{ display: "block" }}
          aria-hidden="true"
        >
          <path
            d="M10 2 C6.7 2 4 4.6 4 7.8 C4 12 10 17.5 10 17.5 S16 12 16 7.8 C16 4.6 13.3 2 10 2 Z"
            fill={color}
          />
          <circle cx="10" cy="7.8" r="1.8" fill="var(--sr-surface)" />
        </svg>
      );
    case "arrive":
      // Checkered finish flag -- the three filled cells form a 3x2 checker
      // against the (empty) node background, so it reads correctly on any
      // node color. A little celebratory flourish for the destination.
      return (
        <svg
          viewBox="0 0 20 20"
          width="14"
          height="14"
          style={{ display: "block" }}
          aria-hidden="true"
        >
          <line
            x1="5"
            y1="2.5"
            x2="5"
            y2="17.5"
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
          />
          <path d="M5.8 3 H15 V9 H5.8 Z" fill="none" stroke={color} strokeWidth="1" />
          <rect x="5.8" y="3" width="3.07" height="3" fill={color} />
          <rect x="11.94" y="3" width="3.06" height="3" fill={color} />
          <rect x="8.87" y="6" width="3.07" height="3" fill={color} />
        </svg>
      );
    default:
      return null;
  }
}

/* ── Button primitives ───────────────────────────────────────
   Two visual styles — primary cyan filled and ghost outlined. Both use
   mono caps with the rail's standard 0.16em tracking. */

export function btnPrimary(): CSSProperties {
  return {
    background: "var(--sr-cyan)",
    color: "#15120A",
    border: 0,
    padding: "7px 12px",
    cursor: "pointer",
    fontFamily: "var(--sr-mono)",
    fontSize: 10,
    letterSpacing: "0.16em",
    textTransform: "uppercase",
    fontWeight: 600,
    display: "inline-flex",
    alignItems: "center",
  };
}

export function btnGhost(): CSSProperties {
  return {
    background: "transparent",
    color: "var(--sr-fg)",
    border: "1px solid var(--sr-rule-bright)",
    padding: "7px 12px",
    cursor: "pointer",
    fontFamily: "var(--sr-mono)",
    fontSize: 10,
    letterSpacing: "0.16em",
    textTransform: "uppercase",
  };
}
