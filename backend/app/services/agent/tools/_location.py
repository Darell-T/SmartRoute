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
import math
import re
from typing import Any

from app.services.agent.tools._types import ToolContext
from app.services.agent import profile as profile_module
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


async def resolve_discovery_place(
    place_id: str,
    ctx: ToolContext,
    *,
    discovery_set_id: str | None = None,
) -> tuple[ResolvedPlace | None, str | None]:
    """Resolve an opaque discovery place id to its stored canonical identity.

    Coordinates and the provider place id come from the server-owned discovery
    record, never from a model-retyped label. Unknown, expired, or
    cross-session references fail safely.
    """

    from app.services.agent import discovery_store
    from app.services.agent import trip_state as trip_state_module

    value = str(place_id or "").strip()
    if not discovery_store.is_opaque_place_id(value):
        return None, "place reference is incomplete"
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    if not session_id:
        return None, "session is required to resolve a place reference"
    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    set_id = str(
        discovery_set_id or state.get("active_discovery_set_id") or ""
    ).strip() or None
    place, error = discovery_store.resolve_place_reference(
        session_id=session_id,
        discovery_set_id=set_id,
        place_id=value,
    )
    if error or place is None:
        return None, error or "place reference is unknown"
    try:
        latitude = float(place["latitude"])
        longitude = float(place["longitude"])
    except (TypeError, KeyError, ValueError):
        return None, "stored place coordinates are unavailable"
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None, "stored place coordinates are invalid"
    name = str(place.get("name") or "").strip()
    return (
        ResolvedPlace(
            name=name or "Selected place",
            latitude=latitude,
            longitude=longitude,
            source="discovery",
            address=str(place.get("address") or "").strip() or None,
            place_id=str(place.get("provider_place_id") or "").strip() or None,
        ),
        None,
    )


def _normalized_label(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _active_discovery_set_id(tool_input: dict, ctx: ToolContext) -> str | None:
    from app.services.agent import trip_state as trip_state_module

    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    return str(
        tool_input.get("discovery_set_id") or state.get("active_discovery_set_id") or ""
    ).strip() or None


async def resolve_destination_reference(
    tool_input: dict,
    merged: dict,
    ctx: ToolContext,
) -> tuple[ResolvedPlace | None, str | None, str | None, str | None]:
    """Resolve a server-owned destination place reference, if one is active.

    Returns (resolved_place, opaque_place_id, error, discovery_set_id_used).
    An explicit opaque destination_place_id in the tool input always wins
    over any free-text destination, which is neither geocoded nor substituted.
    Otherwise a session-selected place is applied only when the free-text
    destination is empty or matches the stored name/address exactly -- a
    server-side fallback for model drift; canonical routing still resolves
    through the opaque id. ``discovery_set_id_used`` is the set that actually
    participated in a successful canonical resolution (None otherwise) so
    callers can bind it without ever rebinding an unused set string. When a
    session-selected place exists but can no longer be resolved from its
    active session-owned discovery set, a bounded domain error is returned
    even when a non-empty destination label is also supplied -- a retyped
    label never regains routing authority from a stale selection.
    """

    from app.services.agent import trip_state as trip_state_module

    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    set_id = _active_discovery_set_id(tool_input, ctx)
    place_id = str(tool_input.get("destination_place_id") or "").strip()
    if place_id:
        place, error = await resolve_discovery_place(
            place_id, ctx, discovery_set_id=set_id
        )
        if place is None:
            return None, None, (
                f"destination place reference is invalid: {error or 'unknown place'}"
            ), None
        return place, place_id, None, set_id
    selected_place_id = str(state.get("selected_place_id") or "").strip()
    if not selected_place_id:
        return None, None, None, None
    place, error = await resolve_discovery_place(
        selected_place_id, ctx, discovery_set_id=set_id
    )
    if place is None:
        destination_text = _normalized_label(merged.get("destination"))
        if not destination_text:
            return None, None, (
                "the selected place is no longer available; search for it again"
            ), None
        # Finding B narrow boundary: with a non-empty destination label the
        # bounded domain error fires only when the selected place cannot
        # resolve from its OWN active session-owned discovery set -- a
        # retyped label must never regain routing authority from a stale
        # selection. An explicit tool-input set that merely does not contain
        # the selected place is the documented "unused explicit set"
        # contract: it never participates, the free-text destination
        # proceeds, and no discovery context rebinds. A genuinely new
        # destination is a new turn whose new-trip reset already cleared the
        # obsolete selected-place context.
        session_set_id = (
            str(state.get("active_discovery_set_id") or "").strip() or None
        )
        if set_id == session_set_id:
            return None, None, (
                "the selected place is no longer available; search for it again"
            ), None
        return None, None, None, None
    destination_text = _normalized_label(merged.get("destination"))
    if not destination_text or destination_text == _normalized_label(place.name) or (
        place.address and destination_text == _normalized_label(place.address)
    ):
        return place, selected_place_id, None, set_id
    return None, None, None, None


async def resolve_waypoint_places(
    waypoints: list[str],
    tool_input: dict,
    ctx: ToolContext,
) -> tuple[dict[str, ResolvedPlace], list[str], str | None, str | None]:
    """Resolve opaque waypoint ids to stored identity; keep plain names as-is.

    Returns (place_by_opaque_id, display_labels, error,
    discovery_set_id_used). Display labels use the stored place name so opaque
    ids never become rider-facing waypoint labels; the id-keyed map keeps
    provider lookups on stored coordinates. ``discovery_set_id_used`` is the
    set that actually resolved at least one opaque waypoint (None when no
    opaque waypoint was resolved).
    """

    from app.services.agent import discovery_store

    set_id = _active_discovery_set_id(tool_input, ctx)
    resolved: dict[str, ResolvedPlace] = {}
    labels: list[str] = []
    for waypoint in waypoints:
        if not discovery_store.is_opaque_place_id(waypoint):
            labels.append(waypoint)
            continue
        place, error = await resolve_discovery_place(
            waypoint, ctx, discovery_set_id=set_id
        )
        if place is None:
            return {}, [], (
                f"waypoint place reference is invalid: {error or 'unknown place'}"
            ), None
        resolved[waypoint] = place
        labels.append(place.name)
    used_set_id = set_id if resolved else None
    return resolved, labels, None, used_set_id


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
    saved, saved_error = profile_module.resolve_profile_place(ctx.session, value)
    if saved_error:
        return None, saved_error
    if saved is not None:
        return (float(saved["latitude"]), float(saved["longitude"])), None
    if _normalized_label(value) in {"home", "work"}:
        return None, f"saved {_normalized_label(value).title()} is unavailable"
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

    from app.services.agent import discovery_store

    if discovery_store.is_opaque_place_id(value):
        place, error = await resolve_discovery_place(value, ctx)
        if place is None:
            return None, error or "unknown place reference"
        return place, None

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
    saved, saved_error = profile_module.resolve_profile_place(ctx.session, value)
    if saved_error:
        return None, saved_error
    if saved is not None:
        return (
            ResolvedPlace(
                name=str(saved["label"]),
                latitude=float(saved["latitude"]),
                longitude=float(saved["longitude"]),
                source="profile",
                address=saved.get("address"),
                place_id=saved.get("place_id"),
            ),
            None,
        )
    if _normalized_label(value) in {"home", "work"}:
        return None, f"saved {_normalized_label(value).title()} is unavailable"

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


__all__ = (
    "KnownPlace",
    "ResolvedPlace",
    "canonical_display_name",
    "known_place",
    "resolve_discovery_place",
    "resolve_destination_reference",
    "resolve_named_place",
    "resolve_named_point",
    "resolve_waypoint_places",
)
