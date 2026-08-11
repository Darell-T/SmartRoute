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
