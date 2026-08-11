"""Prepare server-owned route candidates for one outer conversational model."""

from __future__ import annotations

import time
from typing import Any

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools._location import (
    ResolvedPlace,
    resolve_destination_reference,
    resolve_waypoint_places,
)
from app.services.agent.tools.plan_trip_dependencies import (
    build_preparation_dependencies,
)
from app.services.agent.tools.plan_trip_input import validated_waypoints
from app.services.agent.tools.plan_trip_prepare import PreparedLeg, prepare_single_leg
from app.services.agent.tools.prepare_route_input import merge_route_preparation_input
from app.services.agent.tools.prepare_route_multi_stop import prepare_multi_stop
from app.services.agent.tools.prepare_route_results import (
    as_aggregate,
    candidate_evidence,
    nonfatal_prepare_result,
)
from app.services.agent.tools.route_option_assembly import (
    ROUTE_STATUSES,
    AggregatePreparation,
    candidate_digest,
    route_constraints,
    route_status,
)
from app.services.agent.tools.route_option_evidence import (
    coverage_for_prepared,
    serialize_evidence_envelopes,
)
from app.services.trips.incidents import incident_scan_is_complete

MAX_WAYPOINTS = 3
MAX_WAYPOINT_CHARS = 160


PREPARE_ROUTE_OPTIONS_SCHEMA = {
    "name": "prepare_route_options",
    "description": (
        "Prepare live NYC transit route candidates with evidence. Return only "
        "opaque candidate IDs and compact comparison facts; the outer agent "
        "selects a valid candidate and calls present_route exactly once."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "origin": {"type": "string", "description": "Trip start or profile place."},
            "destination": {"type": "string", "description": "NYC destination."},
            "destination_place_id": {
                "type": "string",
                "description": (
                    "Opaque place id returned by search_local_places or "
                    "get_place_details. The server resolves the stored "
                    "canonical identity and coordinates; a free-text "
                    "destination supplied alongside is ignored."
                ),
            },
            "discovery_set_id": {
                "type": "string",
                "description": (
                    "Optional discovery set id from search_local_places; "
                    "defaults to the session's active set."
                ),
            },
            "exclude_modes": {
                "type": "array",
                "items": {"type": "string", "enum": ["BUS", "SUBWAY", "RAIL"]},
                "description": "Hard transit-mode exclusions.",
            },
            "excluded_route_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hard route exclusions (e.g. Q, B35).",
            },
            "preferred_modes": {
                "type": "array",
                "items": {"type": "string", "enum": ["BUS", "SUBWAY", "RAIL"]},
                "description": "Preferred modes; server still enforces hard exclusions.",
            },
            "routing_preference": {
                "type": "string",
                "enum": ["FEWER_TRANSFERS", "LESS_WALKING"],
                "description": "Routing optimization preference.",
            },
            "departure_time": {"type": "string", "description": "RFC3339 departure."},
            "arrival_by": {"type": "string", "description": "RFC3339 arrival target."},
            "waypoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ordered intermediate stops, bounded server-side. Entries "
                    "may be direct NYC names/addresses or opaque place ids "
                    "from search_local_places."
                ),
            },
            "waypoint_dwell_minutes": {
                "type": "number",
                "description": "Dwell at each intermediate stop; defaults to 25 minutes.",
            },
            "avoid_crowds": {"type": "boolean", "description": "Avoid crowd evidence."},
            "avoid_stairs": {"type": "boolean", "description": "Require stair-avoiding access."},
            "accessibility_required": {"type": "boolean", "description": "Require verified accessible transfers."},
            "walking_tolerance_minutes": {"type": "integer", "description": "Maximum street-walking minutes."},
            "what_if": {"type": "boolean", "description": "Prepare a temporary what-if scenario."},
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _session_id(ctx: ToolContext) -> str:
    return str(getattr(ctx, "session_id", None) or "").strip()


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    session_id = _session_id(ctx)
    if not session_id:
        return ToolResult(ok=False, error="session is required for route preparation")
    explicit_set_id = str((tool_input or {}).get("discovery_set_id") or "").strip()
    merged = merge_route_preparation_input(tool_input or {}, ctx)
    resolved_destination, resolved_place_id, reference_error, destination_used_set = (
        await resolve_destination_reference(tool_input or {}, merged, ctx)
    )
    if reference_error:
        return ToolResult(ok=False, error=reference_error)
    if resolved_destination is not None:
        merged["destination"] = resolved_destination.name
    if not merged.get("destination"):
        return ToolResult(ok=False, error="destination is required (provide one or set trip destination first)")
    waypoints, waypoint_error = validated_waypoints(
        merged.get("waypoints"),
        max_waypoints=MAX_WAYPOINTS,
        max_waypoint_chars=MAX_WAYPOINT_CHARS,
    )
    if waypoint_error:
        return ToolResult(ok=False, error=waypoint_error)
    merged["waypoints"] = waypoints
    waypoint_places, waypoint_labels, waypoint_error, waypoint_used_set = (
        await resolve_waypoint_places(
            waypoints,
            tool_input or {},
            ctx,
        )
    )
    if waypoint_error:
        return ToolResult(ok=False, error=waypoint_error)
    merged["waypoints"] = waypoint_labels
    # Only a set that actually participated in successful canonical reference
    # resolution is allowed to become (or persist as) the active context.
    used_discovery_set_id = destination_used_set or waypoint_used_set
    started = time.monotonic()
    timings = _timings()
    await ctx.emit_progress("finding_routes", "active")
    if waypoints:
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
    if isinstance(prepared, ToolResult):
        await ctx.emit_progress("finding_routes", "complete")
        return nonfatal_prepare_result(prepared, merged, ctx, started)

    aggregate = as_aggregate(prepared)
    if resolved_destination is not None:
        merged["destination"] = aggregate.destination_place.name
    timings.update(aggregate.timings)
    candidate_ids = [candidate_store.new_candidate_id() for _ in aggregate.parsed_routes]
    hard_constraints = [
        route_constraints(route, merged) for route in aggregate.parsed_routes
    ]
    digests = []
    for index, route in enumerate(aggregate.parsed_routes):
        evidence = candidate_evidence(aggregate, index)
        digests.append(
            candidate_digest(
                route=route,
                candidate_id=candidate_ids[index],
                score=next(
                    (row for row in aggregate.scored if int(row.get("index", -1)) == index),
                    {"index": index},
                ),
                alerts=evidence["alerts"],
                incidents=evidence["incidents"],
                event_impacts=evidence["event_impacts"],
                prepared_arrival_by=merged.get("arrival_by"),
                hard_constraints=hard_constraints[index],
            )
        )
    coverage = aggregate.coverage or coverage_for_prepared(prepared)
    status = route_status(
        candidates=digests,
        coverage=coverage,
        incident_impacts=aggregate.incidents,
    )
    presentation_allowed = status in {"good", "degraded_usable"}
    candidates = [
        {"candidate_id": candidate_ids[index], "index": index, "digest": digest}
        for index, digest in enumerate(digests)
    ]
    set_id = candidate_store.store_candidate_set(
        session_id=session_id,
        payload={
            "tool_input": merged,
            "discovery_set_id": used_discovery_set_id,
            "destination_place_id": resolved_place_id,
            "origin_raw": merged.get("origin"),
            "destination_raw": merged.get("destination"),
            "origin_place": aggregate.origin_place.to_event_point(),
            "destination_place": aggregate.destination_place.to_event_point(),
            "departure_time": merged.get("departure_time"),
            "arrival_by": merged.get("arrival_by"),
            "excluded": sorted(set(merged.get("exclude_modes") or [])),
            "excluded_route_ids": list(merged.get("excluded_route_ids") or []),
            "parsed_routes": aggregate.parsed_routes,
            "scored": aggregate.scored,
            "relevant_alerts": aggregate.relevant_alerts,
            "incidents": aggregate.incidents,
            "event_evidence_status": aggregate.event_evidence_status,
            "event_impacts": aggregate.event_impacts,
            "event_failures": aggregate.event_failures,
            "crowd_search_metadata": aggregate.crowd_search_metadata,
            "incident_scan_metadata": aggregate.incident_scan_metadata,
            "evidence_envelopes": serialize_evidence_envelopes(aggregate.evidence_envelopes),
            "candidate_evidence": aggregate.candidate_evidence,
            "collect_crowd_evidence": aggregate.collect_crowd_evidence,
            "candidates": candidates,
            "evidence_coverage": coverage,
            "route_status": status,
            "hard_constraints": {"required": True},
            "candidate_kind": "multi_stop" if waypoints else "single_leg",
            "aggregate_segments": aggregate.aggregate_segments,
            "scenario_mode": merged["scenario"],
            "waypoints": merged.get("waypoints") or [],
            "timings": aggregate.timings,
        },
    )
    if isinstance(ctx.session, dict):
        if merged["scenario"] == "what_if":
            trip_state_module.bind_temporary_candidate_set(
                ctx.session,
                set_id,
                base_candidate_set_id=trip_state_module.get_trip_state(ctx.session).get(
                    "active_candidate_set_id"
                ),
            )
        else:
            trip_state_module.discard_scenario(ctx.session)
            if presentation_allowed:
                # A presentable active preparation replaces the accepted
                # canonical selection: update route facts, bind the new
                # candidate set, and clear the selected candidate until
                # present_route commits it.
                trip_state_module.update_trip_state(
                    ctx.session,
                    origin=merged.get("origin"),
                    destination=merged.get("destination"),
                    waypoints=merged.get("waypoints") or [],
                    planning_mode=(
                        "arrive_by"
                        if merged.get("arrival_by")
                        else "depart_at"
                        if merged.get("departure_time")
                        else "leave_now"
                    ),
                    requested_departure=merged.get("departure_time"),
                    requested_arrival=merged.get("arrival_by"),
                    active_candidate_set_id=set_id,
                    selected_candidate_id=None,
                )
            # A non-presentable active preparation must not move the accepted
            # selection: origin/destination/waypoints, planning mode/time,
            # active candidate set, and selected candidate stay bound. The new
            # set remains available as a separate audit record in the store.
            if explicit_set_id and used_discovery_set_id == explicit_set_id:
                # The explicit set resolved the destination/waypoints; bind it
                # (and its destination place) as the active discovery context.
                trip_state_module.bind_discovery_context(
                    ctx.session,
                    discovery_set_id=used_discovery_set_id,
                    selected_place_id=resolved_place_id,
                )
            elif resolved_place_id:
                trip_state_module.bind_selected_place(
                    ctx.session,
                    resolved_place_id,
                )
    timings["plan_trip_ms"] = (time.monotonic() - started) * 1000
    incomplete = not incident_scan_is_complete(aggregate.incident_scan_metadata)
    return ToolResult(
        ok=True,
        data={
            "candidate_set_id": set_id,
            "route_status": status if status in ROUTE_STATUSES else "insufficient_coverage",
            "presentation_allowed": presentation_allowed,
            "candidates": digests,
            "evidence_coverage": coverage,
            "incident_coverage_incomplete": incomplete,
            "candidate_count": len(digests),
        },
        summary=(
            f"prepared {len(digests)} route option(s); compare them and present one"
            if digests
            else "route coverage is insufficient for a safe recommendation"
        ),
        timings=timings,
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

    return await prepare_multi_stop(
        segment_tool_input,
        ctx,
        timings,
        waypoints,
        prepare_segment=prepare_segment,
        waypoint_labels=waypoint_labels,
        destination_raw=destination_raw,
    )


async def _prepare_segment(
    tool_input: dict,
    ctx: ToolContext,
    *,
    resolved_origin: Any | None = None,
    resolved_destination: Any | None = None,
) -> PreparedLeg | ToolResult:
    leg_timings = _timings()
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


def _timings() -> dict[str, float]:
    return {
        key: 0.0
        for key in (
            "place_resolution_ms",
            "route_provider_ms",
            "mta_ms",
            "ticketmaster_ms",
            "incident_ms",
            "advisor_ms",
            "scoring_ms",
            "enrichment_ms",
            "plan_trip_ms",
        )
    }


__all__ = ("PREPARE_ROUTE_OPTIONS_SCHEMA", "execute")
