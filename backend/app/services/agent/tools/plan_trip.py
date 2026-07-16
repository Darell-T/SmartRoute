"""plan_trip tool: turns a rider request into route cards.

Composes the same building blocks routers/trips.py uses for /api/trip
(directions, live MTA context, the Haiku routing judge, GTFS enrichment) but
is deliberately NOT wired through trips.py itself -- that module imports
FastAPI at module scope, and this tool must stay importable (and unit
testable) without a running app. The underlying service calls are shared
and unchanged.

The model never sees route geometry: `execute()` returns a compact digest
per candidate for the model's context, plus full `route_card` SSE events
(byte-compatible `route` arrays with /api/trip) that the loop both streams
and stores in the session for later "the second option" references.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import secrets

from app.services import ai_advisor
from app.services.agent import events as agent_events
from app.services.agent.tools._location import resolve_named_point
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.mta_feed import (
    fetch_service_alerts,
    filter_alerts_for_routes,
    get_stalled_buses,
    get_stalled_trains,
    parse_service_alerts,
)
from app.services.trips import candidates, enrichment, scoring, text
from app.services.trips import incidents as trip_incidents
from app.utils import geo

directions_service = importlib.import_module("app.services.directions")

# Mirror routers/trips.py's tuning env vars (not imported from there -- that
# module pulls in FastAPI at import time, which this tool must not depend
# on for its own importability/testability).
TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))
TRIP_ADVISOR_TIMEOUT_S = float(os.getenv("TRIP_ADVISOR_TIMEOUT_S", "8.0"))
# Overrides (does not modify) trip_incidents' own 25s default -- the agent
# turn has a much tighter overall deadline than a one-shot /api/trip call.
AGENT_GROK_BUDGET_S = float(os.getenv("AGENT_GROK_BUDGET_S", "6.0"))

_ROUTE_TAG_RE = re.compile(r"\[ROUTE:(\d+)\]")
_ALL_MODES = ("SUBWAY", "BUS")

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
            "include_incident_scan": {
                "type": "boolean",
                "description": (
                    "Set true only when the rider specifically asks about "
                    "incidents, safety, or something unusual on the line -- "
                    "this scan is slow and must not run on ordinary requests."
                ),
            },
        },
        "required": ["origin", "destination"],
        "additionalProperties": False,
    },
}


def _point_label(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value or value.lower() == "user":
        return "your location"
    return text._safe_text(value, 80)


def _walk_minutes(route: list[dict]) -> float:
    """Sum of walking-leg time via haversine distance -- WALK steps carry no
    duration of their own in the parsed shape (only the whole route's total
    duration, repeated per step), so this is computed from endpoints."""
    total = 0.0
    for step in route:
        if step.get("type") != "WALK":
            continue
        start = step.get("start_point") or {}
        end = step.get("end_point") or {}
        lat1, lon1 = start.get("latitude"), start.get("longitude")
        lat2, lon2 = end.get("latitude"), end.get("longitude")
        if None in (lat1, lon1, lat2, lon2):
            continue
        meters = geo.distance_meters(lat1, lon1, lat2, lon2)
        total += geo.walking_time_minutes(meters)
    return round(total, 1)


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    origin_raw = str(tool_input.get("origin") or "")
    destination_raw = str(tool_input.get("destination") or "").strip()
    if not destination_raw:
        return ToolResult(ok=False, error="destination is required")

    # Cheap validation before any network call (geocoding, Google Routes).
    excluded = {str(m).strip().upper() for m in (tool_input.get("exclude_modes") or [])}
    allowed_modes = [m for m in _ALL_MODES if m not in excluded]
    if not allowed_modes:
        return ToolResult(ok=False, error="no transit modes left after excluding all of them")

    origin_coords, origin_error = await resolve_named_point(
        origin_raw,
        ctx,
        missing_location_message="I need your current location to plan from 'origin' -- share GPS or give me an address instead.",
    )
    if origin_coords is None:
        return ToolResult(ok=False, error=origin_error or "could not resolve the origin")

    dest_coords, dest_error = await resolve_named_point(
        destination_raw,
        ctx,
        missing_location_message=(
            "I need your current location to plan from 'destination' -- share GPS or give me an address instead."
        ),
    )
    if dest_coords is None:
        return ToolResult(ok=False, error=dest_error or "could not find that destination in NYC")

    routing_preference = tool_input.get("routing_preference") or "FEWER_TRANSFERS"
    departure_time = tool_input.get("departure_time") or None

    try:
        response = await directions_service.get_transit_route(
            origin_coords,
            destination_raw,
            dest_coords,
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
    except directions_service.GoogleRoutesError as exc:
        print(f"[agent-plan_trip] routing failed code={exc.code}")
        return ToolResult(ok=False, error=f"routing failed ({exc.code})")

    parsed_routes = directions_service.parse_response(response)
    if not parsed_routes:
        return ToolResult(ok=False, error="no transit route found between those points")

    route_ids, bus_route_ids = candidates._collect_route_and_bus_ids(parsed_routes)

    try:
        raw_alerts, stalled, stalled_buses = await asyncio.wait_for(
            asyncio.gather(
                fetch_service_alerts(),
                get_stalled_trains(route_ids),
                get_stalled_buses(bus_route_ids),
                return_exceptions=True,
            ),
            timeout=TRIP_CONTEXT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raw_alerts, stalled, stalled_buses = [], [], []
    raw_alerts = [] if isinstance(raw_alerts, BaseException) else raw_alerts
    stalled = [] if isinstance(stalled, BaseException) else stalled
    stalled_buses = [] if isinstance(stalled_buses, BaseException) else stalled_buses

    parsed_alerts = parse_service_alerts(raw_alerts) if raw_alerts else []
    relevant_alerts = filter_alerts_for_routes(parsed_alerts, route_ids)

    incidents: list = []
    if tool_input.get("include_incident_scan"):
        station_names = trip_incidents._scan_station_names(ctx.gtfs, parsed_routes)
        try:
            incidents = await asyncio.wait_for(
                trip_incidents._scan_route_incidents(station_names),
                timeout=AGENT_GROK_BUDGET_S,
            )
        except asyncio.TimeoutError:
            print(f"[agent-plan_trip] incident scan timed out ({AGENT_GROK_BUDGET_S:.0f}s)")
            incidents = []

    judge_payload = {
        "routes": parsed_routes,
        "route_candidate_labels": candidates._build_route_candidate_labels(parsed_routes),
        "service_alerts": relevant_alerts,
        "incidents": incidents,
        "stalled_trains": stalled or [],
        "stalled_buses": stalled_buses or [],
    }

    try:
        raw_recommendation = await asyncio.wait_for(
            ai_advisor.collect_recommendation(judge_payload), timeout=TRIP_ADVISOR_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        print(f"[agent-plan_trip] advisor timed out ({TRIP_ADVISOR_TIMEOUT_S:.2f}s)")
        raw_recommendation = "[ROUTE:0] Live reasoning timed out; showing the fastest option."
    except Exception as exc:
        print(f"[agent-plan_trip] advisor unavailable: {exc!r}")
        raw_recommendation = "[ROUTE:0] Live reasoning was unavailable; showing the fastest option."

    chosen_index = 0
    route_tag_match = _ROUTE_TAG_RE.search(raw_recommendation)
    if route_tag_match:
        chosen_index = int(route_tag_match.group(1))
        if chosen_index >= len(parsed_routes):
            chosen_index = 0
    analysis_selected_index, candidate_analysis = candidates._parse_candidate_analysis(raw_recommendation)
    if not route_tag_match and analysis_selected_index is not None:
        chosen_index = analysis_selected_index
        if chosen_index >= len(parsed_routes):
            chosen_index = 0

    chosen_route = parsed_routes[chosen_index]
    await enrichment._enrich_route(ctx.gtfs, chosen_route)

    scored = scoring._score_routes(parsed_routes, relevant_alerts)
    display_candidates = candidates._build_route_candidates(parsed_routes, chosen_index, candidate_analysis, scored)

    origin_label = _point_label(origin_raw)
    destination_label = _point_label(destination_raw)

    digest = []
    events = []
    session_cards = []
    for index, route in enumerate(parsed_routes):
        card_id = f"rc_{secrets.token_hex(4)}"
        is_recommended = index == chosen_index
        cand = display_candidates[index]
        lines = cand["score_breakdown"]["transit_lines"]
        eta_minutes = cand["total_minutes"]
        transfers = cand["score_breakdown"]["transfers"]
        reason = cand["recommendation_reason"] if is_recommended else cand["rejection_reason"]
        alert_headlines = [text._safe_text(a.get("header") or "", 80) for a in (relevant_alerts or [])][:3]
        first_step = route[0] if route else {}
        last_step = route[-1] if route else {}

        digest.append(
            {
                "card_id": card_id,
                "lines": lines,
                "eta_minutes": eta_minutes,
                "transfers": transfers,
                "departs_iso": first_step.get("departure_time_iso"),
                "arrives_iso": last_step.get("arrival_time_iso"),
                "walk_minutes": _walk_minutes(route),
                "alert_headlines": alert_headlines,
                "reason": reason,
            }
        )
        summary = {
            "eta_minutes": eta_minutes,
            "transfers": transfers,
            "lines": lines,
            "reason": reason,
        }
        events.append(
            agent_events.RouteCardEvent(
                card_id=card_id,
                turn_id=ctx.turn_id,
                role="recommended" if is_recommended else "alternative",
                origin={"label": origin_label, "lat": origin_coords[0], "lng": origin_coords[1]},
                destination={"label": destination_label, "lat": dest_coords[0], "lng": dest_coords[1]},
                depart_iso=departure_time,
                summary=summary,
                route=route,
                alerts=relevant_alerts,
            )
        )
        session_cards.append(
            {
                "card_id": card_id,
                "role": "recommended" if is_recommended else "alternative",
                "lines": lines,
                "eta_minutes": eta_minutes,
            }
        )

    recommended_lines = digest[chosen_index]["lines"]
    tool_summary = (
        f"found {len(parsed_routes)} route(s) to {destination_label}; "
        f"recommended {'/'.join(recommended_lines) or 'a walking route'}"
    )
    return ToolResult(
        ok=True,
        data={"candidates": digest},
        summary=tool_summary,
        events=events,
        session_route_cards=session_cards,
    )
