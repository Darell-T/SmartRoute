"""Compatibility facade for the ``plan_trip`` agent tool.

Input validation, single-leg execution, chained coordination, and projection
live in focused modules.  This facade intentionally retains the historical
module attributes used by the tool registry and its focused patch-based tests.
"""

from __future__ import annotations

import importlib
import os

from app.services import ai_advisor
from app.services.evidence import current_payload, evidence_envelope
from app.services.agent import events as agent_events
from app.services.agent.tools._location import resolve_named_place
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.mta_feed import (
    fetch_service_alerts,
    filter_alerts_for_routes,
    get_stalled_buses,
    get_stalled_trains,
    parse_service_alerts,
)
from app.services.trips import (
    advisor_context,
    candidates,
    crowd_evidence,
    crowd_hotspots,
    enrichment,
    scoring,
    text,
)
from app.services.trips import incidents as trip_incidents
from app.utils import geo

from app.services.agent.tools import plan_trip_chain as _plan_trip_chain
from app.services.agent.tools import plan_trip_executor as _plan_trip_executor
from app.services.agent.tools import plan_trip_input as _plan_trip_input
from app.services.agent.tools import plan_trip_projection as _plan_trip_projection

directions_service = importlib.import_module("app.services.directions")

TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))
TRIP_ADVISOR_TIMEOUT_S = float(os.getenv("TRIP_ADVISOR_TIMEOUT_S", "8.0"))
LIVE_EVIDENCE_TTL_S = 120
EVENT_EVIDENCE_TTL_S = 300
AGENT_GROK_BUDGET_S = float(os.getenv("AGENT_GROK_BUDGET_S", "6.0"))

_ALL_MODES = ("SUBWAY", "BUS")
MAX_WAYPOINTS = 3
MAX_WAYPOINT_CHARS = 160

PLAN_TRIP_SCHEMA = {
    "name": "plan_trip",
    "description": (
        "Plan an NYC subway/bus trip between two points using live Google "
        "Routes transit directions, current MTA service alerts and stalled "
        "vehicles, and a live routing judge. Returns numbered route options "
        "as cards -- reference them by card_id, never by describing the raw "
        "route geometry (you are not given any)."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "origin": {
                "type": "string",
                "description": (
                    "Trip start: 'user' for the rider's current GPS location, "
                    "an NYC address or place name, or 'lat,lng' coordinates."
                ),
            },
            "destination": {
                "type": "string",
                "description": "Trip end: an NYC address, place name, or 'lat,lng' coordinates.",
            },
            "exclude_modes": {
                "type": "array",
                "items": {"type": "string", "enum": ["BUS", "SUBWAY", "RAIL"]},
                "description": "Transit modes to exclude, e.g. [\"BUS\"] for 'no bus, I have a cart'.",
            },
            "routing_preference": {
                "type": "string",
                "enum": ["FEWER_TRANSFERS", "LESS_WALKING"],
                "description": "Routing optimization. Defaults to FEWER_TRANSFERS.",
            },
            "departure_time": {
                "type": "string",
                "description": (
                    "RFC3339 timestamp with a UTC offset for a future departure "
                    "(e.g. 'tomorrow after the game'). Omit for 'leave now'. "
                    "Google Routes only accepts times within about 7 days out."
                ),
            },
            "arrival_by": {
                "type": "string",
                "description": (
                    "Optional timezone-aware RFC3339 arrival target. SmartRoute "
                    "derives a scheduled departure; do not combine with departure_time."
                ),
            },
            "waypoints": {
                "type": "array",
                "maxItems": MAX_WAYPOINTS,
                "items": {"type": "string", "maxLength": MAX_WAYPOINT_CHARS},
                "description": (
                    "Optional ordered intermediate stops. Use one plan_trip call "
                    "for a multi-stop trip; SmartRoute owns dwell timing and "
                    "returns one chained itinerary."
                ),
            },
            "waypoint_dwell_minutes": {
                "type": "number",
                "description": "Optional dwell time at each intermediate stop. Defaults to 25 minutes when omitted.",
            },
            "include_incident_scan": {
                "type": "boolean",
                "description": (
                    "Set true only when the rider specifically asks about "
                    "incidents, safety, or something unusual on the line -- "
                    "this scan is slow and must not run on ordinary requests."
                ),
            },
            "avoid_crowds": {
                "type": "boolean",
                "description": (
                    "True when the rider explicitly asks to avoid crowds, busy "
                    "stations, game traffic, or concert traffic. This makes "
                    "bounded event evidence mandatory for route comparison."
                ),
            },
            "max_candidates": {
                "type": "integer",
                "description": (
                    "Internal response-mode candidate budget. The orchestrator "
                    "sets this; do not infer a value from rider wording."
                ),
            },
        },
        "required": ["origin", "destination"],
        "additionalProperties": False,
    },
}

_point_label = _plan_trip_input.point_label
_summary_eta_minutes = _plan_trip_input.summary_eta_minutes
_parse_rfc3339 = _plan_trip_input.parse_rfc3339


async def _route_with_recovery(**kwargs) -> dict:
    return await _plan_trip_input.route_with_recovery(
        directions_service=directions_service,
        **kwargs,
    )


async def _derive_arrive_by_departure(**kwargs) -> str:
    return await _plan_trip_input.derive_arrive_by_departure(
        directions_service=directions_service,
        **kwargs,
    )


def _validated_waypoints(value: object) -> tuple[list[str], str | None]:
    return _plan_trip_input.validated_waypoints(
        value, max_waypoints=MAX_WAYPOINTS, max_waypoint_chars=MAX_WAYPOINT_CHARS,
    )


def _first_boarding_context(gtfs, step: dict, walking_minutes: int) -> dict:
    route_id = scoring._step_route_id(step).strip().upper()
    context = {
        "route_id": route_id,
        "mode": str(step.get("type") or "").lower(),
        "stop_name": step.get("departure_stop"),
        "coordinates": step.get("departure_coords"),
        "direction_label": step.get("direction"),
        "walking_minutes": walking_minutes,
    }
    pattern_index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    resolve = getattr(pattern_index, "resolve_route_segment", None)
    if not callable(resolve):
        return context
    resolved = resolve(
        route_id, step.get("departure_stop"), step.get("arrival_stop"),
        step.get("departure_coords"), step.get("arrival_coords"),
    )
    if resolved:
        context.update({
            "stop_id": resolved.get("origin_stop_id"),
            "direction_id": resolved.get("direction_id"),
            "destination_stop_id": resolved.get("destination_stop_id"),
        })
    return context


def _route_service_ids(route: list[dict]) -> set[str]:
    return {
        scoring._step_route_id(step).strip().upper()
        for step in route
        if step.get("type") in {"SUBWAY", "BUS"} and scoring._step_route_id(step).strip()
    }


_next_segment_departure = _plan_trip_chain._next_segment_departure
_safe_dwell_minutes = _plan_trip_chain._safe_dwell_minutes
_dedupe_lines = _plan_trip_chain._dedupe_lines


async def _execute_chained_trip(
    tool_input: dict,
    ctx: ToolContext,
    waypoints: list[str],
) -> ToolResult:
    return await _plan_trip_chain.execute_chained_trip(
        tool_input, ctx, waypoints, execute_leg=execute, summary_eta_minutes=_summary_eta_minutes,
    )


def _project_single_leg(**kwargs) -> ToolResult:
    return _plan_trip_projection.project_single_leg(
        **kwargs,
        point_label=_point_label,
        summary_eta_minutes=_summary_eta_minutes,
        first_boarding_context=_first_boarding_context,
        candidates_module=candidates,
        scoring_module=scoring,
        text_module=text,
        route_card_event=agent_events.RouteCardEvent,
    )


def _dependencies() -> _plan_trip_executor.PlanTripDependencies:
    """Take runtime bindings at call time so facade monkeypatches remain effective."""
    return _plan_trip_executor.PlanTripDependencies(
        directions_service=directions_service,
        route_with_recovery=_route_with_recovery,
        derive_arrive_by_departure=_derive_arrive_by_departure,
        resolve_named_place=resolve_named_place,
        collect_alerts=fetch_service_alerts,
        collect_stalled_trains=get_stalled_trains,
        collect_stalled_buses=get_stalled_buses,
        parse_service_alerts=parse_service_alerts,
        filter_alerts_for_routes=filter_alerts_for_routes,
        ai_advisor=ai_advisor,
        advisor_context=advisor_context,
        candidates=candidates,
        crowd_evidence=crowd_evidence,
        crowd_hotspots=crowd_hotspots,
        enrichment=enrichment,
        scoring=scoring,
        trip_incidents=trip_incidents,
        geo=geo,
        current_payload=current_payload,
        evidence_envelope=evidence_envelope,
        project=_project_single_leg,
        route_service_ids=_route_service_ids,
        context_timeout_seconds=TRIP_CONTEXT_TIMEOUT_S,
        advisor_timeout_seconds=TRIP_ADVISOR_TIMEOUT_S,
        incident_timeout_seconds=AGENT_GROK_BUDGET_S,
        live_evidence_ttl_seconds=LIVE_EVIDENCE_TTL_S,
        event_evidence_ttl_seconds=EVENT_EVIDENCE_TTL_S,
    )


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    """Validate waypoint budget, coordinate chained trips, or execute one leg."""
    timings = {
        "place_resolution_ms": 0.0,
        "route_provider_ms": 0.0,
        "mta_ms": 0.0,
        "ticketmaster_ms": 0.0,
        "scoring_ms": 0.0,
    }
    waypoints, waypoint_error = _validated_waypoints(tool_input.get("waypoints"))
    if waypoint_error:
        return ToolResult(ok=False, error=waypoint_error)
    if waypoints:
        return await _execute_chained_trip(tool_input, ctx, waypoints)
    return await _plan_trip_executor.execute_single_leg(
        tool_input, ctx, timings, dependencies=_dependencies(),
    )
