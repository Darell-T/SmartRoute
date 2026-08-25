"""Neutral location value objects and model-free named-place resolution.

The route planner and the agent both need the same verified endpoint shape.  The
agent still owns discovery and profile composition; this module only handles
the values and the bounded fallback resolver used when no session-owned place
reference is involved.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import re
from typing import Any

from app.services import geography as geo


@dataclasses.dataclass(frozen=True)
class KnownPlace:
    name: str
    latitude: float
    longitude: float
    address: str | None = None
    place_id: str | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedPlace:
    """Stable endpoint identity used by providers and canonical itineraries."""

    name: str
    latitude: float
    longitude: float
    source: str
    address: str | None = None
    place_id: str | None = None
    provider_place_id: str | None = None

    def to_event_point(self) -> dict[str, Any]:
        return {
            "label": self.name,
            "name": self.name,
            "address": self.address,
            "place_id": self.place_id,
            "lat": self.latitude,
            "lng": self.longitude,
            "source": self.source,
        }


_KNOWN_PLACES: dict[str, KnownPlace] = {}


def _register(place: KnownPlace, *aliases: str) -> None:
    for alias in aliases:
        _KNOWN_PLACES[" ".join(alias.casefold().split())] = place


_register(
    KnownPlace(
        "John F. Kennedy International Airport",
        40.6413,
        -73.7781,
        "Queens, NY 11430",
    ),
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
    KnownPlace(
        "Newark Liberty International Airport",
        40.6895,
        -74.1745,
        "Newark, NJ 07114",
    ),
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
    KnownPlace(
        "Grand Central Terminal",
        40.7527,
        -73.9772,
        "89 E 42nd St, New York, NY 10017",
    ),
    "Grand Central",
    "Grand Central Terminal",
)
_register(
    KnownPlace("Atlantic Terminal", 40.6845, -73.9775, "Brooklyn, NY 11217"),
    "Atlantic Terminal",
)
_register(
    KnownPlace(
        "Barclays Center",
        40.6826,
        -73.9754,
        "620 Atlantic Ave, Brooklyn, NY 11217",
    ),
    "Barclays Center",
)
_register(
    KnownPlace(
        "Madison Square Garden",
        40.7505,
        -73.9934,
        "4 Pennsylvania Plaza, New York, NY 10001",
    ),
    "Madison Square Garden",
    "MSG",
)
_register(
    KnownPlace("Yankee Stadium", 40.8296, -73.9262, "1 E 161 St, Bronx, NY 10451"),
    "Yankee Stadium",
)
_register(
    KnownPlace("Citi Field", 40.7571, -73.8458, "41 Seaver Way, New York, NY 11368"),
    "Citi Field",
)


_COORDINATE_RE = re.compile(r"^-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?$")


def known_place(raw_value: object) -> KnownPlace | None:
    return _KNOWN_PLACES.get(" ".join(str(raw_value or "").casefold().split()))


def canonical_display_name(raw_value: object) -> str:
    place = known_place(raw_value)
    return place.name if place else str(raw_value or "").strip()


def parse_coordinates(value: object) -> tuple[float, float] | None:
    """Parse a finite NYC coordinate pair without invoking geocoding."""

    latitude: object
    longitude: object
    if isinstance(value, dict):
        latitude = value.get("latitude", value.get("lat"))
        longitude = value.get("longitude", value.get("lng", value.get("lon")))
    else:
        raw = str(value or "").strip()
        if not _COORDINATE_RE.fullmatch(raw):
            return None
        latitude, longitude = (part.strip() for part in raw.split(",", 1))
    try:
        parsed_latitude = float(latitude)
        parsed_longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(parsed_latitude) and math.isfinite(parsed_longitude)):
        return None
    bounds = geo.NYC_BOUNDS
    if not (
        bounds["min_lat"] <= parsed_latitude <= bounds["max_lat"]
        and bounds["min_lon"] <= parsed_longitude <= bounds["max_lon"]
    ):
        return None
    return parsed_latitude, parsed_longitude


async def resolve_named_place(
    raw_value: str,
    ctx: Any,
    *,
    missing_location_message: str,
    user_location_label: str = "Your location",
) -> tuple[ResolvedPlace | None, str | None]:
    """Resolve a free-text endpoint without session or model state.

    Discovery references, saved places, and route-qualified station policy are
    intentionally supplied by the agent adapter.  Direct REST planning needs
    only rider coordinates, known-place aliases, and the bounded geocoder.
    """

    value = str(raw_value or "").strip()
    if not value or value.casefold() == "user":
        origin = getattr(ctx, "origin", None) or {}
        latitude, longitude = origin.get("lat"), origin.get("lng")
        if latitude is None or longitude is None:
            return None, missing_location_message
        return (
            ResolvedPlace(
                name=user_location_label,
                latitude=float(latitude),
                longitude=float(longitude),
                source="user",
            ),
            None,
        )

    alias = known_place(value)
    if alias is not None:
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
    explicit_coordinates = bool(_COORDINATE_RE.fullmatch(value))
    return (
        ResolvedPlace(
            name=value,
            latitude=float(coords[0]),
            longitude=float(coords[1]),
            source="user" if explicit_coordinates else "geocoder",
            address=None if explicit_coordinates else value,
        ),
        None,
    )


__all__ = (
    "KnownPlace",
    "ResolvedPlace",
    "canonical_display_name",
    "known_place",
    "parse_coordinates",
    "resolve_named_place",
)
