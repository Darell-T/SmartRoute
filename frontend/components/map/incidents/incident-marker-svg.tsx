"use client";

import { useId, type CSSProperties } from "react";
import type { IncidentMarkerSize, IncidentMarkerState, IncidentType } from "./incident-marker-types";
import { INCIDENT_MARKER_SIZES, INCIDENT_MARKER_TOKENS } from "./incident-marker-tokens";
import { lightenHexColor } from "./incident-marker-artwork";

function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

function IncidentMarkerGlyph({ type }: { type: IncidentType }) {
  switch (type) {
    case "shooting":
      return (
        <g fill="none" stroke="currentColor" strokeLinecap="round">
          <circle cx="0" cy="0" r="6" strokeWidth="2" />
          <line x1="-9" y1="0" x2="-3.5" y2="0" strokeWidth="2" />
          <line x1="9" y1="0" x2="3.5" y2="0" strokeWidth="2" />
          <line x1="0" y1="-9" x2="0" y2="-3.5" strokeWidth="2" />
          <line x1="0" y1="9" x2="0" y2="3.5" strokeWidth="2" />
          <circle cx="0" cy="0" r="2" fill="currentColor" stroke="none" />
        </g>
      );
    case "stabbing":
      return (
        <g transform="translate(0 1) rotate(20)">
          <path d="M 1.2 -10.5 L 1.2 -1 L -3.2 -1 Q -2.6 -5 -1.2 -7.6 Q 0 -9.6 1.2 -10.5 Z" fill="currentColor" />
          <rect x="-3.6" y="-1" width="6" height="2" rx="0.3" fill="currentColor" />
          <path d="M -2.8 1 L 1.6 1 L 1.6 8.6 Q 1.6 9.8 0.4 9.8 L -1.6 9.8 Q -2.8 9.8 -2.8 8.6 Z" fill="#1a1300" />
          <circle cx="-0.6" cy="3.6" r="0.7" fill="currentColor" />
          <circle cx="-0.6" cy="6.4" r="0.7" fill="currentColor" />
          <path d="M -2.4 -2 Q -1.6 -5.4 -0.2 -8.4" stroke="rgba(255,255,255,0.55)" strokeWidth="0.7" strokeLinecap="round" fill="none" />
        </g>
      );
    case "medical":
      return (
        <g fill="currentColor">
          <rect x="-3" y="-9" width="6" height="18" rx="1" />
          <rect x="-9" y="-3" width="18" height="6" rx="1" />
        </g>
      );
    case "fire":
      return (
        <g>
          <path d="M 0 -10 C 4 -4 7 -2 7 3 C 7 8 3 10 0 10 C -3 10 -7 8 -7 3 C -7 -1 -3 0 -2 -4 C -1 -7 -1 -8 0 -10 Z" fill="currentColor" />
          <path d="M 0 -2 C 2 1 3 2 3 5 C 3 7 1.5 8 0 8 C -1.5 8 -3 7 -3 5 C -3 3 -1 3 0 -2 Z" fill="rgba(255,255,255,0.45)" />
        </g>
      );
    case "police":
      return (
        <g>
          <path d="M 0 -10 L 8 -7 L 8 1 C 8 6 4 9 0 10 C -4 9 -8 6 -8 1 L -8 -7 Z" fill="currentColor" />
          <path d="M 0 -5 L 1.2 -1.6 L 4.8 -1.6 L 1.9 0.5 L 3 4 L 0 1.9 L -3 4 L -1.9 0.5 L -4.8 -1.6 L -1.2 -1.6 Z" fill="white" />
        </g>
      );
    case "disruptive":
      return (
        <g>
          <circle cx="0" cy="0" r="9.5" fill="currentColor" />
          <path d="M -6.5 -2.2 L -2.2 -2.2" stroke="#1a1300" strokeWidth="1.8" strokeLinecap="round" />
          <path d="M 2.2 -2.2 L 6.5 -2.2" stroke="#1a1300" strokeWidth="1.8" strokeLinecap="round" />
          <circle cx="-4.3" cy="-0.4" r="0.9" fill="#1a1300" />
          <circle cx="4.3" cy="-0.4" r="0.9" fill="#1a1300" />
          <path d="M -3.8 4.4 L 3.8 4.4" stroke="#1a1300" strokeWidth="1.8" strokeLinecap="round" />
        </g>
      );
    case "suspicious":
      return (
        <g>
          <rect x="-8" y="-5" width="16" height="13" rx="1.5" fill="currentColor" />
          <line x1="0" y1="-5" x2="0" y2="8" stroke="rgba(0,0,0,0.35)" strokeWidth="1.5" />
          <line x1="-8" y1="1.5" x2="8" y2="1.5" stroke="rgba(0,0,0,0.35)" strokeWidth="1.5" />
          <path d="M -2 -10 L 2 -10 L 4 -7 L -4 -7 Z" fill="currentColor" />
        </g>
      );
    case "general":
    default:
      return (
        <g>
          <path d="M 0 -10 L 9 7 L -9 7 Z" fill="currentColor" />
          <rect x="-1.2" y="-4" width="2.4" height="6" rx="1" fill="white" />
          <circle cx="0" cy="4.5" r="1.4" fill="white" />
        </g>
      );
  }
}

export function IncidentMarkerSvg({
  type,
  size = "M",
  state = "default",
  className,
  title,
  decorative = false,
  glow = true,
  radius = 0,
}: {
  type: IncidentType;
  size?: IncidentMarkerSize;
  state?: IncidentMarkerState;
  className?: string;
  title?: string;
  decorative?: boolean;
  glow?: boolean;
  radius?: number;
}) {
  const token = INCIDENT_MARKER_TOKENS[type];
  const markerSize = INCIDENT_MARKER_SIZES[size];
  const reactId = useId().replace(/:/g, "");
  const uid = `${type}-${size}-${state}-${reactId}`;
  const radiusSize = radius > 0
    ? Math.max(markerSize.width + 18, Math.min(markerSize.width + 64, Math.round(radius / 3)))
    : 0;
  const shellStyle = {
    "--sr-incident-marker-hue": token.color,
    "--sr-incident-marker-glow": token.glow,
  } as CSSProperties;
  const label = title ?? `${token.label} marker`;
  const topColor = lightenHexColor(token.color, 0.18);

  return (
    <span className={cx("sr-incident-marker-svg", className)} style={shellStyle}>
      {radiusSize > 0 ? (
        <svg
          aria-hidden="true"
          className="sr-incident-marker-svg__radius"
          width={radiusSize}
          height={radiusSize}
          viewBox={`0 0 ${radiusSize} ${radiusSize}`}
        >
          <circle
            cx={radiusSize / 2}
            cy={radiusSize / 2}
            r={radiusSize / 2 - 2}
            fill="none"
            stroke={token.color}
            strokeOpacity="0.22"
            strokeWidth="2"
          />
        </svg>
      ) : null}

      <svg
        role={decorative ? undefined : "img"}
        aria-hidden={decorative ? "true" : undefined}
        aria-label={decorative ? undefined : label}
        width={markerSize.width}
        height={markerSize.height}
        viewBox={"0 0 44 56"}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {!decorative ? <title>{label}</title> : null}
        <defs>
          <linearGradient id={`face-${uid}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={topColor} />
            <stop offset="1" stopColor={token.color} />
          </linearGradient>
          <radialGradient id={`pulse-${uid}`}>
            <stop offset="0" stopColor={token.color} stopOpacity="0.55" />
            <stop offset="1" stopColor={token.color} stopOpacity="0" />
          </radialGradient>
          <filter id={`glow-${uid}`} x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation={size === "L" ? 3.6 : size === "M" ? 2.4 : 1.5} />
          </filter>
        </defs>

        {glow ? (
          <ellipse
            cx="22"
            cy="55"
            rx={size === "L" ? 16 : size === "M" ? 11 : 8}
            ry={size === "L" ? 3.2 : size === "M" ? 2.2 : 1.6}
            fill={token.glow}
            filter={`url(#glow-${uid})`}
            opacity="0.7"
          />
        ) : null}

        {state === "pulse" ? (
          <circle cx="22" cy="22" r="24" fill={`url(#pulse-${uid})`} opacity="0.32" />
        ) : null}

        <path
          d="M22 2 C10.4 2 2 10.4 2 22 C2 36 22 54 22 54 S42 36 42 22 C42 10.4 33.6 2 22 2 Z"
          fill={`url(#face-${uid})`}
          stroke="#0a0e1a"
          strokeWidth="1.5"
        />
        <circle cx="22" cy="22" r="14" fill="#ffffff" fillOpacity="0.96" />
        <g transform="translate(22 22)" style={{ color: token.color }}>
          <IncidentMarkerGlyph type={type} />
        </g>

        {state === "selected" ? (
          <>
            <circle cx="22" cy="54" r={markerSize.anchor + 1} fill={token.color} opacity="0.5" />
            <circle cx="22" cy="54" r={markerSize.anchor} fill="#fff" />
          </>
        ) : null}
      </svg>
    </span>
  );
}

export type { IncidentMarkerSize, IncidentMarkerState, IncidentType };
