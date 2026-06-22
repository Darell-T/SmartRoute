import tokens from "./mta-colors.json";

// Single source of truth for MTA route -> line color (NYCT lettered/numbered
// services + the Staten Island Railway). Shared by the runtime renderer, the
// incident popups, and the build scripts (via scripts/build/mta-colors.mjs,
// which reads the same JSON).
//
// Key conventions match the build pipeline's normalizeRouteId():
//   - express variants are kept distinct (6X, 7X, FX)
//   - the three physically disconnected shuttles stay gray (S, FS, GS, H)
//   - the Staten Island Railway is keyed as "SI" (SIR normalizes to SI)
export const MTA_ROUTE_COLORS: Record<string, string> = tokens;

// Historical getLineColor() fallback for an unknown service id.
const UNKNOWN_ROUTE_COLOR = "#FFD700";

export function getRouteColor(line: string): string {
  return MTA_ROUTE_COLORS[String(line).toUpperCase()] ?? UNKNOWN_ROUTE_COLOR;
}
