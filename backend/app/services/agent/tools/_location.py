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
class KnownPlace:
    name: str
    latitude: float
    longitude: float
    address: str | None = None
    place_id: str | None = None


_KNOWN_PLACES: dict[str, KnownPlace] = {}


def _register(place: KnownPlace, *aliases: str) -> None:
    for alias in aliases:
        _KNOWN_PLACES[" ".join(alias.casefold().split())] = place


_register(
    KnownPlace("John F. Kennedy International Airport", 40.6413, -73.7781, "Queens, NY 11430"),
    "JFK",
    "JFK Airport",
    "Kennedy Airport",
)
_register(
    KnownPlace("LaGuardia Airport", 40.7769, -73.8740, "Queens, NY 11371"),
    "LGA",
    "LaGuardia",
    "LaGuardia Airport",
)
_register(
    KnownPlace("Newark Liberty International Airport", 40.6895, -74.1745, "Newark, NJ 07114"),
    "EWR",
    "Newark Airport",
    "Newark Liberty Airport",
)
_register(
    KnownPlace("Penn Station", 40.7506, -73.9935, "New York, NY 10119"),
    "Penn Station",
    "NY Penn Station",
)
_register(
    KnownPlace("Grand Central Terminal", 40.7527, -73.9772, "89 E 42nd St, New York, NY 10017"),
    "Grand Central",
    "Grand Central Terminal",
)
_register(
    KnownPlace("Atlantic Terminal", 40.6845, -73.9775, "Brooklyn, NY 11217"),
    "Atlantic Terminal",
)
_register(
    KnownPlace("Barclays Center", 40.6826, -73.9754, "620 Atlantic Ave, Brooklyn, NY 11217"),
    "Barclays Center",
)
_register(
    KnownPlace("Madison Square Garden", 40.7505, -73.9934, "4 Pennsylvania Plaza, New York, NY 10001"),
    "Madison Square Garden",
    "MSG",
)
_register(
    KnownPlace("Yankee Stadium", 40.8296, -73.9262, "1 E 161 St, Bronx, NY 10451"),
    "Yankee Stadium",
)
_register(
    KnownPlace("Citi Field", 40.7571, -73.8458, "41 Seaver Way, Queens, NY 11368"),
    "Citi Field",
)


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


def known_place(raw_value: str) -> KnownPlace | None:
    return _KNOWN_PLACES.get(" ".join(str(raw_value or "").casefold().split()))


def canonical_display_name(raw_value: str) -> str:
    place = known_place(raw_value)
    return place.name if place else str(raw_value or "").strip()


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
    alias = known_place(value)
    if alias:
        return (alias.latitude, alias.longitude), None
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

    alias = known_place(value)
    if alias:
        return (
            ResolvedPlace(
                name=alias.name,
                latitude=alias.latitude,
                longitude=alias.longitude,
                source="fallback",
                address=alias.address,
                place_id=alias.place_id,
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
