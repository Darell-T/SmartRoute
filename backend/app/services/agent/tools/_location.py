"""Shared "resolve a rider-named point to coordinates" helper.

Several tools accept a location string that is `'user'` (the rider's GPS
from `ToolContext.origin`), empty (also treated as `'user'`), or an NYC
address/place name to geocode via `utils/geo.py` (which itself fast-paths
`'lat,lng'` strings and enforces NYC bounds). `plan_trip` (origin and
destination) and `transit_snapshot` (`near`) use this exact resolution with
only their "no GPS available" message differing; `poi_search` builds on it
too but layers its own extra cases (an empty `near` biases to all of NYC
instead of asking for GPS, and a raw coordinate string is accepted without
the NYC-bounds check `geo.py` would apply) that don't belong here.
"""

from __future__ import annotations

import asyncio

from app.services.agent.tools._types import ToolContext
from app.utils import geo


async def resolve_named_point(
    raw_value: str, ctx: ToolContext, *, missing_location_message: str
) -> tuple[tuple[float, float] | None, str | None]:
    """'' or 'user' -> `ctx.origin`'s GPS coords, or `missing_location_message`
    if the rider hasn't shared one. Anything else is geocoded."""
    value = (raw_value or "").strip()
    if not value or value.lower() == "user":
        origin = ctx.origin or {}
        lat, lng = origin.get("lat"), origin.get("lng")
        if lat is not None and lng is not None:
            return (float(lat), float(lng)), None
        return None, missing_location_message
    return await asyncio.to_thread(geo.geocode_address_with_reason, value)
