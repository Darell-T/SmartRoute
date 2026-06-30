import { BUNDLE_COLOR_ORDER } from "../../lane-order.ts";
import { MTA_ROUTE_COLORS } from "../../mta-colors.ts";

// Route ID normalization. The MTA publishes some service variants as
// distinct route_ids (e.g., "6X" for express 6, "FX" for F express); these
// need to share the user-facing color but stay separate in topology since
// they have different stop sequences and shapes.
export function normalizeRouteId(value: string) {
  const r = String(value || "").trim().toUpperCase();
  if (r === "6D") return "6X";
  if (r === "7D") return "7X";
  if (r === "FD") return "FX";
  // NOTE: deliberately do NOT collapse FS / GS / H into "S". They are
  // three physically disconnected shuttle services (42 St / Franklin Av /
  // Rockaway Park). Merging them into one route_id breaks connectivity
  // validation by construction. The runtime color map still treats
  // them all as gray.
  if (r === "SIR") return "SI";
  return r;
}

// Single source of truth lives in lib/mta-colors.json.
const ROUTE_COLORS = MTA_ROUTE_COLORS;

const COLOR_VISUAL_ORDER = [
  "#808183",
  "#A7A9AC",
  "#996633",
  "#6CBE45",
  "#FCCC0A",
  "#00933C",
  "#EE352E",
  "#0A84FF",
  "#FF6319",
  "#B933AD",
  "#0078C6",
];

// Hand-curated per-bundle color order overrides. Keyed by overrideKey
// ("<from_anchor_id>::<to_anchor_id>"). Empty in Phase 2; Phase 6 may populate
// after visual QA flags specific junctions where the heuristic produces
// visible crossings.
//
// Note: BUNDLE_COLOR_ORDER is imported from ../../lane-order.ts (single
// source of truth). The local copy was removed to prevent the rank table
// from drifting from the canonical order used by orderColorsForBundle.
export const BUNDLE_ORDER_OVERRIDES = {};

export function routeColorFor(routeId: string) {
  return ROUTE_COLORS[routeId] ?? "#808183";
}

export function colorRank(color: string) {
  const index = COLOR_VISUAL_ORDER.indexOf(color);
  return index === -1 ? 999 : index;
}

export function bundleColorRank(color: string) {
  const index = BUNDLE_COLOR_ORDER.indexOf(color);
  return index === -1 ? 999 : index;
}

export function compareRouteIds(a: string, b: string) {
  return a.localeCompare(b, "en", { numeric: true });
}
