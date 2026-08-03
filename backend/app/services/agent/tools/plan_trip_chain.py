"""Multi-stop itinerary assembly for the plan-trip tool."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.services.agent import events as agent_events
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.trips.itinerary import build_chained_itinerary


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


async def execute_chained_trip(
    tool_input: dict,
    ctx: ToolContext,
    waypoints: list[str],
    *,
    execute_leg: Callable[[dict, ToolContext], Awaitable[ToolResult]],
    summary_eta_minutes: Callable[[list[dict], int], int],
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
            if key
            not in {
                "waypoints",
                "waypoint_dwell_minutes",
                "destination",
                "origin",
                "departure_time",
            }
        }
        leg_input.update(
            {
                "origin": current_origin,
                "destination": segment_destination,
            }
        )
        if departure_time:
            leg_input["departure_time"] = departure_time

        result = await execute_leg(leg_input, ctx)
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
    eta_minutes = summary_eta_minutes(chained_route, chained["total_duration_seconds"])
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
        timings={
            name: sum(
                max(0.0, float(result.timings.get(name) or 0.0))
                for result in segment_results
            )
            for name in {
                key
                for result in segment_results
                for key in result.timings
            }
        },
    )
