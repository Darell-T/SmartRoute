"""Public dispatcher for deterministic subway and bus arrival lookups."""

from __future__ import annotations

import re
import time

from app.services.mta import realtime as mta_realtime
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.transit.lookup_arrivals_bus import execute as _execute_bus
from app.services.agent.tools.transit.lookup_arrivals_common import (
    ARRIVAL_LIMIT_DEFAULT,
    ARRIVAL_LIMIT_MAX,
    BOARDING_BUFFER_MINUTES as _BOARDING_BUFFER_MINUTES,
    FEED_STALE_AFTER_S as _FEED_STALE_AFTER_S,
    _active_boarding,
    assess_catchability as _assess_catchability,
    canonical_station_query as _canonical_station_query,
)
from app.services.agent.tools.transit.lookup_arrivals_subway import execute as _execute_subway

assess_catchability = _assess_catchability
BOARDING_BUFFER_MINUTES = _BOARDING_BUFFER_MINUTES
FEED_STALE_AFTER_S = _FEED_STALE_AFTER_S
canonical_station_query = _canonical_station_query

LOOKUP_ARRIVALS_SCHEMA = {
    "name": "lookup_arrivals",
    "description": (
        "Use when the rider asks when a specific train or bus is arriving, "
        "whether they can catch an upcoming vehicle, or when live arrival "
        "timing materially supports a broader question. This does not prove "
        "whether a line has delays; use transit_snapshot for service status."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "route_id": {
                "type": "string",
                "description": "MTA route, e.g. B, Q, F, M15, B35.",
            },
            "stop_query": {
                "type": "string",
                "description": "Explicit station or bus-stop name.",
            },
            "direction": {
                "type": "string",
                "description": "Explicit rider direction or destination. Omit to show both directions.",
            },
        },
        "required": ["route_id"],
        "additionalProperties": False,
    },
}


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    route_id = str(tool_input.get("route_id") or "").strip().upper()
    if not route_id:
        active = _active_boarding(ctx, "")
        route_id = str((active or {}).get("route_id") or "").strip().upper()
    if not route_id:
        return ToolResult(ok=False, error="route_id is required")
    try:
        limit = min(
            ARRIVAL_LIMIT_MAX,
            max(1, int(tool_input.get("limit") or ARRIVAL_LIMIT_DEFAULT)),
        )
    except (TypeError, ValueError):
        limit = ARRIVAL_LIMIT_DEFAULT
    requested_mode = str(tool_input.get("mode") or "").strip().lower()
    mode = requested_mode or (
        "bus" if re.match(r"^[A-Z]+\d+", route_id) else "subway"
    )
    if mode == "bus":
        return await _execute_bus(
            tool_input,
            ctx,
            route_id,
            limit,
            feed_api=mta_realtime,
            clock=time,
        )
    return await _execute_subway(
        tool_input,
        ctx,
        route_id,
        limit,
        feed_api=mta_realtime,
        clock=time,
    )
