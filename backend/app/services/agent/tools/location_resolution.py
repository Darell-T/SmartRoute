"""Agent-owned location resolution for named and discovered endpoints."""

from __future__ import annotations

import asyncio
import math
import re

from app.services import geography as geo
from app.services.agent import profile as profile_module
from app.services.agent.tools._types import ToolContext
from app.services.mta.static_gtfs.stop_patterns import normalize_station_name
from app.services.trips.location import (
    KnownPlace,
    ResolvedPlace,
    canonical_display_name,
    known_place,
    parse_coordinates,
)


def _normalized_label(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


async def resolve_discovery_place(
    place_id: str,
    ctx: ToolContext,
    *,
    discovery_set_id: str | None = None,
) -> tuple[ResolvedPlace | None, str | None]:
    """Resolve an opaque discovery id to its stored canonical identity.

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
    set_id = (
        str(discovery_set_id or state.get("active_discovery_set_id") or "").strip()
        or None
    )
    place, error = discovery_store.resolve_place_reference(
        session_id=session_id,
        discovery_set_id=set_id,
        place_id=value,
    )
    if error or place is None:
        return None, error or "place reference is unknown"
    return _resolved_discovery_record(place)


def _resolved_discovery_record(place: dict) -> tuple[ResolvedPlace | None, str | None]:
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
            # ``place_id`` is the session-owned opaque identity used by route
            # selection. Provider identity remains available only at this
            # boundary and is never projected to the model.
            place_id=str(place.get("place_id") or "").strip() or None,
            provider_place_id=str(place.get("provider_place_id") or "").strip() or None,
        ),
        None,
    )


def _active_discovery_set_id(ctx: ToolContext) -> str | None:
    """Return the route-authority set from server-owned trip state only."""

    from app.services.agent import trip_state as trip_state_module

    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    return str(state.get("active_discovery_set_id") or "").strip() or None


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
    destination is empty or matches the stored name/address exactly. A stale
    selected place never regains routing authority from a retyped label.
    """

    from app.services.agent import trip_state as trip_state_module

    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    set_id = _active_discovery_set_id(ctx)
    place_id = str(tool_input.get("destination_place_id") or "").strip()
    if place_id:
        return await _destination_from_explicit_place_id(
            place_id, ctx, session, set_id
        )
    from_label = _destination_from_presented_label(merged, ctx, session)
    if from_label is not None:
        return from_label
    return await _destination_from_selected_place(merged, ctx, state, set_id)


async def _destination_from_explicit_place_id(
    place_id: str,
    ctx: ToolContext,
    session: dict,
    set_id: str | None,
) -> tuple[ResolvedPlace | None, str | None, str | None, str | None]:
    from app.services.agent import discovery_store
    from app.services.agent import trip_state as trip_state_module

    presented, presented_error, presented_set_id = (
        discovery_store.resolve_presented_place_reference(
            session=session,
            session_id=str(getattr(ctx, "session_id", None) or "").strip(),
            place_id=place_id,
        )
    )
    if presented_error:
        return (
            None,
            None,
            f"destination place reference is invalid: {presented_error}",
            None,
        )
    if presented is not None and presented_set_id:
        resolved, error = _resolved_discovery_record(presented)
        if resolved is None:
            return (
                None,
                None,
                f"destination place reference is invalid: {error}",
                None,
            )
        trip_state_module.bind_discovery_context(
            session,
            discovery_set_id=presented_set_id,
            selected_place_id=place_id,
        )
        return resolved, place_id, None, presented_set_id
    place, error = await resolve_discovery_place(
        place_id, ctx, discovery_set_id=set_id
    )
    if place is None:
        return (
            None,
            None,
            f"destination place reference is invalid: {error or 'unknown place'}",
            None,
        )
    return place, place_id, None, set_id


def _destination_from_presented_label(
    merged: dict,
    ctx: ToolContext,
    session: dict,
) -> tuple[ResolvedPlace | None, str | None, str | None, str | None] | None:
    from app.services.agent import discovery_store
    from app.services.agent import trip_state as trip_state_module

    destination_label = str(merged.get("destination") or "").strip()
    if not destination_label:
        return None
    presented, presented_error, presented_set_id = (
        discovery_store.resolve_presented_place_reference(
            session=session,
            session_id=str(getattr(ctx, "session_id", None) or "").strip(),
            description=destination_label,
        )
    )
    if presented_error:
        return None, None, presented_error, None
    if presented is None or not presented_set_id:
        return None
    resolved, error = _resolved_discovery_record(presented)
    if resolved is None:
        return None, None, error, None
    resolved_place_id = str(presented.get("place_id") or "").strip()
    trip_state_module.bind_discovery_context(
        session,
        discovery_set_id=presented_set_id,
        selected_place_id=resolved_place_id,
    )
    return resolved, resolved_place_id, None, presented_set_id


async def _destination_from_selected_place(
    merged: dict,
    ctx: ToolContext,
    state: dict,
    set_id: str | None,
) -> tuple[ResolvedPlace | None, str | None, str | None, str | None]:
    selected_place_id = str(state.get("selected_place_id") or "").strip()
    if not selected_place_id:
        return None, None, None, None
    place, _error = await resolve_discovery_place(
        selected_place_id, ctx, discovery_set_id=set_id
    )
    if place is None:
        return _unavailable_selected_place(merged, state, set_id)
    if _selected_place_matches_label(merged, place):
        return place, selected_place_id, None, set_id
    return None, None, None, None


def _unavailable_selected_place(
    merged: dict,
    state: dict,
    set_id: str | None,
) -> tuple[ResolvedPlace | None, str | None, str | None, str | None]:
    destination_text = _normalized_label(merged.get("destination"))
    if not destination_text:
        return (
            None,
            None,
            "the selected place is no longer available; search for it again",
            None,
        )
    # With a non-empty destination label the bounded domain error fires
    # only when the selected place cannot resolve from its own active,
    # session-owned discovery set. A retyped label never regains authority.
    session_set_id = (
        str(state.get("active_discovery_set_id") or "").strip() or None
    )
    if set_id == session_set_id:
        return (
            None,
            None,
            "the selected place is no longer available; search for it again",
            None,
        )
    return None, None, None, None


def _selected_place_matches_label(merged: dict, place: ResolvedPlace) -> bool:
    destination_text = _normalized_label(merged.get("destination"))
    return (
        not destination_text
        or destination_text == _normalized_label(place.name)
        or (place.address and destination_text == _normalized_label(place.address))
    )


async def resolve_waypoint_places(
    waypoints: list[str],
    tool_input: dict,
    ctx: ToolContext,
) -> tuple[dict[str, ResolvedPlace], list[str], str | None, str | None]:
    """Resolve opaque waypoint ids to stored identity; keep plain names as-is.

    Returns (place_by_opaque_id, display_labels, error,
    discovery_set_id_used). Display labels use the stored place name so opaque
    ids never become rider-facing waypoint labels.
    """

    del tool_input
    from app.services.agent import discovery_store

    set_id = _active_discovery_set_id(ctx)
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
            return (
                {},
                [],
                f"waypoint place reference is invalid: {error or 'unknown place'}",
                None,
            )
        resolved[waypoint] = place
        labels.append(place.name)
    used_set_id = set_id if resolved else None
    return resolved, labels, None, used_set_id


_ROUTE_QUALIFIED_STATION_RE = re.compile(
    r"^(?P<station>.+?)\s+(?:on|at)\s+(?:the\s+)?"
    r"(?P<route>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\s+"
    r"(?:train|line)$",
    re.IGNORECASE,
)


def _route_qualified_station(
    raw_value: str,
    ctx: ToolContext,
) -> tuple[ResolvedPlace | None, str | None, bool]:
    match = _ROUTE_QUALIFIED_STATION_RE.fullmatch(str(raw_value or "").strip())
    if match is None:
        return None, None, False
    route_id = match.group("route").upper()
    station_query = normalize_station_name(match.group("station"))
    if not station_query or ctx.gtfs is None:
        return None, "subway station data is unavailable", True
    try:
        stops = ctx.gtfs.get_subway_stops_with_routes({route_id})
    except Exception:  # noqa: BLE001 subway-stop index faults stay unavailable
        return None, "subway station data is unavailable", True
    matches = [
        stop
        for stop in stops or []
        if route_id
        in {str(value).strip().upper() for value in stop.get("route_ids") or []}
        and normalize_station_name(stop.get("stop_name")) == station_query
    ]
    if len(matches) != 1:
        detail = "is ambiguous" if matches else "could not be found"
        return None, f"that {route_id} station {detail}", True
    stop = matches[0]
    try:
        latitude = float(stop["stop_lat"])
        longitude = float(stop["stop_lon"])
    except (KeyError, TypeError, ValueError):
        return None, "subway station coordinates are unavailable", True
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None, "subway station coordinates are unavailable", True
    return (
        ResolvedPlace(
            name=str(stop.get("stop_name") or match.group("station")).strip(),
            latitude=latitude,
            longitude=longitude,
            source="gtfs",
            place_id=str(stop.get("stop_id") or "").strip() or None,
        ),
        None,
        True,
    )


async def resolve_named_point(
    raw_value: str,
    ctx: ToolContext,
    *,
    missing_location_message: str,
) -> tuple[tuple[float, float] | None, str | None]:
    """Resolve a rider point to coordinates without inventing a location."""

    value = (raw_value or "").strip()
    if not value or value.lower() == "user":
        coords = _origin_latlng(ctx)
        if coords is not None:
            return coords, None
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
    station, station_error, station_matched = _route_qualified_station(value, ctx)
    if station_matched:
        if station is None:
            return None, station_error
        return (station.latitude, station.longitude), None
    return await asyncio.to_thread(geo.geocode_address_with_reason, value)


def _origin_latlng(ctx: ToolContext) -> tuple[float, float] | None:
    origin = ctx.origin or {}
    lat, lng = origin.get("lat"), origin.get("lng")
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _place_from_user_origin(
    ctx: ToolContext,
    *,
    missing_location_message: str,
    user_location_label: str,
) -> tuple[ResolvedPlace | None, str | None]:
    coords = _origin_latlng(ctx)
    if coords is None:
        return None, missing_location_message
    return (
        ResolvedPlace(
            name=user_location_label,
            latitude=coords[0],
            longitude=coords[1],
            source="user",
        ),
        None,
    )


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
        return _place_from_user_origin(
            ctx,
            missing_location_message=missing_location_message,
            user_location_label=user_location_label,
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

    station, station_error, station_matched = _route_qualified_station(value, ctx)
    if station_matched:
        return station, station_error

    coords, error = await asyncio.to_thread(geo.geocode_address_with_reason, value)
    if coords is None:
        return None, error
    # A coordinate string is displayed only when the rider explicitly supplied
    # coordinates. All named searches preserve their meaningful label.
    explicit_coordinates = parse_coordinates(value) is not None
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
    "resolve_destination_reference",
    "resolve_discovery_place",
    "resolve_named_place",
    "resolve_named_point",
    "resolve_waypoint_places",
)
