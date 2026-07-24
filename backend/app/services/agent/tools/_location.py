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
import dataclasses
import re

from app.services.agent.tools._types import ToolContext
from app.utils import geo


@dataclasses.dataclass(frozen=True)
class ResolvedPlace:
    """Stable place identity for rider-facing surfaces and provider calls."""

    name: str
    latitude: float
    longitude: float
    source: str
    address: str | None = None
    place_id: str | None = None

    def to_event_point(self) -> dict:
        return {
            "label": self.name,
            "name": self.name,
            "address": self.address,
            "place_id": self.place_id,
            "lat": self.latitude,
            "lng": self.longitude,
            "source": self.source,
        }


_COORDINATE_RE = re.compile(r"^-?\d+\.?\d*,\s*-?\d+\.?\d*$")


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


async def resolve_named_place(
    raw_value: str,
    ctx: ToolContext,
    *,
    missing_location_message: str,
    user_location_label: str = "Your location",
) -> tuple[ResolvedPlace | None, str | None]:
    """Resolve a provider point without sacrificing its rider-facing identity."""
    value = (raw_value or "").strip()
    if not value or value.lower() == "user":
        origin = ctx.origin or {}
        lat, lng = origin.get("lat"), origin.get("lng")
        if lat is None or lng is None:
            return None, missing_location_message
        return (
            ResolvedPlace(
                name=user_location_label,
                latitude=float(lat),
                longitude=float(lng),
                source="user",
            ),
            None,
        )

    coords, error = await asyncio.to_thread(geo.geocode_address_with_reason, value)
    if coords is None:
        return None, error
    # A coordinate string is displayed only when the rider explicitly supplied
    # coordinates. All named searches preserve their meaningful label.
    explicit_coordinates = bool(_COORDINATE_RE.match(value))
    return (
        ResolvedPlace(
            name=value if explicit_coordinates else value,
            latitude=float(coords[0]),
            longitude=float(coords[1]),
            source="user" if explicit_coordinates else "geocoder",
            address=None if explicit_coordinates else value,
        ),
        None,
    )
