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
import math
import os
import secrets
from datetime import datetime, timedelta

from app.services import ai_advisor
from app.services.agent import events as agent_events
from app.services.agent.tools._location import (
    ResolvedPlace,
    canonical_display_name,
    resolve_named_place,
)
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.mta_feed import (
    fetch_service_alerts,
    filter_alerts_for_routes,
    get_stalled_buses,
    get_stalled_trains,
    parse_service_alerts,
)
from app.services.trips import advisor_context, candidates, enrichment, event_crowd, scoring, text
from app.services.trips import incidents as trip_incidents
from app.services.trips.itinerary import build_canonical_itinerary, build_chained_itinerary
from app.services.trips.recommendation_reasons import (
    build_recommendation_reasons,
    format_recommendation_reason,
)
from app.services.trips.selection_decision import build_route_selection_decision
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
            "arrival_by": {
                "type": "string",
                "description": (
                    "Optional timezone-aware RFC3339 arrival target. SmartRoute "
                    "derives a scheduled departure; do not combine with departure_time."
                ),
            },
            "waypoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional ordered intermediate stops. Use one plan_trip call "
                    "for a multi-stop trip; SmartRoute owns dwell timing and "
                    "returns one chained itinerary."
                ),
            },
            "waypoint_dwell_minutes": {
                "type": "number",
                "description": (
                    "Optional dwell time at each intermediate stop. Defaults to "
                    "25 minutes when omitted."
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


def _point_label(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value or value.lower() == "user":
        return "your location"
    return text._safe_text(canonical_display_name(value), 80)


def _summary_eta_minutes(route: list[dict], total_duration_seconds: int) -> int:
    """Card/digest ETA from itinerary seconds only.

    Empty route → 0 (no trip). Otherwise max(1, round(seconds/60)) so a
    sub-minute non-empty trip still shows as 1 minute.
    """
    if not route:
        return 0
    return max(1, round(int(total_duration_seconds) / 60))


def _parse_rfc3339(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be RFC3339 with a timezone offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


async def _route_with_recovery(
    *,
    origin: ResolvedPlace,
    destination: ResolvedPlace,
    destination_query: str,
    allowed_modes: list[str],
    routing_preference: str,
    departure_time: str | None,
) -> dict:
    """Try the rider's resolved label first, then privately recover by coords."""
    try:
        return await directions_service.get_transit_route(
            (origin.latitude, origin.longitude),
            destination_query,
            None,
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
    except directions_service.GoogleRoutesError:
        # This is an internal provider recovery, not a second rider operation.
        # Keep the named destination attached to the resulting itinerary.
        return await directions_service.get_transit_route(
            (origin.latitude, origin.longitude),
            destination_query,
            (destination.latitude, destination.longitude),
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )


async def _derive_arrive_by_departure(
    *,
    origin: ResolvedPlace,
    destination: ResolvedPlace,
    destination_query: str,
    arrival_by: str,
    allowed_modes: list[str],
    routing_preference: str,
) -> str:
    """Estimate a provider-supported departure for an explicit arrival target.

    Google Routes' transit endpoint takes departure time, not arrival time.
    The probe is an internal estimate only; the actual planning request below
    is still made at the derived departure and its provider timestamps remain
    canonical.
    """
    target = _parse_rfc3339(arrival_by, field="arrival_by")
    probe = await _route_with_recovery(
        origin=origin,
        destination=destination,
        destination_query=destination_query,
        allowed_modes=allowed_modes,
        routing_preference=routing_preference,
        departure_time=target.isoformat(),
    )
    parsed = directions_service.parse_response(probe)
    if not parsed:
        raise directions_service.GoogleRoutesError(
            "no_route",
            "no route available to estimate arrive-by departure",
        )
    durations = [
        int(step["route_total_seconds"])
        for route in parsed
        for step in route
        if isinstance(step.get("route_total_seconds"), (int, float))
    ]
    if not durations:
        raise directions_service.GoogleRoutesError(
            "no_duration",
            "provider did not return a route duration for arrive-by planning",
        )
    return (target - timedelta(seconds=min(durations))).isoformat()


def _normalized_waypoints(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            continue
        place = raw.strip()
        if place and place not in seen:
            seen.append(place)
    return seen


def _next_segment_departure(arrival_at: object, dwell_minutes: int) -> str | None:
    if not isinstance(arrival_at, str) or not arrival_at.strip():
        return None
    try:
        arrival = datetime.fromisoformat(arrival_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (arrival + timedelta(minutes=max(0, dwell_minutes))).isoformat()


def _safe_dwell_minutes(value: object) -> tuple[int, str]:
    if value is None:
        return 25, "default"
    try:
        return max(0, int(round(float(value)))), "user"
    except (TypeError, ValueError):
        return 25, "default"


def _dedupe_lines(routes: list[list[dict]]) -> list[str]:
    lines: list[str] = []
    for route in routes:
        for step in route:
            if step.get("type") not in ("SUBWAY", "BUS"):
                continue
            line = str(step.get("route_id") or step.get("train_line") or "").strip()
            if line and line not in lines:
                lines.append(line)
    return lines


async def _execute_chained_trip(
    tool_input: dict,
    ctx: ToolContext,
    waypoints: list[str],
) -> ToolResult:
    """Plan ordered OD legs, then emit one server-owned chained card.

    This deliberately delegates each individual leg to the established
    ``execute`` path so directions parsing, live context, candidate selection,
    enrichment, and canonical normalization remain the production path. Only
    the final event assembly changes: the rider receives one itinerary rather
    than frontend-spliced cards with inferred dwell.
    """
    origin = str(tool_input.get("origin") or "")
    destination = str(tool_input.get("destination") or "").strip()
    if not destination:
        return ToolResult(ok=False, error="destination is required")
    if tool_input.get("arrival_by"):
        return ToolResult(
            ok=False,
            error="arrive-by planning with intermediate stops is not available yet",
        )

    dwell_minutes, dwell_source = _safe_dwell_minutes(
        tool_input.get("waypoint_dwell_minutes")
    )
    ordered_places = [*waypoints, destination]
    segment_results: list[ToolResult] = []
    current_origin = origin
    departure_time = tool_input.get("departure_time")

    for index, segment_destination in enumerate(ordered_places):
        leg_input = {
            key: value
            for key, value in tool_input.items()
            if key not in {"waypoints", "waypoint_dwell_minutes", "destination", "origin", "departure_time"}
        }
        leg_input.update(
            {
                "origin": current_origin,
                "destination": segment_destination,
            }
        )
        if departure_time:
            leg_input["departure_time"] = departure_time

        result = await execute(leg_input, ctx)
        if not result.ok:
            return ToolResult(
                ok=False,
                error=f"could not plan segment {index + 1}: {result.error or 'routing failed'}",
            )
        segment_results.append(result)

        recommended = next(
            (
                event
                for event in result.events
                if isinstance(event, agent_events.RouteCardEvent)
                and event.role == "recommended"
            ),
            None,
        )
        if recommended is None or not recommended.itinerary:
            return ToolResult(ok=False, error="segment planning returned no canonical itinerary")

        if index < len(ordered_places) - 1:
            departure_time = _next_segment_departure(
                recommended.itinerary.get("arrival_at"), dwell_minutes
            )
        current_origin = segment_destination

    recommended_events = [
        next(
            event
            for event in result.events
            if isinstance(event, agent_events.RouteCardEvent)
            and event.role == "recommended"
        )
        for result in segment_results
    ]
    first = recommended_events[0]
    last = recommended_events[-1]
    raw_routes = [event.route for event in recommended_events]
    card_id = f"rc_{secrets.token_hex(4)}"
    segments = []
    for index, event in enumerate(recommended_events):
        segments.append(
            {
                "steps": event.route,
                "origin_place": event.origin,
                "destination_place": event.destination,
                **(
                    {"dwell_minutes": dwell_minutes, "dwell_source": dwell_source}
                    if index < len(recommended_events) - 1
                    else {}
                ),
            }
        )

    chained = build_chained_itinerary(
        segments,
        origin=first.origin,
        final_destination=last.destination,
        planning_mode="depart_at" if tool_input.get("departure_time") else "leave_now",
        requested_departure=tool_input.get("departure_time"),
        reasons=[],
        itinerary_id=card_id,
    )
    # Preserve the server-owned segment boundary alongside the existing route
    # step shape. Legacy clients ignore the additive field; modern map/rail
    # consumers use it only to associate geometry with the canonical segment.
    chained_route = [
        {**step, "segment_index": segment_index}
        for segment_index, route in enumerate(raw_routes)
        for step in route
    ]
    lines = _dedupe_lines(raw_routes)
    alerts: list = []
    for event in recommended_events:
        for alert in event.alerts:
            if alert not in alerts:
                alerts.append(alert)
    eta_minutes = _summary_eta_minutes(chained_route, chained["total_duration_seconds"])
    summary = {
        "eta_minutes": eta_minutes,
        "transfers": int(chained["transfer_count"]),
        "lines": lines,
        "reason": "Multi-stop itinerary with server-timed dwell.",
    }
    event = agent_events.RouteCardEvent(
        card_id=card_id,
        turn_id=ctx.turn_id,
        role="recommended",
        origin=first.origin,
        destination=last.destination,
        depart_iso=tool_input.get("departure_time"),
        summary=summary,
        route=chained_route,
        alerts=alerts,
        itinerary=chained,
    )
    return ToolResult(
        ok=True,
        data={
            "candidates": [
                {
                    "card_id": card_id,
                    "lines": lines,
                    "eta_minutes": eta_minutes,
                    "transfers": int(chained["transfer_count"]),
                    "reason": summary["reason"],
                }
            ]
        },
        summary=f"planned {len(recommended_events)} legs as one itinerary",
        events=[event],
        session_route_cards=[
            {
                "card_id": card_id,
                "role": "recommended",
                "lines": lines,
                "eta_minutes": eta_minutes,
            }
        ],
    )


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    waypoints = _normalized_waypoints(tool_input.get("waypoints"))
    if waypoints:
        return await _execute_chained_trip(tool_input, ctx, waypoints)

    origin_raw = str(tool_input.get("origin") or "")
    destination_raw = str(tool_input.get("destination") or "").strip()
    if not destination_raw:
        return ToolResult(ok=False, error="destination is required")

    # Cheap validation before any network call (geocoding, Google Routes).
    excluded = {str(m).strip().upper() for m in (tool_input.get("exclude_modes") or [])}
    allowed_modes = [m for m in _ALL_MODES if m not in excluded]
    if not allowed_modes:
        return ToolResult(ok=False, error="no transit modes left after excluding all of them")

    origin_place, origin_error = await resolve_named_place(
        origin_raw,
        ctx,
        missing_location_message="I need your current location to plan from 'origin' -- share GPS or give me an address instead.",
    )
    if origin_place is None:
        return ToolResult(ok=False, error=origin_error or "could not resolve the origin")

    destination_place, dest_error = await resolve_named_place(
        destination_raw,
        ctx,
        missing_location_message=(
            "I need your current location to plan from 'destination' -- share GPS or give me an address instead."
        ),
    )
    if destination_place is None:
        return ToolResult(ok=False, error=dest_error or "could not find that destination in NYC")

    routing_preference = tool_input.get("routing_preference") or "FEWER_TRANSFERS"
    departure_time = tool_input.get("departure_time") or None
    arrival_by = tool_input.get("arrival_by") or None
    if departure_time and arrival_by:
        return ToolResult(ok=False, error="use either departure_time or arrival_by, not both")
    if arrival_by:
        try:
            departure_time = await _derive_arrive_by_departure(
                origin=origin_place,
                destination=destination_place,
                destination_query=destination_raw,
                arrival_by=str(arrival_by),
                allowed_modes=allowed_modes,
                routing_preference=routing_preference,
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        except directions_service.GoogleRoutesError as exc:
            return ToolResult(ok=False, error=f"could not estimate an arrive-by departure ({exc.code})")

    try:
        response = await _route_with_recovery(
            origin=origin_place,
            destination=destination_place,
            destination_query=destination_raw,
            allowed_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
    except directions_service.GoogleRoutesError as exc:
        print(f"[agent-plan_trip] routing failed code={exc.code}")
        return ToolResult(ok=False, error=f"routing failed ({exc.code})")

    parsed_routes = directions_service.parse_response(response)
    if not parsed_routes:
        return ToolResult(ok=False, error="no transit route found between those points")
    try:
        max_candidates = max(1, int(tool_input.get("max_candidates") or len(parsed_routes)))
    except (TypeError, ValueError):
        max_candidates = len(parsed_routes)
    parsed_routes = parsed_routes[:max_candidates]

    route_ids, bus_route_ids = candidates._collect_route_and_bus_ids(parsed_routes)
    avoid_crowds = bool(tool_input.get("avoid_crowds"))
    event_task = (
        asyncio.create_task(event_crowd.collect_route_event_evidence(parsed_routes, ctx))
        if avoid_crowds
        else None
    )

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

    event_evidence_status: event_crowd.EventEvidenceStatus = "not_required"
    event_impacts: list[dict] = []
    event_failures: list[str] = []
    if event_task is not None:
        try:
            event_evidence_status, event_impacts, event_failures = await event_task
        except Exception as exc:
            print(f"[agent-plan_trip] event enrichment failed: {type(exc).__name__}")
            event_evidence_status = "provider_unavailable"
            event_impacts = []
            event_failures = [type(exc).__name__]

    incidents: list = []
    if tool_input.get("include_incident_scan"):
        incident_context = trip_incidents.build_candidate_stop_context(ctx.gtfs, parsed_routes)
        try:
            incidents = await asyncio.wait_for(
                trip_incidents._scan_route_incidents(incident_context),
                timeout=AGENT_GROK_BUDGET_S,
            )
        except asyncio.TimeoutError:
            print(f"[agent-plan_trip] incident scan timed out ({AGENT_GROK_BUDGET_S:.0f}s)")
            incidents = []

    judge_payload = advisor_context.build_advisor_payload(
        routes=parsed_routes,
        service_alerts=relevant_alerts,
        incidents=incidents,
        stalled_trains=stalled,
        stalled_buses=stalled_buses,
        ticketmaster_event_impacts=event_impacts,
        mode=advisor_context.PlanningMode.INTELLIGENCE,
    )

    try:
        raw_recommendation = await asyncio.wait_for(
            ai_advisor.collect_recommendation(judge_payload), timeout=TRIP_ADVISOR_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        print(f"[agent-plan_trip] advisor timed out ({TRIP_ADVISOR_TIMEOUT_S:.2f}s)")
        raw_recommendation = "[ROUTE:0] Live reasoning timed out; showing the fastest option."
    except Exception as exc:
        print(f"[agent-plan_trip] advisor unavailable type={type(exc).__name__}")
        raw_recommendation = "[ROUTE:0] Live reasoning was unavailable; showing the fastest option."

    chosen_index, candidate_analysis = advisor_context.parse_advisor_selection(
        raw_recommendation,
        len(parsed_routes),
    )

    scored = scoring._score_routes(
        parsed_routes,
        relevant_alerts,
        ticketmaster_event_impacts=event_impacts,
    )
    decision_reason = "advisor_tiebreak"
    selection_log_reason = "advisor_selection"
    if avoid_crowds and event_impacts:
        # Crowd avoidance is a hard evidence contract. The model may explain
        # the evidence, but the actual route choice remains deterministic.
        chosen_index = min(
            scored,
            key=lambda row: (
                row["score"],
                row["event_crowd_penalty"],
                row["total_minutes"],
                row["index"],
            ),
        )["index"]
        decision_reason = "lowest_final_score"
        selection_log_reason = "risk_adjusted_event_score"

    chosen_route = parsed_routes[chosen_index]
    await enrichment._enrich_route(ctx.gtfs, chosen_route)
    first_leg_arrival_context: dict | None = None
    if tool_input.get("include_first_leg_arrivals"):
        first_transit = next(
            (step for step in chosen_route if step.get("type") in {"SUBWAY", "BUS"}),
            None,
        )
        if first_transit:
            departure_coords = first_transit.get("departure_coords") or {}
            try:
                boarding_latitude = float(
                    departure_coords.get("latitude", departure_coords.get("lat"))
                )
                boarding_longitude = float(
                    departure_coords.get("longitude", departure_coords.get("lng"))
                )
                walk_minutes = max(
                    0,
                    math.ceil(
                        geo.distance_meters(
                            origin_place.latitude,
                            origin_place.longitude,
                            boarding_latitude,
                            boarding_longitude,
                        )
                        / 80
                    ),
                )
                from app.services.agent.tools import lookup_arrivals

                arrival_result = await asyncio.wait_for(
                    lookup_arrivals.execute(
                        {
                            "mode": str(first_transit.get("type") or "").lower(),
                            "route_id": scoring._step_route_id(first_transit),
                            "stop_query": first_transit.get("departure_stop"),
                            "direction": first_transit.get("direction"),
                            "user_location": {
                                "latitude": boarding_latitude,
                                "longitude": boarding_longitude,
                            },
                            "walking_minutes": walk_minutes,
                            "limit": 3,
                        },
                        ctx,
                    ),
                    timeout=3.0,
                )
                if arrival_result.ok and isinstance(arrival_result.data, dict):
                    catchability = arrival_result.data.get("catchability")
                    if isinstance(catchability, dict):
                        first_leg_arrival_context = {
                            "route_id": scoring._step_route_id(first_transit),
                            "stop_name": first_transit.get("departure_stop"),
                            "source_status": arrival_result.data.get("source_status"),
                            "walking_minutes": catchability.get("walking_minutes"),
                            "catchable_arrival_minutes": catchability.get(
                                "catchable_arrival_minutes"
                            ),
                            "arrival_minutes": catchability.get("arrival_minutes") or [],
                        }
            except (asyncio.TimeoutError, TypeError, ValueError):
                first_leg_arrival_context = None

    display_candidates = candidates._build_route_candidates(parsed_routes, chosen_index, candidate_analysis, scored)
    score_by_index = scoring._score_by_index(scored)
    selected_score = score_by_index[chosen_index]
    card_ids = [f"rc_{secrets.token_hex(4)}" for _route in parsed_routes]
    selection_decision = build_route_selection_decision(
        selected_index=chosen_index,
        selected_candidate_id=card_ids[chosen_index],
        selected_score=selected_score,
        selection_reason=decision_reason,
        excluded_modes=excluded,
        arrival_by=bool(arrival_by),
        avoid_crowds=avoid_crowds,
        event_evidence_status=event_evidence_status,
        event_impacts=event_impacts,
    )
    print(
        f"[agent-plan_trip] candidates={len(parsed_routes)} selected={chosen_index} "
        f"selected_id={selection_decision['selected_candidate_id']} "
        f"reason={selection_log_reason} event_status={event_evidence_status} "
        f"event_impacts={len(event_impacts)}"
    )
    structured_reasons = build_recommendation_reasons(
        selected_score,
        [
            score
            for index, score in score_by_index.items()
            if index != chosen_index
        ],
    )
    selected_event_impacts = [
        impact for impact in event_impacts if impact.get("route_index") == chosen_index
    ]
    if avoid_crowds and event_impacts:
        structured_reasons.append(
            {
                "code": "lower_event_crowd_exposure",
                "event_count": len(selected_event_impacts),
                "provider_status": event_evidence_status,
            }
        )
    canonical_reason_copy = [
        rendered
        for rendered in (format_recommendation_reason(reason) for reason in structured_reasons)
        if rendered
    ]

    origin_label = _point_label(origin_raw)
    destination_label = _point_label(destination_raw)
    origin_point = origin_place.to_event_point()
    destination_point = destination_place.to_event_point()
    # A named search query is a better display label than the normalized
    # provider coordinate. Never surface an internal latitude/longitude.
    origin_point["label"] = origin_label if origin_place.source != "user" else "Your location"
    destination_point["label"] = destination_label
    planning_mode = "arrive_by" if arrival_by else ("depart_at" if departure_time else "leave_now")

    digest = []
    events = []
    session_cards = []
    for index, route in enumerate(parsed_routes):
        card_id = card_ids[index]
        is_recommended = index == chosen_index
        cand = display_candidates[index]
        lines = cand["score_breakdown"]["transit_lines"]
        reason = cand["recommendation_reason"] if is_recommended else cand["rejection_reason"]
        if is_recommended and canonical_reason_copy:
            reason = canonical_reason_copy[0]
        alert_headlines = [text._safe_text(a.get("header") or "", 80) for a in (relevant_alerts or [])][:3]
        first_step = route[0] if route else {}
        last_step = route[-1] if route else {}

        # One immutable itinerary per card; summary times derive only from it.
        itinerary = build_canonical_itinerary(
            route,
            origin=origin_point,
            destination=destination_point,
            planning_mode=planning_mode,
            requested_departure=departure_time,
            requested_arrival=str(arrival_by) if arrival_by else None,
            reasons=structured_reasons if is_recommended else [],
            itinerary_id=card_id,
        )
        if is_recommended:
            itinerary["selection_decision"] = selection_decision
        eta_minutes = _summary_eta_minutes(route, itinerary["total_duration_seconds"])
        transfers = int(itinerary["transfer_count"])
        walk_minutes = round(int(itinerary["total_walk_seconds"]) / 60)

        digest.append(
            {
                "card_id": card_id,
                "lines": lines,
                "eta_minutes": eta_minutes,
                "transfers": transfers,
                "departs_iso": first_step.get("departure_time_iso"),
                "arrives_iso": last_step.get("arrival_time_iso"),
                "walk_minutes": walk_minutes,
                "alert_headlines": alert_headlines,
                "reason": reason,
                "structured_recommendation_reasons": structured_reasons if is_recommended else [],
                "event_evidence_status": event_evidence_status,
                "event_crowd_penalty": score_by_index[index].get("event_crowd_penalty", 0),
                "event_impacts": [
                    {
                        "event_name": impact.get("title"),
                        "venue_name": impact.get("venue"),
                        "exposure_window": impact.get("exposure_window"),
                        "distance_meters": impact.get("distance_meters"),
                        "risk_score": impact.get("risk_score"),
                    }
                    for impact in event_impacts
                    if impact.get("route_index") == index
                ][:3],
                "first_leg_arrival": first_leg_arrival_context if is_recommended else None,
            }
        )
        summary = {
            "eta_minutes": eta_minutes,
            "transfers": transfers,
            "lines": lines,
            # Retain model prose only as the legacy text alias. Canonical
            # recommendation facts above always come from deterministic
            # candidate scores.
            "reason": reason or (canonical_reason_copy[0] if canonical_reason_copy else None),
            "event_evidence_status": event_evidence_status,
            "first_leg_arrival": first_leg_arrival_context if is_recommended else None,
        }
        events.append(
            agent_events.RouteCardEvent(
                card_id=card_id,
                turn_id=ctx.turn_id,
                role="recommended" if is_recommended else "alternative",
                origin=origin_point,
                destination=destination_point,
                depart_iso=departure_time,
                summary=summary,
                route=route,
                alerts=relevant_alerts,
                itinerary=itinerary,
                selection_decision=selection_decision,
            )
        )
        first_transit = next(
            (step for step in route if step.get("type") in {"SUBWAY", "BUS"}),
            None,
        )
        initial_walk_seconds = 0
        for leg in itinerary.get("legs") or []:
            if str(leg.get("mode") or "").upper() != "WALK":
                break
            initial_walk_seconds += int(leg.get("walk_seconds") or 0)
        session_cards.append(
            {
                "card_id": card_id,
                "role": "recommended" if is_recommended else "alternative",
                "lines": lines,
                "eta_minutes": eta_minutes,
                "destination": destination_point,
                "first_boarding": (
                    {
                        "route_id": scoring._step_route_id(first_transit),
                        "mode": str(first_transit.get("type") or "").lower(),
                        "stop_name": first_transit.get("departure_stop"),
                        "coordinates": first_transit.get("departure_coords"),
                        "direction": first_transit.get("direction"),
                        "walking_minutes": round(initial_walk_seconds / 60),
                    }
                    if first_transit
                    else None
                ),
                "selection_decision": selection_decision,
            }
        )

    recommended_lines = digest[chosen_index]["lines"]
    tool_summary = (
        f"found {len(parsed_routes)} route(s) to {destination_label}; "
        f"recommended {'/'.join(recommended_lines) or 'a walking route'}"
    )
    return ToolResult(
        ok=True,
        data={
            "candidates": digest,
            "event_evidence": {
                "status": event_evidence_status,
                "impact_count": len(event_impacts),
                "provider_failure_count": len(event_failures),
            },
            "selected_route_index": chosen_index,
            "selection_decision": selection_decision,
        },
        summary=tool_summary,
        events=events,
        session_route_cards=session_cards,
    )
