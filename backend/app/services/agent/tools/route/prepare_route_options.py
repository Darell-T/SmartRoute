"""Prepare server-owned route candidates for one outer conversational model."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.location_resolution import (
    ResolvedPlace,
    resolve_waypoint_places,
)
from app.services.agent.tools.route.preparation_adapter import (
    PreparedLeg,
    build_preparation_dependencies,
    new_preparation_timings,
    prepare_single_leg,
)
from app.services.agent.tools.route.prepare_route_branches import (
    is_current_location_discovery,
    limit_final_branch_chains,
    prepare_destination_branches,
    reasonable_branch_indexes,
    resolve_destination_options,
    stage_a_factors,
)
from app.services.agent.tools.route.prepare_route_persistence import (
    bind_canonical_destination_identities,
    nonfatal_prepare_result,
    persist_route_candidates,
)
from app.services.agent.tools.route.route_input import (
    merge_route_preparation_input,
    validated_waypoints,
)
from app.services.agent.turn.contract import GoalKind
from app.services.trips.preparation.combine import combine_prepared_chains
from app.services.trips.preparation.context import RoutePreparationFailure
from app.services.trips.preparation.evidence import (
    candidate_evidence_for_route,
    coverage_for_prepared,
)
from app.services.trips.preparation.finalize import finalize_aggregate
from app.services.trips.preparation.multi_stop import prepare_multi_stop
from app.services.trips.preparation.prepare import (
    AggregatePreparation,
    PreparedChain,
)

MAX_WAYPOINTS = 3
MAX_WAYPOINT_CHARS = 160


def as_aggregate(
    prepared: PreparedLeg | AggregatePreparation,
) -> AggregatePreparation:
    if isinstance(prepared, AggregatePreparation):
        return prepared
    coverage = coverage_for_prepared(prepared)
    return AggregatePreparation(
        parsed_routes=prepared.parsed_routes,
        scored=prepared.scored,
        aggregate_segments=[],
        origin_place=prepared.origin_place,
        destination_place=prepared.destination_place,
        relevant_alerts=prepared.relevant_alerts,
        event_impacts=prepared.event_impacts,
        event_failures=prepared.event_failures,
        event_evidence_status=prepared.event_evidence_status,
        incident_scan_metadata=prepared.incident_scan_metadata,
        evidence_envelopes=prepared.evidence_envelopes,
        crowd_search_metadata=prepared.crowd_search_metadata,
        collect_crowd_evidence=prepared.collect_crowd_evidence,
        incidents=prepared.incidents,
        coverage=coverage,
        timings=prepared.timings,
        candidate_evidence=[
            candidate_evidence_for_route(
                prepared,
                route_index=index,
                aggregate_index=index,
            )
            for index in range(len(prepared.parsed_routes))
        ],
        candidate_destinations=[
            prepared.destination_place for _ in prepared.parsed_routes
        ],
    )


PREPARE_ROUTE_OPTIONS_SCHEMA = {
    "name": "prepare_route_options",
    "description": (
        "Use when the rider asks for NYC directions, alternatives, arrive-by "
        "planning, a contextual replan, or a multi-stop trip. It prepares "
        "server-owned route candidates and disruption evidence; it is not a "
        "line-status or arrival lookup. Select one returned opaque candidate "
        "and call present_route exactly once."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "origin": {"type": ["string", "null"], "description": "Trip start or profile place."},
            "destination": {"type": ["string", "null"], "description": "NYC destination."},
            "destination_place_id": {
                "type": ["string", "null"],
                "description": (
                    "Opaque place id from the current discover_places set. "
                    "Required after structured discovery or web research in "
                    "this turn. The server resolves stored identity; a "
                    "free-text destination supplied alongside is ignored."
                ),
            },
            "destination_place_ids": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": (
                    "Opaque branch place ids from one current discover_places set. "
                    "Use multiple ids for a route-dependent delegated destination "
                    "choice when SmartRoute must compare actual route facts such as "
                    "least walking, fastest trip, fewer transfers, line or disruption "
                    "avoidance, accessibility, crowds, or practical trip burden—even "
                    "when the rider does not explicitly ask to compare. Each resulting "
                    "route candidate keeps its branch identity and canonical itinerary. "
                    "For route-independent place-only criteria, use one "
                    "destination_place_id instead."
                ),
            },
            "destination_source": {
                "type": "string",
                "enum": ["current_turn", "accepted_trip"],
                "description": (
                    "Use current_turn when this rider turn supplies or changes the "
                    "destination. Use accepted_trip only when deliberately continuing "
                    "the accepted trip endpoint. A current_turn destination must be "
                    "passed as destination or destination_place_id and never inherits "
                    "the previous trip endpoint."
                ),
            },
            "exclude_modes": {
                "type": ["array", "null"],
                "items": {"type": "string", "enum": ["BUS", "SUBWAY", "RAIL"]},
                "description": "Hard transit-mode exclusions.",
            },
            "allowed_modes": {
                "type": ["array", "null"],
                "items": {"type": "string", "enum": ["BUS", "SUBWAY", "RAIL"]},
                "description": "Previously excluded modes the rider explicitly allows again.",
            },
            "excluded_route_ids": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": "Hard route exclusions (e.g. Q, B35).",
            },
            "required_route_ids": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": (
                    "Hard route requirement when the rider explicitly says the "
                    "itinerary must use a route (e.g. Q). When reinstating an "
                    "excluded route, send it in both required_route_ids and "
                    "allowed_route_ids."
                ),
            },
            "allowed_route_ids": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": (
                    "Previously excluded routes the rider explicitly allows again. "
                    "This clears the old exclusion but does not by itself require "
                    "the route."
                ),
            },
            "preferred_modes": {
                "type": ["array", "null"],
                "items": {"type": "string", "enum": ["BUS", "SUBWAY", "RAIL"]},
                "description": "Preferred modes; server still enforces hard exclusions.",
            },
            "routing_preference": {
                "type": ["string", "null"],
                "enum": ["FEWER_TRANSFERS", "LESS_WALKING"],
                "description": "Routing optimization preference.",
            },
            "departure_time": {"type": ["string", "null"], "description": "RFC3339 departure."},
            "arrival_by": {"type": ["string", "null"], "description": "RFC3339 arrival target."},
            "waypoints": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": (
                    "Ordered intermediate stops, bounded server-side. Entries "
                    "may be direct NYC names/addresses or opaque place ids "
                    "from the current discover_places set."
                ),
            },
            "waypoint_dwell_minutes": {
                "type": ["number", "null"],
                "description": "Dwell at each intermediate stop; defaults to 25 minutes.",
            },
            "avoid_crowds": {"type": ["boolean", "null"], "description": "Avoid crowd evidence."},
            "avoid_stairs": {
                "type": ["boolean", "null"],
                "description": "Require stair-avoiding access.",
            },
            "accessibility_required": {
                "type": ["boolean", "null"],
                "description": "Require verified accessible transfers.",
            },
            "walking_tolerance_minutes": {
                "type": ["integer", "null"],
                "description": "Maximum street-walking minutes.",
            },
            "what_if": {
                "type": ["boolean", "null"],
                "description": "Prepare a temporary what-if scenario.",
            },
            "goal_key": {
                "type": "string",
                "description": "Turn goal associated with this route preparation.",
            },
            "activity_label": {
                "type": ["string", "null"],
                "description": (
                    "Optional short, context-aware phrase describing this work in progress. "
                    "Use null for simple actions. Do not state results, timing, or internals."
                ),
            },
        },
        "required": [
            "origin", "destination", "destination_place_id", "destination_place_ids",
            "destination_source", "exclude_modes", "allowed_modes", "excluded_route_ids",
            "required_route_ids", "allowed_route_ids", "preferred_modes", "routing_preference",
            "departure_time", "arrival_by", "waypoints", "waypoint_dwell_minutes",
            "avoid_crowds", "avoid_stairs", "accessibility_required", "walking_tolerance_minutes",
            "what_if", "goal_key", "activity_label",
        ],
        "additionalProperties": False,
    },
}


def _validate_goal_key(
    tool_input: dict, ctx: ToolContext
) -> tuple[str | None, ToolResult | None]:
    evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None, None
    raw_goal_key = tool_input.get("goal_key")
    if not isinstance(raw_goal_key, str) or not raw_goal_key.strip():
        return None, ToolResult(
            ok=False,
            error="goal_key is required when a turn contract is active",
            internal_diagnostic=True,
        )
    goal_key = raw_goal_key.strip()
    goal = contract.get_goal(goal_key)
    if goal is None:
        return None, ToolResult(
            ok=False,
            error="goal_key is unknown for this turn contract",
            internal_diagnostic=True,
        )
    if goal.kind != GoalKind.ROUTE:
        return None, ToolResult(
            ok=False,
            error="goal_key is incompatible with prepare_route_options",
            internal_diagnostic=True,
        )
    return goal_key, None


def _apply_destination_branch_input(
    merged: dict[str, Any],
    destination_options: list[tuple[ResolvedPlace, str | None]],
    waypoints: list[str],
) -> ToolResult | None:
    """Apply the comparison input contract before route preparation begins."""
    if len(destination_options) <= 1:
        return None
    if waypoints:
        return ToolResult(
            ok=False,
            error="branch route comparison cannot include waypoints",
        )
    merged["destination_place_ids"] = [
        place_id for _place, place_id in destination_options if place_id
    ]
    return None


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    _goal_key, goal_error = _validate_goal_key(tool_input or {}, ctx)
    if goal_error:
        return goal_error
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    if not session_id:
        return ToolResult(ok=False, error="session is required for route preparation")
    merged = merge_route_preparation_input(tool_input or {}, ctx)
    destination_state = await _resolve_destination_state(tool_input or {}, merged, ctx)
    if isinstance(destination_state, ToolResult):
        return destination_state
    (
        destination_options,
        resolved_destination,
        resolved_place_id,
        destination_used_set,
        accepted_destination_label,
    ) = destination_state
    waypoint_state = await _resolve_waypoint_state(tool_input or {}, merged, ctx)
    if isinstance(waypoint_state, ToolResult):
        return waypoint_state
    waypoint_places, waypoint_labels, waypoint_used_set, waypoints = waypoint_state
    comparison_error = _apply_destination_branch_input(
        merged,
        destination_options,
        waypoints,
    )
    if comparison_error:
        return comparison_error
    used_discovery_set_id = destination_used_set or waypoint_used_set
    started = time.monotonic()
    timings = new_preparation_timings()
    await ctx.emit_progress("finding_routes", "active")
    prepared, branch_chains, branch_coverage = await _prepare_route_request(
        merged,
        destination_options,
        resolved_destination,
        resolved_place_id,
        waypoint_places,
        waypoint_labels,
        waypoints,
        ctx,
        timings,
    )
    if isinstance(prepared, ToolResult):
        await ctx.emit_progress("finding_routes", "complete")
        return nonfatal_prepare_result(prepared, merged, ctx, started)

    snapshot_observed_at = datetime.now(UTC).isoformat()
    snapshot_id = "route-snapshot:{session}:{turn}:{millis}".format(
        session=session_id,
        turn=str(getattr(ctx, "turn_id", None) or "turn"),
        millis=int(time.time() * 1000),
    )
    aggregate = _finalize_prepared_aggregate(
        prepared,
        branch_coverage,
        destination_options,
        resolved_destination,
        resolved_place_id,
        merged,
        snapshot_id=snapshot_id,
        snapshot_observed_at=snapshot_observed_at,
    )
    aggregate = _finalize_branch_candidates(
        aggregate,
        branch_chains,
        branch_coverage,
        destination_options,
        merged,
        ctx,
        discovery_set_id=used_discovery_set_id,
        resolved_place_id=resolved_place_id,
        snapshot_id=snapshot_id,
        snapshot_observed_at=snapshot_observed_at,
    )
    return persist_route_candidates(
        aggregate,
        prepared,
        merged,
        ctx,
        session_id=session_id,
        started=started,
        timings=timings,
        destination_options=destination_options,
        accepted_destination_label=accepted_destination_label,
        used_discovery_set_id=used_discovery_set_id,
        destination_discovery_set_id=destination_used_set,
        waypoint_discovery_set_id=waypoint_used_set,
        resolved_place_id=resolved_place_id,
        waypoints=waypoints,
        snapshot_id=snapshot_id,
        snapshot_observed_at=snapshot_observed_at,
    )


def _finalize_prepared_aggregate(
    prepared: AggregatePreparation | PreparedLeg,
    branch_coverage: list[dict[str, str]],
    destination_options: list[tuple[ResolvedPlace, str | None]],
    resolved_destination: ResolvedPlace | None,
    resolved_place_id: str | None,
    merged: dict[str, Any],
    *,
    snapshot_id: str,
    snapshot_observed_at: str,
) -> AggregatePreparation:
    """Finalize one immutable aggregate and its branch-owned candidate facts."""

    aggregate = as_aggregate(prepared)
    bind_canonical_destination_identities(
        aggregate,
        destination_options,
        resolved_place_id,
    )
    if branch_coverage:
        aggregate.branch_coverage = branch_coverage
        if any(item.get("status") != "available" for item in branch_coverage):
            aggregate.coverage = {**aggregate.coverage, "branches": "partial"}
    if resolved_destination is not None:
        merged["destination"] = aggregate.destination_place.name
    return finalize_aggregate(
        aggregate,
        merged,
        snapshot_id=snapshot_id,
        snapshot_observed_at=snapshot_observed_at,
    )


async def _resolve_destination_state(
    tool_input: dict[str, Any],
    merged: dict[str, Any],
    ctx: ToolContext,
) -> (
    tuple[
        list[tuple[ResolvedPlace, str | None]],
        ResolvedPlace | None,
        str | None,
        str | None,
        str,
    ]
    | ToolResult
):
    accepted_label = str(merged.get("destination") or "").strip()
    has_opaque_reference = bool(
        str(tool_input.get("destination_place_id") or "").strip()
        or tool_input.get("destination_place_ids")
    )
    (
        destination_options,
        resolved_destination,
        resolved_place_id,
        reference_error,
        destination_used_set,
    ) = await resolve_destination_options(tool_input, merged, ctx)
    if reference_error:
        return ToolResult(ok=False, error=reference_error)
    if resolved_destination is not None:
        merged["destination"] = resolved_destination.name
    elif destination_options:
        merged["destination"] = destination_options[0][0].name
    canonical_name = str(merged.get("destination") or "").strip()
    if has_opaque_reference or not accepted_label:
        accepted_label = canonical_name
    if not canonical_name:
        return ToolResult(
            ok=False,
            error="destination is required (provide one or set trip destination first)",
        )
    return (
        destination_options,
        resolved_destination,
        resolved_place_id,
        destination_used_set,
        accepted_label,
    )


async def _resolve_waypoint_state(
    tool_input: dict[str, Any],
    merged: dict[str, Any],
    ctx: ToolContext,
) -> tuple[dict[str, Any], list[str], str | None, list[str]] | ToolResult:
    waypoints, waypoint_error = validated_waypoints(
        merged.get("waypoints"),
        max_waypoints=MAX_WAYPOINTS,
        max_waypoint_chars=MAX_WAYPOINT_CHARS,
    )
    if waypoint_error:
        return ToolResult(ok=False, error=waypoint_error)
    (
        waypoint_places,
        waypoint_labels,
        waypoint_error,
        waypoint_used_set,
    ) = await resolve_waypoint_places(waypoints, tool_input, ctx)
    if waypoint_error:
        return ToolResult(ok=False, error=waypoint_error)
    merged["waypoints"] = waypoint_labels
    return waypoint_places, waypoint_labels, waypoint_used_set, waypoints


async def _prepare_route_request(
    merged: dict[str, Any],
    destination_options: list[tuple[ResolvedPlace, str | None]],
    resolved_destination: ResolvedPlace | None,
    resolved_place_id: str | None,
    waypoint_places: dict[str, Any],
    waypoint_labels: list[str],
    waypoints: list[str],
    ctx: ToolContext,
    timings: dict[str, float],
) -> tuple[
    AggregatePreparation | PreparedLeg | ToolResult,
    list[PreparedChain],
    list[dict[str, str]],
]:
    branch_chains: list[PreparedChain] = []
    branch_coverage: list[dict[str, str]] = []
    if len(destination_options) > 1:
        prepared, branch_chains, branch_coverage = await prepare_destination_branches(
            destination_options,
            merged,
            ctx,
        )
    elif waypoints:
        prepared = await _prepare_multi_stop(
            merged,
            ctx,
            timings,
            waypoints,
            waypoint_places=waypoint_places,
            resolved_destination=resolved_destination,
            resolved_place_id=resolved_place_id,
            waypoint_labels=waypoint_labels,
        )
    else:
        prepared = await prepare_single_leg(
            merged,
            ctx,
            timings,
            dependencies=build_preparation_dependencies(),
            emit_comparing_progress=False,
            resolved_destination=resolved_destination,
        )
    return prepared, branch_chains, branch_coverage


def _finalize_branch_candidates(
    aggregate: AggregatePreparation,
    branch_chains: list[PreparedChain],
    branch_coverage: list[dict[str, str]],
    destination_options: list[tuple[ResolvedPlace, str | None]],
    merged: dict[str, Any],
    ctx: ToolContext,
    *,
    discovery_set_id: str | None,
    resolved_place_id: str | None,
    snapshot_id: str,
    snapshot_observed_at: str,
) -> AggregatePreparation:
    """Apply the final reasonable-pool and budget rules to branch candidates."""

    if not branch_chains:
        return aggregate
    narrow_pool = is_current_location_discovery(ctx, discovery_set_id)
    reasonable_indexes = (
        reasonable_branch_indexes(branch_chains, aggregate)
        if narrow_pool
        else list(range(len(branch_chains)))
    )
    selected_chains = limit_final_branch_chains(
        branch_chains,
        aggregate,
        merged,
        narrow_pool=narrow_pool,
    )
    if len(reasonable_indexes) < len(branch_chains):
        aggregate.stage_a_factors = stage_a_factors(
            aggregate,
            reasonable_indexes,
            merged,
        )
    selected_chain_ids = {id(chain) for chain in selected_chains}
    selected_branch_ids = {
        str(aggregate.candidate_destinations[index].place_id or "")
        for index, chain in enumerate(branch_chains)
        if id(chain) in selected_chain_ids
        and index < len(aggregate.candidate_destinations)
    }
    for branch in branch_coverage:
        if (
            branch.get("status") == "available"
            and branch.get("place_id") not in selected_branch_ids
        ):
            branch.update(status="excluded", coverage="reasonable_pool")
    if len(selected_chains) == len(branch_chains):
        return aggregate

    selected_aggregate = combine_prepared_chains(
        selected_chains,
        waypoints=[],
        destination_raw=merged["destination"],
        dwell_minutes=0,
        dwell_source="user",
    )
    selected_aggregate.coverage = dict(aggregate.coverage)
    selected_aggregate.branch_coverage = list(aggregate.branch_coverage)
    selected_aggregate.stage_a_factors = list(aggregate.stage_a_factors)
    bind_canonical_destination_identities(
        selected_aggregate,
        destination_options,
        resolved_place_id,
    )
    return finalize_aggregate(
        selected_aggregate,
        merged,
        snapshot_id=snapshot_id,
        snapshot_observed_at=snapshot_observed_at,
    )


async def _prepare_multi_stop(
    tool_input: dict,
    ctx: ToolContext,
    timings: dict[str, float],
    waypoints: list[str],
    *,
    waypoint_places: dict[str, Any] | None = None,
    resolved_destination: Any | None = None,
    resolved_place_id: str | None = None,
    waypoint_labels: list[str] | None = None,
) -> AggregatePreparation | ToolResult:
    place_overrides: dict[str, ResolvedPlace] = dict(waypoint_places or {})
    destination_raw = str(tool_input.get("destination") or "").strip()
    segment_tool_input = tool_input
    if resolved_destination is not None:
        # Key the destination override by its opaque id so a waypoint whose
        # stored name matches the destination name can never silently route
        # through the destination's coordinates.
        if resolved_place_id:
            place_overrides[resolved_place_id] = resolved_destination
            segment_tool_input = {**tool_input, "destination": resolved_place_id}
        else:
            place_overrides[resolved_destination.name] = resolved_destination

    async def prepare_segment(
        segment_input: dict,
        segment_ctx: ToolContext,
    ) -> PreparedLeg | ToolResult:
        resolved_origin = place_overrides.get(
            str(segment_input.get("origin") or "").strip()
        )
        resolved_dest = place_overrides.get(
            str(segment_input.get("destination") or "").strip()
        )
        return await _prepare_segment(
            segment_input,
            segment_ctx,
            resolved_origin=resolved_origin,
            resolved_destination=resolved_dest,
        )

    result = await prepare_multi_stop(
        segment_tool_input,
        ctx,
        timings,
        waypoints,
        prepare_segment=prepare_segment,
        waypoint_labels=waypoint_labels,
        destination_raw=destination_raw,
    )
    if isinstance(result, RoutePreparationFailure):
        return ToolResult(ok=False, error=result.error)
    return result


async def _prepare_segment(
    tool_input: dict,
    ctx: ToolContext,
    *,
    resolved_origin: Any | None = None,
    resolved_destination: Any | None = None,
) -> PreparedLeg | ToolResult:
    leg_timings = new_preparation_timings()
    prepared = await prepare_single_leg(
        tool_input,
        ctx,
        leg_timings,
        dependencies=build_preparation_dependencies(),
        emit_comparing_progress=False,
        resolved_origin=resolved_origin,
        resolved_destination=resolved_destination,
    )
    if isinstance(prepared, PreparedLeg):
        prepared.timings = leg_timings
    return prepared
