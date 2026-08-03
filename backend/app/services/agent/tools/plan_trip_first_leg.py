"""Optional first-leg live-arrival enrichment for a canonical itinerary."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from app.services.agent.tools._location import ResolvedPlace
from app.services.agent.tools._types import ToolContext


async def first_leg_arrival_context(
    tool_input: dict,
    ctx: ToolContext,
    origin_place: ResolvedPlace,
    chosen_route: list[dict],
    dependencies: Any,
) -> dict | None:
    if not tool_input.get("include_first_leg_arrivals"):
        return None
    first_transit = next(
        (step for step in chosen_route if step.get("type") in {"SUBWAY", "BUS"}),
        None,
    )
    if not first_transit:
        return None
    departure_coords = first_transit.get("departure_coords") or {}
    try:
        latitude = float(departure_coords.get("latitude", departure_coords.get("lat")))
        longitude = float(departure_coords.get("longitude", departure_coords.get("lng")))
        walking_minutes = max(
            0,
            math.ceil(
                dependencies.geo.distance_meters(
                    origin_place.latitude,
                    origin_place.longitude,
                    latitude,
                    longitude,
                )
                / 80
            ),
        )
        from app.services.agent.tools import lookup_arrivals

        result = await asyncio.wait_for(
            lookup_arrivals.execute(
                {
                    "mode": str(first_transit.get("type") or "").lower(),
                    "route_id": dependencies.scoring._step_route_id(first_transit),
                    "stop_query": first_transit.get("departure_stop"),
                    "direction": first_transit.get("direction"),
                    "user_location": {
                        "latitude": latitude,
                        "longitude": longitude,
                    },
                    "walking_minutes": walking_minutes,
                    "limit": 3,
                },
                ctx,
            ),
            timeout=3.0,
        )
        if (
            result.ok
            and isinstance(result.data, dict)
            and isinstance(result.data.get("catchability"), dict)
        ):
            catchability = result.data["catchability"]
            return {
                "route_id": dependencies.scoring._step_route_id(first_transit),
                "stop_name": first_transit.get("departure_stop"),
                "source_status": result.data.get("source_status"),
                "walking_minutes": catchability.get("walking_minutes"),
                "catchable_arrival_minutes": catchability.get(
                    "catchable_arrival_minutes"
                ),
                "arrival_minutes": catchability.get("arrival_minutes") or [],
            }
    except (asyncio.TimeoutError, TypeError, ValueError):
        pass
    return None
