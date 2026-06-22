import type { IncidentType } from "./incident-marker-types";
import { INCIDENT_MARKER_TOKENS } from "./incident-marker-tokens";

const INCIDENT_MARKER_VIEWBOX = "0 0 44 56";
export const INCIDENT_MARKER_BASE_WIDTH = 44;
export const INCIDENT_MARKER_BASE_HEIGHT = 56;

export interface IncidentMarkerSvgOptions {
  size?: number;
  title?: string;
  uid?: string;
  withPulse?: boolean;
}

const INCIDENT_GLYPHS: Record<IncidentType, string> = {
  shooting: `
    <circle cx="0" cy="0" r="6" fill="none" stroke="currentColor" stroke-width="2"/>
    <line x1="-9" y1="0" x2="-3.5" y2="0" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    <line x1="9" y1="0" x2="3.5" y2="0" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    <line x1="0" y1="-9" x2="0" y2="-3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    <line x1="0" y1="9" x2="0" y2="3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    <circle cx="0" cy="0" r="2" fill="currentColor"/>`,
  stabbing: `
    <g transform="translate(0 1) rotate(20)">
      <path d="M 1.2 -10.5 L 1.2 -1 L -3.2 -1 Q -2.6 -5 -1.2 -7.6 Q 0 -9.6 1.2 -10.5 Z" fill="currentColor"/>
      <rect x="-3.6" y="-1" width="6" height="2" rx="0.3" fill="currentColor"/>
      <path d="M -2.8 1 L 1.6 1 L 1.6 8.6 Q 1.6 9.8 0.4 9.8 L -1.6 9.8 Q -2.8 9.8 -2.8 8.6 Z" fill="#1a1300"/>
      <circle cx="-0.6" cy="3.6" r="0.7" fill="currentColor"/>
      <circle cx="-0.6" cy="6.4" r="0.7" fill="currentColor"/>
      <path d="M -2.4 -2 Q -1.6 -5.4 -0.2 -8.4" stroke="rgba(255,255,255,0.55)" stroke-width="0.7" stroke-linecap="round" fill="none"/>
    </g>`,
  medical: `
    <rect x="-3" y="-9" width="6" height="18" rx="1" fill="currentColor"/>
    <rect x="-9" y="-3" width="18" height="6" rx="1" fill="currentColor"/>`,
  fire: `
    <path d="M 0 -10 C 4 -4 7 -2 7 3 C 7 8 3 10 0 10 C -3 10 -7 8 -7 3 C -7 -1 -3 0 -2 -4 C -1 -7 -1 -8 0 -10 Z" fill="currentColor"/>
    <path d="M 0 -2 C 2 1 3 2 3 5 C 3 7 1.5 8 0 8 C -1.5 8 -3 7 -3 5 C -3 3 -1 3 0 -2 Z" fill="rgba(255,255,255,0.45)"/>`,
  police: `
    <path d="M 0 -10 L 8 -7 L 8 1 C 8 6 4 9 0 10 C -4 9 -8 6 -8 1 L -8 -7 Z" fill="currentColor"/>
    <path d="M 0 -5 L 1.2 -1.6 L 4.8 -1.6 L 1.9 0.5 L 3 4 L 0 1.9 L -3 4 L -1.9 0.5 L -4.8 -1.6 L -1.2 -1.6 Z" fill="white"/>`,
  disruptive: `
    <circle cx="0" cy="0" r="9.5" fill="currentColor"/>
    <path d="M -6.5 -2.2 L -2.2 -2.2" stroke="#1a1300" stroke-width="1.8" stroke-linecap="round"/>
    <path d="M 2.2 -2.2 L 6.5 -2.2" stroke="#1a1300" stroke-width="1.8" stroke-linecap="round"/>
    <circle cx="-4.3" cy="-0.4" r="0.9" fill="#1a1300"/>
    <circle cx="4.3" cy="-0.4" r="0.9" fill="#1a1300"/>
    <path d="M -3.8 4.4 L 3.8 4.4" stroke="#1a1300" stroke-width="1.8" stroke-linecap="round"/>`,
  suspicious: `
    <rect x="-8" y="-5" width="16" height="13" rx="1.5" fill="currentColor"/>
    <line x1="0" y1="-5" x2="0" y2="8" stroke="rgba(0,0,0,0.35)" stroke-width="1.5"/>
    <line x1="-8" y1="1.5" x2="8" y2="1.5" stroke="rgba(0,0,0,0.35)" stroke-width="1.5"/>
    <path d="M -2 -10 L 2 -10 L 4 -7 L -4 -7 Z" fill="currentColor"/>`,
  general: `
    <path d="M 0 -10 L 9 7 L -9 7 Z" fill="currentColor"/>
    <rect x="-1.2" y="-4" width="2.4" height="6" rx="1" fill="white"/>
    <circle cx="0" cy="4.5" r="1.4" fill="white"/>`,
};

function escapeXml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function lightenHexColor(hex: string, amount: number) {
  const color = hex.replace("#", "");
  const red = parseInt(color.slice(0, 2), 16);
  const green = parseInt(color.slice(2, 4), 16);
  const blue = parseInt(color.slice(4, 6), 16);
  const lift = (channel: number) =>
    Math.min(255, Math.round(channel + (255 - channel) * amount))
      .toString(16)
      .padStart(2, "0");
  return `#${lift(red)}${lift(green)}${lift(blue)}`;
}

function stableSvgId(type: IncidentType, size: number, withPulse: boolean) {
  return `sr-incident-${type}-${size}-${withPulse ? "pulse" : "static"}`;
}

export function buildIncidentMarkerSvg(
  type: IncidentType,
  { size = INCIDENT_MARKER_BASE_WIDTH, title, uid, withPulse = false }: IncidentMarkerSvgOptions = {},
) {
  const token = INCIDENT_MARKER_TOKENS[type];
  const width = size;
  const height = Math.round((size * INCIDENT_MARKER_BASE_HEIGHT) / INCIDENT_MARKER_BASE_WIDTH);
  const gradientId = uid ?? stableSvgId(type, size, withPulse);
  const label = escapeXml(title ?? `${token.label} marker`);
  const topColor = lightenHexColor(token.color, 0.18);

  return `
<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${INCIDENT_MARKER_VIEWBOX}" role="img" aria-label="${label}">
  <defs>
    <linearGradient id="${gradientId}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${topColor}"/>
      <stop offset="1" stop-color="${token.color}"/>
    </linearGradient>
    ${withPulse ? `
    <radialGradient id="${gradientId}-pulse">
      <stop offset="0" stop-color="${token.color}" stop-opacity="0.55"/>
      <stop offset="1" stop-color="${token.color}" stop-opacity="0"/>
    </radialGradient>` : ""}
  </defs>
  ${withPulse ? `<circle cx="22" cy="22" r="24" fill="url(#${gradientId}-pulse)" opacity="0.32"/>` : ""}
  <path d="M22 2 C10.4 2 2 10.4 2 22 C2 36 22 54 22 54 S42 36 42 22 C42 10.4 33.6 2 22 2 Z"
        fill="url(#${gradientId})" stroke="#0a0e1a" stroke-width="1.5"/>
  <circle cx="22" cy="22" r="14" fill="#ffffff" fill-opacity="0.96"/>
  <g transform="translate(22 22)" style="color:${token.color}">${INCIDENT_GLYPHS[type]}</g>
</svg>`.trim();
}
