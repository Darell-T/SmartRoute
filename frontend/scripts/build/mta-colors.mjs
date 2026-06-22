import { createRequire } from "node:module";

// Build-script view of the single MTA color source (lib/mta-colors.json).
// Runtime code imports the JSON via lib/mta-colors.ts; the .mjs build scripts
// read the very same file here so there is exactly one place to edit a color.
const require = createRequire(import.meta.url);
const tokens = require("../../lib/mta-colors.json");

export const MTA_ROUTE_COLORS = tokens;

// Build-side default. Unknown ids fall back to neutral gray so a stray service
// id never crashes a build (the runtime uses its own gold fallback for trips).
export function routeColor(routeId, fallback = "#808183") {
  return MTA_ROUTE_COLORS[String(routeId || "").toUpperCase()] ?? fallback;
}

// Darken a #RRGGBB color toward black by `amount` (0..1), preserving hue.
// Used for the Apple-style stop-dot rim: a darker shade of the line's own
// color instead of a neutral near-black ring.
export function darkenHexColor(hex, amount = 0.45) {
  const match = /^#?([0-9a-fA-F]{6})$/.exec(String(hex || ""));
  if (!match) return hex;
  const value = parseInt(match[1], 16);
  const factor = 1 - Math.min(Math.max(amount, 0), 1);
  const r = Math.round(((value >> 16) & 0xff) * factor);
  const g = Math.round(((value >> 8) & 0xff) * factor);
  const b = Math.round((value & 0xff) * factor);
  const out = (r << 16) | (g << 8) | b;
  return `#${out.toString(16).padStart(6, "0").toUpperCase()}`;
}
