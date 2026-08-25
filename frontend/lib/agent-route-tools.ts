/**
 * Classification of the conversational agent's route workflow tools.
 *
 * The backend canonical route workflow emits `prepare_route_options` and
 * then `present_route`. Legacy `plan_trip` remains supported as rollback and
 * display compatibility, so the working panel, the ThinkingOrb state, and
 * the reducer's progress clearing share one typed source of truth instead of
 * drifting on raw tool-name literals.
 */

const ROUTE_PREPARATION_TOOLS: ReadonlySet<string> = new Set([
  "prepare_route_options",
  "plan_trip",
]);

const ROUTE_RESULT_TOOLS: ReadonlySet<string> = new Set([
  "present_route",
  "plan_trip",
]);

/** Capabilities whose running state means authoritative information is being
 * retrieved or compared. Keep this tied to real `tool_start` events: model
 * deliberation alone must never activate the searching UI. */
const SEARCH_ACTIVITY_TOOLS: ReadonlySet<string> = new Set([
  "prepare_route_options",
  "plan_trip",
  "discover_places",
  "search_local_places",
  "get_place_details",
  "check_transit",
  "transit_snapshot",
  "check_area_conditions",
  "lookup_arrivals",
  "lookup_facts",
  "accessibility_status",
  "event_lookup",
  "venue_crowd_window",
  "web_search",
]);

/** Runtime/audit steps that remain part of the event stream but do not
 * represent rider-useful work in progress. */
const HIDDEN_ACTIVITY_TOOLS: ReadonlySet<string> = new Set([
  "declare_goals",
  "present_places",
  "present_transit",
  "present_route",
  "complete_turn",
]);

/** True when a running `tool` should drive the route-search searching UI. */
export function isRoutePreparationTool(tool: string): boolean {
  return ROUTE_PREPARATION_TOOLS.has(tool);
}

/** True when a successful `tool` completion is a presented route result. */
export function isRouteResultTool(tool: string): boolean {
  return ROUTE_RESULT_TOOLS.has(tool);
}

/** True for any route workflow tool whose failure must clear active
 *  semantic progress so a retry cannot inherit stale route work. */
export function isRouteWorkflowTool(tool: string): boolean {
  return isRoutePreparationTool(tool) || isRouteResultTool(tool);
}

/** True only after a real retrieval/comparison capability has started. */
export function isSearchActivityTool(tool: string): boolean {
  return SEARCH_ACTIVITY_TOOLS.has(tool);
}

/** True when a tool event should stay out of the rider-facing activity list. */
export function isHiddenActivityTool(tool: string): boolean {
  return HIDDEN_ACTIVITY_TOOLS.has(tool);
}
