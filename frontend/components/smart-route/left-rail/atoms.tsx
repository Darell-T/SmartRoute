"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail atoms

   The smallest pieces of vocabulary: meta labels (mono caps), pulsing dots,
   MTA line bullets, and the Apple-Maps-style stepper pictographs. All atoms
   are pure-presentation; no data fetching, no global state.
   ════════════════════════════════════════════════════════════════════════ */

import {
  FontAwesomeIcon,
  type FontAwesomeIconProps,
} from "@fortawesome/react-fontawesome";
import {
  faArrowRightArrowLeft,
  faBusSimple,
  faMapPin,
  faPersonWalking,
  faRightFromBracket,
  faTrain,
} from "@fortawesome/free-solid-svg-icons";
import type { CSSProperties, ReactNode } from "react";
import {
  RAIL_TONE_COLORS,
  type RailToneKey,
} from "./types";
import {
  SUBWAY_BULLET_ROUTES,
  TrainBullet,
} from "@/components/smart-route/train-bullet";

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

export function RouteBullet({ line, size = 24, title }: LineBulletProps) {
  return <LineBullet line={line} size={size} title={title} />;
}

export function RouteBulletGroup({
  lines,
  size = 24,
  limit,
}: {
  lines: string[];
  size?: number;
  limit?: number;
}) {
  const visible = limit ? lines.slice(0, limit) : lines;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        minWidth: 0,
      }}
    >
      {visible.map((line) => (
        <RouteBullet key={line} line={line} size={size} />
      ))}
      {limit && lines.length > limit && (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            minWidth: size,
            height: size,
            padding: "0 5px",
            borderRadius: 999,
            border: "1px solid var(--sr-rule)",
            color: "var(--sr-fg-3)",
            fontFamily: "var(--sr-mono)",
            fontSize: Math.max(9, size * 0.42),
            fontWeight: 700,
          }}
        >
          +{lines.length - limit}
        </span>
      )}
    </span>
  );
}

/* ── BusChip ─────────────────────────────────────────────────
   MTA buses use rectangular route badges (M34-SBS, B41…), not circular
   bullets. Muted blue-gray fill so route-true color stays reserved for
   subway bullets, while bus rows still read instantly distinct. */
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
        background: "#1d4ed8",
        color: "rgba(255, 255, 255, 0.92)",
        fontFamily: "var(--sr-display)",
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

/* ── TransitText ─────────────────────────────────────────────
   MTA alert copy references lines in brackets — "[N][W] trains are
   delayed", "No [7][7X] between…", "[B35] detour". Render those tokens as
   the real bullet or bus badge instead of raw bracket text. Unrecognized
   tokens pass through untouched. */
const TRANSIT_TOKEN = /\[([A-Za-z0-9+-]{1,8})\]/;
const TRANSIT_TEXT_TOKEN = new RegExp(TRANSIT_TOKEN.source, "gi");
const BUS_ROUTE_TOKEN = /^(?:B|BM|BX|M|Q|QM|SIM|S|X)\d{1,3}[A-Z]?(?:-?SBS)?$/;
const INLINE_ICON_PLACEHOLDER =
  /\[(?:free\s+)?(?:shuttle\s+bus|bus|subway|train|shuttle)\s+icon\]|\[(?:shuttle\s+bus|bus|subway|train|shuttle)\]/gi;

export function cleanTransitParagraphText(text: string): string {
  return text
    .replace(INLINE_ICON_PLACEHOLDER, " ")
    .replace(TRANSIT_TEXT_TOKEN, "$1")
    .replace(/\s+/g, " ")
    .replace(/\s+([.,;:!?])/g, "$1")
    .trim();
}

export function TransitText({
  text,
  bulletSize = 15,
  mode = "identity",
}: {
  text: string;
  bulletSize?: number;
  mode?: "identity" | "paragraph";
}) {
  if (mode === "paragraph") {
    return <>{cleanTransitParagraphText(text)}</>;
  }

  const nodes: ReactNode[] = [];
  const pattern = new RegExp(TRANSIT_TEXT_TOKEN.source, "gi");
  let cursor = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    const routeToken = match[1]?.toUpperCase();
    let badge: ReactNode | null = null;

    if (routeToken && SUBWAY_BULLET_ROUTES.has(routeToken)) {
      badge = (
        <span key={key++} className="sr-line-token">
          <RouteBullet line={routeToken} size={bulletSize} />
        </span>
      );
    } else if (routeToken && BUS_ROUTE_TOKEN.test(routeToken)) {
      badge = (
        <span key={key++} className="sr-line-token">
          <BusChip route={routeToken} />
        </span>
      );
    }

    if (!badge) continue;
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    nodes.push(badge);
    cursor = match.index + match[0].length;
  }

  if (cursor === 0) return <>{text}</>;
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return <>{nodes}</>;
}

/* ── StepIcon ────────────────────────────────────────────────
   Apple-Maps-style stepper pictographs. Route identity stays on MTA bullets,
   bus pills, and ride connectors; these small mode glyphs are neutral except
   for the red/pink start/arrive pins. */
type StepType =
  | "walk"
  | "board"
  | "ride"
  | "bus"
  | "transfer"
  | "exit"
  | "destination"
  | "arrive";

const STEP_ICON_COLORS = {
  primary: "var(--sr-fg)",
  secondary: "var(--sr-fg)",
  neutral: "var(--sr-fg-2)",
  marker: "#ef3b5d",
} as const;

export function StepIcon({
  type,
  color,
  size: sizeOverride,
}: {
  type: StepType;
  color?: string;
  size?: number;
}) {
  const size =
    sizeOverride ?? (type === "destination" || type === "arrive" ? 16 : 15);
  const iconStyle = (iconColor: string): FontAwesomeIconProps["style"] => ({
    display: "block",
    width: size,
    height: size,
    color: iconColor,
    flexShrink: 0,
  });

  switch (type) {
    case "walk":
      return (
        <FontAwesomeIcon
          icon={faPersonWalking}
          data-step-icon="walk"
          style={iconStyle(color ?? STEP_ICON_COLORS.primary)}
          aria-hidden="true"
        />
      );
    case "board":
    case "ride":
      return (
        <FontAwesomeIcon
          icon={faTrain}
          data-step-icon="train"
          style={iconStyle(color ?? STEP_ICON_COLORS.secondary)}
          aria-hidden="true"
        />
      );
    case "bus":
      return (
        <FontAwesomeIcon
          icon={faBusSimple}
          data-step-icon="bus"
          style={iconStyle(color ?? STEP_ICON_COLORS.secondary)}
          aria-hidden="true"
        />
      );
    case "transfer":
      return (
        <FontAwesomeIcon
          icon={faArrowRightArrowLeft}
          data-step-icon="transfer"
          style={iconStyle(color ?? STEP_ICON_COLORS.neutral)}
          aria-hidden="true"
        />
      );
    case "exit":
      return (
        <FontAwesomeIcon
          icon={faRightFromBracket}
          data-step-icon="exit"
          style={iconStyle(color ?? STEP_ICON_COLORS.neutral)}
          aria-hidden="true"
        />
      );
    case "destination":
    case "arrive":
      return (
        <FontAwesomeIcon
          icon={faMapPin}
          data-step-icon={type}
          style={iconStyle(color ?? STEP_ICON_COLORS.marker)}
          aria-hidden="true"
        />
      );
    default:
      return null;
  }
}

/* ── LocationPin ─────────────────────────────────────────────
   Custom teardrop endpoint marker for the details chain: filled body with a
   punched-out center dot, sized to sit alongside route bullets. Cyan for the
   trip start (current location), coral for the arrival. Deliberately more
   substantial than a stock pin, but never large enough to dominate. */
export function LocationPin({
  tone = "arrive",
  size = 20,
}: {
  tone?: "start" | "arrive";
  size?: number;
}) {
  const color = tone === "start" ? "#5aa2ff" : "#fb5a7d";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      style={{ display: "block", flexShrink: 0 }}
    >
      <path
        d="M12 2.4c-3.93 0-7.1 3.06-7.1 6.85 0 4.7 5.75 11.05 6.5 11.85a.82.82 0 0 0 1.2 0c.75-.8 6.5-7.15 6.5-11.85 0-3.79-3.17-6.85-7.1-6.85Z"
        fill={color}
      />
      <circle cx="12" cy="9.1" r="2.5" fill="#0d1117" />
    </svg>
  );
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
