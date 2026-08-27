"""Dispatch transit evidence requests and own their operation handlers.

The public tool and leaf operations intentionally share this module so the
dispatch boundary, provider seams, and operation behavior have one canonical
import surface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.agent import candidate_store, trip_state
from app.services.agent.tools._types import ToolContext, ToolOutcome, ToolResult
from app.services.agent.tools.transit import (
    accessibility_status,
    check_area_conditions,
    lookup_arrivals,
    lookup_facts,
)
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools.transit import venue_crowd_window as venues
from app.services.agent.tools.transit.direction import (
    DirectionResolution,
    accepted_trip_direction,
    direction_clarification,
    normalize_direction_text,
    resolve_direction,
    resolve_model_direction,
)
from app.services.agent.tools.transit.transit_snapshot import collect_service_status
from app.services.trips.crowds import event_provider


@dataclass(frozen=True)
class OperationServices:
    lookup_arrivals: object
    accessibility_status: object
    lookup_facts: object
    check_area_conditions: object
    event_lookup: Callable[[dict, ToolContext], Any]
    venues: object
    transit_evidence: object
    wrap: Callable[..., ToolResult]
    grounding_succeeded: Callable[..., bool]
    note_transit: Callable[..., None]
    merged_timings: Callable[[list[ToolResult]], dict[str, float]]


def prepare_direction(
    operation: str,
    fields: dict[str, str | None],
    route_ids: list[str],
    ctx: ToolContext,
) -> tuple[dict[str, str | None], DirectionResolution, bool]:
    explicit_direction = fields.get("direction")
    if _is_unproven_accepted_headsign(ctx, route_ids, explicit_direction):
        explicit_direction = None
        fields = dict(fields)
        fields["direction"] = None
    direction_resolution = resolve_model_direction(
        explicit_direction,
        route_ids,
        session=ctx.session,
        gtfs=ctx.gtfs,
    )
    if explicit_direction and not direction_resolution.resolved:
        candidate_resolution, _ = _candidate_direction(
            ctx, route_ids, requested=explicit_direction
        )
        if candidate_resolution is not None:
            direction_resolution = candidate_resolution
    elif not explicit_direction:
        candidate_resolution, candidate_found = _candidate_direction(ctx, route_ids)
        if candidate_found:
            direction_resolution = candidate_resolution or DirectionResolution(
                requested=None,
                resolved=None,
                authoritative=False,
            )
        elif route_ids:
            accepted_direction = accepted_trip_direction(ctx, route_ids)
            if accepted_direction:
                direction_resolution = resolve_model_direction(
                    accepted_direction,
                    route_ids,
                    session=ctx.session,
                    gtfs=ctx.gtfs,
                )
    prepared_fields = fields
    if direction_resolution.resolved:
        prepared_fields = dict(fields)
        prepared_fields["direction"] = direction_resolution.resolved
    needs_clarification = bool(
        route_ids
        and operation in {"service_status", "arrivals"}
        and not direction_resolution.resolved
        and (operation == "arrivals" or bool(explicit_direction))
    )
    return prepared_fields, direction_resolution, needs_clarification


def _is_unproven_accepted_headsign(
    ctx: ToolContext, route_ids: list[str], value: str | None
) -> bool:
    """Reject an accepted-trip headsign echoed without current-turn support."""

    requested = normalize_direction_text(value)
    if not requested or resolve_direction(value).resolved:
        return False
    rider_message = normalize_direction_text(getattr(ctx, "rider_message", ""))
    if requested in rider_message:
        return False
    session = ctx.session
    active_trip = session.get("active_trip") if isinstance(session, dict) else None
    if not isinstance(active_trip, dict):
        return False
    wanted = {str(route).strip().upper() for route in route_ids if str(route).strip()}
    if not wanted:
        return False
    rows = []
    boarding = active_trip.get("first_boarding")
    if isinstance(boarding, dict):
        rows.append(boarding)
    itinerary = active_trip.get("canonical_itinerary")
    if isinstance(itinerary, dict):
        legs = itinerary.get("legs")
        if isinstance(legs, list):
            rows.extend(leg for leg in legs if isinstance(leg, dict))
    for row in rows:
        route = str(row.get("route_id") or row.get("service_id") or "").strip().upper()
        if wanted and route not in wanted:
            continue
        for key in ("headsign", "direction", "direction_label"):
            if normalize_direction_text(row.get(key)) == requested:
                return True
    return False


def _candidate_direction(
    ctx: ToolContext,
    route_ids: list[str],
    *,
    requested: str | None = None,
) -> tuple[DirectionResolution | None, bool]:
    """Resolve direction from matching canonical legs in the active set."""

    session = ctx.session
    session_id = str(ctx.session_id or "").strip()
    if not isinstance(session, dict) or not session_id or not route_ids:
        return None, False
    state = trip_state.get_trip_state(session)
    set_id = str(state.get("active_candidate_set_id") or "").strip()
    if not set_id:
        return None, False
    record = candidate_store.load_candidate_set(set_id, session_id=session_id)
    if not isinstance(record, dict):
        return None, False
    wanted = {str(route).strip().upper() for route in route_ids if str(route).strip()}
    resolutions: list[DirectionResolution] = []
    matching = False
    for candidate in record.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        digest = candidate.get("digest")
        if not isinstance(digest, dict):
            continue
        candidate_routes = {
            str(route).strip().upper()
            for route in (digest.get("transit_lines") or digest.get("route_ids") or [])
            if str(route).strip()
        }
        if not candidate_routes.intersection(wanted):
            continue
        matching = True
        itinerary = digest.get("_canonical_itinerary")
        legs = itinerary.get("legs") if isinstance(itinerary, dict) else None
        if not isinstance(legs, list):
            return None, True
        candidate_resolutions = []
        for leg in legs:
            if not isinstance(leg, dict) or not _is_matching_transit_leg(leg, wanted):
                continue
            resolution = _candidate_leg_direction(leg, requested=requested)
            if resolution is None:
                if requested:
                    continue
                return None, True
            candidate_resolutions.append(resolution)
        if not candidate_resolutions and not requested:
            return None, True
        resolutions.extend(candidate_resolutions)
    if not matching or not resolutions:
        return None, matching
    keys = {_direction_key(item.resolved) for item in resolutions}
    if "" in keys or len(keys) != 1:
        return None, True
    selected = resolutions[0]
    return DirectionResolution(
        requested=selected.requested,
        resolved=selected.resolved,
        authoritative=True,
        matched_value=selected.matched_value,
    ), True


def _is_matching_transit_leg(leg: dict[str, Any], wanted: set[str]) -> bool:
    mode = str(leg.get("mode") or leg.get("type") or "").strip().upper()
    route = str(leg.get("service_id") or leg.get("route_id") or "").strip().upper()
    return mode in {"SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL", "TRAM"} and route in wanted


def _candidate_leg_direction(
    leg: dict[str, Any], *, requested: str | None = None
) -> DirectionResolution | None:
    contexts = [leg]
    requested_text = normalize_direction_text(requested)
    for key in (
        "canonical_direction",
        "semantic_direction",
        "direction",
        "direction_label",
        "headsign",
        "destination_stop_name",
    ):
        value = leg.get(key)
        if value in (None, "", []):
            continue
        if requested_text and normalize_direction_text(value) != requested_text:
            continue
        resolution = resolve_direction(requested if requested_text else value, contexts)
        if resolution.resolved and resolution.authoritative:
            return resolution
    return None


def _direction_key(value: object) -> str:
    resolution = resolve_direction(value)
    return resolution.resolved or str(value or "").strip().casefold()


async def arrivals(
    route_ids: list[str],
    fields: dict[str, str | None],
    concerns: list[str],
    ctx: ToolContext,
    *,
    direction_resolution: DirectionResolution,
    services: OperationServices,
) -> ToolResult:
    if not route_ids:
        return ToolResult(ok=False, error="arrivals requires at least one route_id")
    if not fields.get("direction"):
        fields = dict(fields)
        fields["direction"] = accepted_trip_direction(ctx, route_ids)
    calls = [
        services.lookup_arrivals.execute(
            {
                "route_id": route_id,
                "stop_source": fields.get("stop_source") or "auto",
                **({"stop_query": fields["stop_query"]} if fields["stop_query"] else {}),
                **({"direction": fields["direction"]} if fields["direction"] else {}),
            },
            ctx,
        )
        for route_id in route_ids[:3]
    ]
    results = await asyncio.gather(*calls)
    if len(results) == 1:
        return services.wrap(
            "arrivals",
            results[0],
            ctx,
            route_ids=route_ids,
            fields=fields,
            concerns=concerns,
            direction_resolution=direction_resolution,
        )
    if any(not result.ok for result in results):
        failed = next(result for result in results if not result.ok)
        return services.wrap("arrivals", failed, ctx)
    merged = {"operation": "arrivals", "results": [result.data for result in results]}
    grounded = services.grounding_succeeded("arrivals", merged, ok=True)
    services.note_transit(ctx, grounded, "arrivals", data=merged)
    if not grounded:
        return ToolResult(
            ok=True,
            outcome=(
                ToolOutcome.NEEDS_CLARIFICATION
                if any(
                    result.outcome == ToolOutcome.NEEDS_CLARIFICATION
                    for result in results
                )
                else ToolOutcome.UNAVAILABLE
            ),
            data={"operation": "arrivals", "result": merged},
            summary="Arrival stop needs clarification",
            events=[],
            timings=services.merged_timings(results),
        )
    evidence_set_id, evidence = services.transit_evidence.build_evidence_set(
        session_id=str(getattr(ctx, "session_id", "") or ""),
        operation="arrivals",
        route_ids=route_ids,
        direction=fields.get("direction"),
        concerns=concerns,
        result=merged,
        direction_resolution=direction_resolution,
        turn_id=str(getattr(ctx, "turn_id", "") or ""),
    )
    return ToolResult(
        ok=True,
        data={
            "operation": "arrivals",
            "evidence_set_id": evidence_set_id,
            "evidence": evidence,
            "result": merged,
        },
        summary="Checked arrivals",
        events=[],
        timings=services.merged_timings(results),
    )


async def accessibility(
    fields: dict[str, str | None],
    route_ids: list[str],
    ctx: ToolContext,
    *,
    services: OperationServices,
) -> ToolResult:
    station = fields["station"]
    if not station:
        return ToolResult(ok=False, error="accessibility requires station")
    binding = None
    if _accessibility_provenance(fields) == "accepted_trip":
        binding, binding_error = services.transit_evidence.bind_accessibility_target(
            station,
            ctx.session,
            route_ids,
        )
        if binding_error:
            return ToolResult(
                ok=False, error=binding_error, outcome=ToolOutcome.UNAVAILABLE
            )
        if binding is None:
            return ToolResult(
                ok=False,
                error="the accepted itinerary is unavailable for accessibility lookup",
                outcome=ToolOutcome.UNAVAILABLE,
            )
    result = await services.accessibility_status.execute({"station": station}, ctx)
    if result.ok and isinstance(result.data, dict):
        if binding is not None and not services.transit_evidence.accessibility_result_matches(
            result.data, binding
        ):
            return ToolResult(
                ok=False,
                error="accessibility evidence did not match the accepted station",
                outcome=ToolOutcome.UNAVAILABLE,
            )
        result.data = {
            **result.data,
            "source": "mta_accessibility",
            "freshness": "current",
            "observed_at": datetime.now(UTC).isoformat(),
            **({"binding": binding} if binding is not None else {}),
        }
    bound_routes = (
        [str(route).strip().upper() for route in binding.get("route_ids") or []]
        if binding is not None
        else route_ids
    )
    return services.wrap(
        "accessibility",
        result,
        ctx,
        route_ids=bound_routes,
        fields=fields,
    )


def _accessibility_provenance(fields: dict[str, str | None]) -> str:
    source = (fields.get("station_source") or "auto").casefold()
    return source if source in {"current_turn", "accepted_trip"} else "current_turn"


async def fact(
    fields: dict[str, str | None], ctx: ToolContext, *, services: OperationServices
) -> ToolResult:
    topic = fields["topic"]
    if not topic:
        return ToolResult(ok=False, error="fact requires topic")
    result = await services.lookup_facts.execute({"topic": topic}, ctx)
    return services.wrap("fact", result, ctx)


async def area_conditions(
    fields: dict[str, str | None], ctx: ToolContext, *, services: OperationServices
) -> ToolResult:
    area = fields["area"]
    if not area:
        return ToolResult(ok=False, error="area_conditions requires area")
    payload: dict[str, Any] = {"area": area}
    if fields["at"]:
        payload["at"] = fields["at"]
    result = await services.check_area_conditions.execute(payload, ctx)
    return services.wrap("area_conditions", result, ctx)


async def event_schedule(
    fields: dict[str, str | None], ctx: ToolContext, *, services: OperationServices
) -> ToolResult:
    payload: dict[str, Any] = {}
    if fields["event_query"]:
        payload["query"] = fields["event_query"]
    if fields["venue"]:
        payload["venue"] = fields["venue"]
    if fields["at"]:
        payload["date"] = fields["at"][:10]
    result = await services.event_lookup(payload, ctx)
    return services.wrap("event_schedule", result, ctx)


EVENT_LOOKUP_SCHEMA = {
    "name": "event_lookup",
    "description": (
        "Use when the rider asks about a current NYC-area event, game, or "
        "concert, or when confirmed event timing is needed before estimating "
        "a venue crowd window. It grounds start time and estimates end time."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Optional event, team, or artist name. Omit for a bounded "
                    "all-events search near a route hub."
                ),
            },
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD to narrow the search to a single day (America/New_York).",
            },
            "venue": {
                "type": "string",
                "description": "Venue name to narrow the search, e.g. 'Madison Square Garden'.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


async def execute_event_lookup(tool_input: dict, ctx: ToolContext) -> ToolResult:
    """Adapt neutral event facts to the hidden agent tool contract."""

    result = await event_provider.lookup_events(tool_input, ctx)
    return ToolResult(
        ok=result.ok,
        data=result.data,
        summary=result.summary,
        error=result.error,
    )


async def venue_window(
    fields: dict[str, str | None], ctx: ToolContext, *, services: OperationServices
) -> ToolResult:
    venue = fields["venue"]
    end = fields["window_end"]
    if not venue or not end:
        return ToolResult(
            ok=False, error="venue_crowd_window requires venue and window_end"
        )
    payload: dict[str, Any] = {"venue": venue, "event_end_iso": end}
    if fields["window_start"]:
        payload["event_start_iso"] = fields["window_start"]
    result = await services.venues.execute(payload, ctx)
    return services.wrap("venue_crowd_window", result, ctx)

CHECK_TRANSIT_SCHEMA = {
    "name": "check_transit",
    "description": (
        "Look up current NYC transit evidence. service_status is for delays "
        "and line conditions. arrivals is for next-vehicle timing. Do not "
        "replace a status question with arrival times."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "service_status",
                    "arrivals",
                    "accessibility",
                    "fact",
                    "area_conditions",
                    "event_schedule",
                    "venue_crowd_window",
                ],
            },
            "route_ids": {"type": "array", "items": {"type": "string"}},
            "stop_source": {
                "type": "string",
                "enum": ["auto", "current_location", "accepted_trip", "named_station"],
                "description": (
                    "How the backend should resolve an arrivals stop. Use "
                    "current_location for the rider's authoritative GPS, accepted_trip "
                    "for the active trip boarding stop, named_station only with a "
                    "stop_query, and auto when existing context should decide."
                ),
            },
            "stop_query": {"type": ["string", "null"]},
            "direction": {
                "type": ["string", "null"],
                "description": (
                    "Model-declared rider direction, such as uptown, downtown, "
                    "or the requested train's destination/headsign when the rider "
                    "said it in this turn. Omit accepted-trip headsigns copied from "
                    "context; the server resolves those."
                ),
            },
            "area": {"type": ["string", "null"]},
            "station": {"type": ["string", "null"]},
            "station_source": {
                "type": "string",
                "enum": ["auto", "current_turn", "accepted_trip"],
                "description": (
                    "Accessibility station provenance. Use current_turn (or auto) for a "
                    "station named in this rider turn. Use accepted_trip only when the "
                    "station belongs to the active canonical itinerary. route_ids are "
                    "evidence scope only and do not select provenance. This field does "
                    "not affect arrivals stop resolution."
                ),
            },
            "topic": {"type": ["string", "null"]},
            "event_query": {"type": ["string", "null"]},
            "venue": {"type": ["string", "null"]},
            "at": {"type": ["string", "null"]},
            "window_start": {"type": ["string", "null"]},
            "window_end": {"type": ["string", "null"]},
            "concerns": {
                "type": "array",
                "items": {"type": "string", "enum": ["delay", "stalled_train"]},
                "description": (
                    "Optional typed filters for service-status evidence. Use an "
                    "empty array for general status so valid disruptions are not "
                    "accidentally filtered out."
                ),
            },
            "goal_key": {"type": "string"},
            "activity_label": {
                "type": ["string", "null"],
                "description": (
                    "Optional short, context-aware phrase describing this work in progress. "
                    "Use null for simple actions. Do not state results, timing, or internals."
                ),
            },
        },
        "required": [
            "operation",
            "route_ids",
            "stop_source",
            "stop_query",
            "direction",
            "area",
            "station",
            "station_source",
            "topic",
            "event_query",
            "venue",
            "at",
            "window_start",
            "window_end",
            "concerns",
            "goal_key",
            "activity_label",
        ],
        "additionalProperties": False,
    },
}

_TEXT_FIELDS = (
    "stop_source",
    "stop_query",
    "direction",
    "area",
    "station",
    "station_source",
    "topic",
    "event_query",
    "venue",
    "at",
    "window_start",
    "window_end",
    "goal_key",
)


def _text(value: object) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _route_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    routes: list[str] = []
    seen: set[str] = set()
    for item in value:
        route = str(item or "").strip().upper()
        if route and route not in seen:
            routes.append(route)
            seen.add(route)
    return routes


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    operation = str(tool_input.get("operation") or "").strip()
    fields = {name: _text(tool_input.get(name)) for name in _TEXT_FIELDS}
    route_ids = _route_ids(tool_input.get("route_ids"))
    concerns = _concerns(tool_input.get("concerns"))
    fields, direction_resolution, needs_clarification = prepare_direction(
        operation,
        fields,
        route_ids,
        ctx,
    )
    if needs_clarification:
        return _direction_needs_clarification(operation, route_ids, fields.get("direction"))

    services = OperationServices(
        lookup_arrivals=lookup_arrivals,
        accessibility_status=accessibility_status,
        lookup_facts=lookup_facts,
        check_area_conditions=check_area_conditions,
        event_lookup=execute_event_lookup,
        venues=venues,
        transit_evidence=transit_evidence,
        wrap=_wrap,
        grounding_succeeded=grounding_succeeded,
        note_transit=_note_transit,
        merged_timings=_merged_timings,
    )
    if operation == "service_status":
        result = await collect_service_status(route_ids, fields, ctx)
        return _wrap(
            "service_status",
            result,
            ctx,
            route_ids=route_ids,
            fields=fields,
            concerns=concerns,
            direction_resolution=direction_resolution,
            turn_id=str(getattr(ctx, "turn_id", "") or ""),
        )
    if operation == "arrivals":
        return await arrivals(
            route_ids,
            fields,
            concerns,
            ctx,
            direction_resolution=direction_resolution,
            services=services,
        )
    if operation == "accessibility":
        return await accessibility(fields, route_ids, ctx, services=services)
    if operation == "fact":
        return await fact(fields, ctx, services=services)
    if operation == "area_conditions":
        return await area_conditions(fields, ctx, services=services)
    if operation == "event_schedule":
        return await event_schedule(fields, ctx, services=services)
    if operation == "venue_crowd_window":
        return await venue_window(fields, ctx, services=services)
    return ToolResult(ok=False, error="unsupported transit operation")


def _direction_needs_clarification(
    operation: str, route_ids: list[str], requested: str | None
) -> ToolResult:
    return ToolResult(
        ok=True,
        outcome=ToolOutcome.NEEDS_CLARIFICATION,
        data={
            "operation": operation,
            **direction_clarification(route_ids, requested),
        },
        summary="Transit direction needs clarification",
    )


def _wrap(
    operation: str,
    result: ToolResult,
    ctx: ToolContext,
    *,
    route_ids: list[str] | None = None,
    fields: dict[str, str | None] | None = None,
    concerns: list[str] | None = None,
    direction_resolution: DirectionResolution | None = None,
    turn_id: str | None = None,
) -> ToolResult:
    data: dict[str, Any] = {"operation": operation, "result": result.data}
    evidence_set_id = None
    evidence = None
    grounded = grounding_succeeded(operation, result.data, ok=result.ok)
    if grounded:
        evidence_set_id, evidence = transit_evidence.build_evidence_set(
            session_id=str(getattr(ctx, "session_id", "") or ""),
            operation=operation,
            route_ids=route_ids or [],
            direction=(fields or {}).get("direction"),
            concerns=concerns or [],
            result=result.data,
            direction_resolution=direction_resolution,
            turn_id=str(turn_id or getattr(ctx, "turn_id", "") or ""),
        )
        data.update({"evidence_set_id": evidence_set_id, "evidence": evidence})
    _note_transit(ctx, grounded, operation, data=data)
    return ToolResult(
        ok=result.ok,
        data=data if result.ok else result.data,
        summary=result.summary,
        error=result.error,
        events=[],
        timings=dict(result.timings),
        outcome=(result.outcome if grounded else _unready_outcome(result)),
    )


def _unready_outcome(result: ToolResult) -> ToolOutcome:
    if result.outcome in {ToolOutcome.NEEDS_CLARIFICATION, ToolOutcome.UNAVAILABLE}:
        return result.outcome
    payload = result.data if isinstance(result.data, dict) else {}
    if (
        str(payload.get("source_status") or "").casefold() == "stop_not_resolved"
        or str(payload.get("status") or "").casefold() == "clarification_required"
    ):
        return ToolOutcome.NEEDS_CLARIFICATION
    return ToolOutcome.UNAVAILABLE if result.ok else ToolOutcome.FAILED


def grounding_succeeded(operation: str, data: object, *, ok: bool) -> bool:
    if not ok:
        return False
    if str(operation or "").strip().casefold() != "arrivals":
        return True
    payload = data if isinstance(data, dict) else {}
    nested = payload.get("result")
    if isinstance(nested, dict):
        payload = nested
    results = payload.get("results")
    arrivals = results if isinstance(results, list) else [payload]
    if not arrivals:
        return False
    unusable = {"provider_unavailable", "stop_not_resolved"}
    return all(
        str(item.get("source_status") or "").strip().casefold() not in unusable
        for item in arrivals
        if isinstance(item, dict)
    )


def _note_transit(
    ctx: ToolContext, ok: bool, operation: str, *, data: object = None
) -> None:
    evidence = getattr(ctx, "turn_evidence", None)
    if evidence is not None:
        evidence.note_check_transit(ok=ok, operation=operation, data=data)


def _merged_timings(results: list[ToolResult]) -> dict[str, float]:
    timings: dict[str, float] = {}
    for result in results:
        for name, duration in result.timings.items():
            timings[name] = timings.get(name, 0.0) + max(0.0, float(duration))
    return timings


def _concerns(value: object) -> list[str]:
    if isinstance(value, str):
        values = value.split("+")
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    for item in values:
        concern = "_".join(str(item or "").strip().casefold().split())
        if concern and concern not in result:
            result.append(concern)
    if "stalled_train" in result and "delay" not in result:
        result.append("delay")
    return result[:8]
