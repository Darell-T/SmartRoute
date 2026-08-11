"""Present one validated server-owned route candidate as the route card."""

from __future__ import annotations

import time
from typing import Any

from app.services.agent import candidate_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._location import ResolvedPlace
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.plan_trip_first_leg import first_leg_arrival_context
from app.services.agent.tools.route_option_assembly import route_constraints
from app.services.agent.turn_telemetry import record_phase_ms
from app.services.trips.itinerary import build_chained_itinerary


def _presentation_dependencies():
    """Lazy binding import keeps the registry/Live Map graph projection-free.

    ``route_presentation_dependencies`` binds the conversational projection
    wrapper; importing it at module level would pull that projection into any
    process that merely imports this tool module (including the direct Live
    Map ``/api/trip`` graph).
    """
    from app.services.agent.tools.route_presentation_dependencies import (
        build_presentation_dependencies,
    )

    return build_presentation_dependencies()


PRESENT_ROUTE_SCHEMA = {
    "name": "present_route",
    "description": (
        "Present exactly one previously prepared candidate. Use only the "
        "opaque candidate_id returned by prepare_route_options; the server "
        "owns the canonical route, timing, geometry, and transfer facts."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "candidate_id": {"type": "string", "description": "Server-issued candidate id."},
            "commit_scenario": {
                "type": "boolean",
                "description": "Commit a temporary what-if only when the rider explicitly asks.",
            },
        },
        "required": ["candidate_id"],
        "additionalProperties": False,
    },
}


def _session_id(ctx: ToolContext) -> str:
    return str(getattr(ctx, "session_id", None) or "").strip()


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    session_id = _session_id(ctx)
    if not session_id:
        return ToolResult(ok=False, error="session is required for route presentation")
    candidate_id = str(tool_input.get("candidate_id") or "").strip()
    if not candidate_id:
        return ToolResult(ok=False, error="candidate_id is required")

    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    candidate_set_id = _candidate_set_id(state)
    if not candidate_set_id:
        return ToolResult(ok=False, error="no active candidate set; call prepare_route_options first")
    if not _set_is_bound(candidate_set_id, state):
        return ToolResult(ok=False, error="candidate set is not active for this trip")
    record, entry, error = candidate_store.get_candidate(
        candidate_set_id,
        candidate_id,
        session_id=session_id,
    )
    if error or record is None or entry is None:
        return ToolResult(ok=False, error=error or "candidate not found")
    status = str(record.get("route_status") or "good")
    if status not in {"good", "degraded_usable"}:
        return ToolResult(
            ok=False,
            error=f"route status {status} does not permit presenting a recommendation",
        )
    chosen_index = _candidate_index(entry)
    parsed_routes = [route for route in (record.get("parsed_routes") or []) if isinstance(route, list)]
    if chosen_index < 0 or chosen_index >= len(parsed_routes):
        return ToolResult(ok=False, error="candidate index is out of range")
    tool_input_body = dict(record.get("tool_input") or {})
    dependencies = _presentation_dependencies()
    chosen_route, canonical_override, first_route = await _load_canonical_candidate(
        record,
        chosen_index,
        parsed_routes[chosen_index],
        ctx,
        dependencies,
    )
    parsed_routes[chosen_index] = chosen_route
    constraints = route_constraints(chosen_route, tool_input_body)
    if constraints.get("satisfied") is not True:
        return ToolResult(
            ok=False,
            error="selected candidate does not satisfy the server-owned hard constraints",
        )

    origin_place = _place_from_dict(record.get("origin_place"))
    destination_place = _place_from_dict(record.get("destination_place"))
    if origin_place is None or destination_place is None:
        return ToolResult(ok=False, error="stored place identity is invalid")

    scenario_mode = str(record.get("scenario_mode") or "active")
    commit_scenario = scenario_mode == "what_if" and bool(
        tool_input.get("commit_scenario")
    )
    await ctx.emit_progress("comparing_options", "active")
    scored = [row for row in (record.get("scored") or []) if isinstance(row, dict)]
    if not any(int(row.get("index", -1)) == chosen_index for row in scored):
        scored.append(
            {
                "index": chosen_index,
                "score": chosen_index,
                "total_minutes": 0,
                "transfers": 0,
            }
        )
    candidate_evidence = _candidate_evidence(record, chosen_index)
    timings = dict(record.get("timings") or {})
    plan_origin = time.monotonic()
    first_leg_context = await first_leg_arrival_context(
        tool_input_body,
        ctx,
        origin_place,
        first_route,
        dependencies,
    )
    evidence_envelopes = {
        name: _EnvelopeShim(payload)
        for name, payload in (candidate_evidence.get("evidence_envelopes") or {}).items()
        if isinstance(payload, dict)
    }
    projected = dependencies.project(
        tool_input=tool_input_body,
        ctx=ctx,
        timings=timings,
        parsed_routes=parsed_routes,
        origin_raw=str(record.get("origin_raw") or ""),
        destination_raw=str(record.get("destination_raw") or ""),
        origin_place=origin_place,
        destination_place=destination_place,
        departure_time=record.get("departure_time"),
        arrival_by=record.get("arrival_by"),
        excluded=set(record.get("excluded") or []),
        relevant_alerts=list(candidate_evidence.get("alerts") or []),
        event_evidence_status=str(
            candidate_evidence.get("event_evidence_status") or "unscanned"
        ),
        event_impacts=list(candidate_evidence.get("event_impacts") or []),
        event_failures=list(candidate_evidence.get("event_failures") or []),
        crowd_search_metadata=dict(candidate_evidence.get("crowd_search_metadata") or {}),
        incident_scan_metadata=dict(
            candidate_evidence.get("incident_scan_metadata")
            or record.get("incident_scan_metadata")
            or {}
        ),
        evidence_envelopes=evidence_envelopes,
        collect_crowd_evidence=bool(record.get("collect_crowd_evidence")),
        chosen_index=chosen_index,
        candidate_analysis={
            chosen_index: {
                "recommendation_reason": "",
                "rejection_reason": "",
            }
        },
        scored=scored,
        decision_reason="outer_agent_selection",
        selection_log_reason="outer_agent_selection",
        scoring_event_impacts=[
            impact
            for impact in (candidate_evidence.get("event_impacts") or [])
            if float(impact.get("risk_score") or 0) > 0
        ],
        first_leg_arrival_context=first_leg_context,
        advisor_recommendation="",
        include_alternatives=False,
        itinerary_overrides=(
            {chosen_index: canonical_override} if canonical_override is not None else None
        ),
    )
    if not projected.ok:
        await ctx.emit_progress("comparing_options", "complete")
        return projected
    if isinstance(projected.data, dict):
        projected.data = {
            key: value
            for key, value in projected.data.items()
            if key not in {"candidate_id", "candidate_set_id", "selected_candidate_id"}
        }
        projected.data.update(
            {
                "selection_source": "outer_agent",
                "route_status": status,
            }
        )
    # Final one-time gate: consume the candidate only after every fallible
    # canonical load, first-leg context, and projection step succeeded and
    # immediately before session-owned selection/commit mutation. A failure
    # here (duplicate, concurrent race, or store error) leaves the candidate
    # retryable, must not mutate selection state, and must not publish the
    # card. The reservation itself stays atomic in the store.
    if scenario_mode != "what_if" or commit_scenario:
        reservation_error = candidate_store.mark_presented(
            candidate_set_id,
            candidate_id,
            session_id=session_id,
        )
        if reservation_error:
            await ctx.emit_progress("comparing_options", "complete")
            return ToolResult(ok=False, error=reservation_error)
    if scenario_mode == "what_if":
        if commit_scenario:
            trip_state_module.commit_scenario(
                session,
                candidate_set_id=candidate_set_id,
                candidate_id=candidate_id,
                tool_input=tool_input_body,
            )
            # The accepted scenario is now the active trip: make the discovery
            # context it actually used (validated at prepare time) active too,
            # re-validating the set so commit never activates an invented,
            # cross-session, or expired set.
            _activate_stored_discovery_context(record, session, session_id)
        else:
            trip_state_module.bind_temporary_selected_candidate(session, candidate_id)
            projected.session_route_cards = []
    else:
        trip_state_module.bind_selected_candidate(session, candidate_id)
    timings["enrichment_ms"] = (time.monotonic() - plan_origin) * 1000
    record_phase_ms(ctx.telemetry, "enrichment_complete_ms", timings["enrichment_ms"])
    projected.timings = timings
    await ctx.emit_progress("comparing_options", "complete")
    return projected


def _activate_stored_discovery_context(
    record: dict[str, Any],
    session: dict,
    session_id: str,
) -> None:
    """Bind the committed scenario's discovery set and destination place.

    The candidate record retains only ids that participated in successful
    canonical resolution at prepare time. The set is re-loaded through the
    server-owned store (session-scoped, unexpired) before binding so an
    expired or cross-session set can never be activated here.
    """

    from app.services.agent import discovery_store

    discovery_set_id = str(record.get("discovery_set_id") or "").strip()
    if not discovery_set_id:
        return
    if (
        discovery_store.load_discovery_set(discovery_set_id, session_id=session_id)
        is None
    ):
        return
    destination_place_id = str(record.get("destination_place_id") or "").strip()
    if not discovery_store.is_opaque_place_id(destination_place_id):
        destination_place_id = ""
    trip_state_module.bind_discovery_context(
        session,
        discovery_set_id=discovery_set_id,
        selected_place_id=destination_place_id or None,
    )


async def _load_canonical_candidate(
    record: dict[str, Any],
    chosen_index: int,
    stored_route: list[dict],
    ctx: ToolContext,
    dependencies: Any,
) -> tuple[list[dict], dict | None, list[dict]]:
    if str(record.get("candidate_kind") or "single_leg") != "multi_stop":
        await dependencies.enrichment._enrich_route(ctx.gtfs, stored_route)
        return stored_route, None, stored_route
    all_segments = record.get("aggregate_segments") or []
    segments = all_segments[chosen_index] if chosen_index < len(all_segments) else []
    if not isinstance(segments, list) or not segments:
        return stored_route, None, stored_route
    flattened: list[dict] = []
    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        route = segment.get("steps")
        if not isinstance(route, list):
            continue
        await dependencies.enrichment._enrich_route(ctx.gtfs, route)
        flattened.extend({**step, "segment_index": segment_index} for step in route)
    origin = record.get("origin_place") or {}
    destination = record.get("destination_place") or {}
    override = build_chained_itinerary(
        segments,
        origin=origin,
        final_destination=destination,
        planning_mode=(
            "arrive_by"
            if record.get("arrival_by")
            else "depart_at"
            if record.get("departure_time")
            else "leave_now"
        ),
        requested_departure=record.get("departure_time"),
        requested_arrival=record.get("arrival_by"),
    )
    first_route = segments[0].get("steps") if isinstance(segments[0], dict) else []
    return flattened or stored_route, override, first_route if isinstance(first_route, list) else stored_route


def _candidate_set_id(state: dict) -> str:
    return str(
        state.get("temporary_candidate_set_id")
        or state.get("active_candidate_set_id")
        or ""
    ).strip()


def _set_is_bound(candidate_set_id: str, state: dict) -> bool:
    return candidate_set_id in {
        state.get("active_candidate_set_id"),
        state.get("temporary_candidate_set_id"),
    }


def _candidate_index(entry: dict) -> int:
    try:
        return int(entry.get("index") or 0)
    except (TypeError, ValueError):
        return -1


def _place_from_dict(raw: object) -> ResolvedPlace | None:
    if not isinstance(raw, dict):
        return None
    try:
        latitude = raw.get("latitude")
        if latitude is None:
            latitude = raw["lat"]
        longitude = raw.get("longitude")
        if longitude is None:
            longitude = raw["lng"]
        return ResolvedPlace(
            name=str(raw.get("name") or raw.get("label") or "Place"),
            latitude=float(latitude),
            longitude=float(longitude),
            source=str(raw.get("source") or "geocode"),
            address=raw.get("address"),
            place_id=raw.get("place_id"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _candidate_evidence(record: dict[str, Any], chosen_index: int) -> dict[str, Any]:
    values = record.get("candidate_evidence")
    if isinstance(values, list) and 0 <= chosen_index < len(values):
        value = values[chosen_index]
        if isinstance(value, dict):
            return value
    return {
        "alerts": list(record.get("relevant_alerts") or []),
        "incidents": list(record.get("incidents") or []),
        "event_impacts": [
            impact
            for impact in record.get("event_impacts") or []
            if isinstance(impact, dict) and impact.get("route_index") == chosen_index
        ],
        "event_evidence_status": record.get("event_evidence_status") or "unscanned",
        "event_failures": list(record.get("event_failures") or []),
        "crowd_search_metadata": dict(record.get("crowd_search_metadata") or {}),
        "incident_scan_metadata": dict(record.get("incident_scan_metadata") or {}),
        "evidence_envelopes": dict(record.get("evidence_envelopes") or {}),
    }


class _EnvelopeShim:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_model_dict(self, *, empty: Any, now: Any = None) -> dict[str, Any]:
        result = dict(self._payload)
        if result.get("status") != "current":
            result["payload"] = empty
        return result

    def current_payload(self, now: Any = None) -> Any:
        if self._payload.get("status") != "current":
            return None
        return self._payload.get("payload")
