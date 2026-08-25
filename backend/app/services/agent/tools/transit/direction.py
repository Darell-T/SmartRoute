"""Bounded semantic direction handling for transit evidence.

The conversational model owns the meaning of a rider's direction field.  This
module only canonicalizes the small set of provider direction values that are
safe to recognize and matches destination/headsign text against authoritative
route or trip context.  It deliberately does not classify arbitrary rider
prose by phrase-family matching.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


_DIRECTION_ALIASES = {
    "uptown": "uptown",
    "northbound": "uptown",
    "downtown": "downtown",
    "southbound": "downtown",
}

_CONTEXT_VALUE_KEYS = (
    "direction_id",
    "headsign",
    "direction",
    "direction_label",
    "label",
    "id",
    "destination",
    "destination_stop",
    "destination_stop_name",
    "terminal_stop",
    "terminal_stop_name",
    "board",
    "alight",
    "departure_stop",
    "arrival_stop",
)
_HEADSIGN_CONTEXT_KEYS = frozenset(
    {
        "headsign",
        "direction",
        "direction_label",
        "destination_stop",
        "destination_stop_name",
        "terminal_stop",
        "terminal_stop_name",
    }
)


@dataclass(frozen=True, slots=True)
class DirectionResolution:
    """Model-declared direction resolved against server-owned context."""

    requested: str | None
    resolved: str | None
    authoritative: bool
    matched_value: str | None = None


def normalize_direction(value: object) -> str | None:
    """Return a canonical direction for an exact semantic direction value.

    Numeric GTFS direction ids are deliberately excluded.  ``0`` and ``1``
    mean opposite things across feeds; they are safe only after a route/trip
    context supplies an explicit semantic mapping.
    """

    return _DIRECTION_ALIASES.get(normalize_direction_text(value))


def normalize_direction_text(value: object) -> str:
    """Normalize a destination/headsign for exact context comparison."""

    raw = "" if value is None else str(value)
    return " ".join(
        raw.replace("–", "-").replace("—", "-").replace("_", " ").split()
    ).casefold()


def stop_id_direction(value: object) -> str | None:
    """Resolve the authoritative N/S platform suffix used by subway stops."""

    text = str(value or "").strip().upper()
    if text.endswith("N"):
        return "uptown"
    if text.endswith("S"):
        return "downtown"
    return None


def resolve_direction(
    value: object,
    contexts: Iterable[Mapping[str, object]] = (),
) -> DirectionResolution:
    """Resolve a model direction using exact authoritative context values.

    Canonical ``uptown``/``downtown`` declarations are retained even when a
    source has no direction metadata, but they are marked non-authoritative in
    that case.  Destination/headsign declarations only resolve when the exact
    normalized value exists in a route, stop, or accepted-trip context.
    """

    requested_text = normalize_direction_text(value)
    if not requested_text:
        return DirectionResolution(None, None, False)

    canonical = normalize_direction(requested_text)
    context_rows = [row for row in contexts if isinstance(row, Mapping)]
    if canonical:
        authoritative = any(
            _context_direction(row) == canonical for row in context_rows
        )
        return DirectionResolution(
            requested=canonical,
            resolved=canonical,
            authoritative=authoritative,
        )

    for row in context_rows:
        for key in _CONTEXT_VALUE_KEYS:
            candidate = row.get(key)
            if normalize_direction_text(candidate) != requested_text:
                continue
            resolved = _context_direction(row)
            if resolved:
                return DirectionResolution(
                    requested=requested_text,
                    resolved=resolved,
                    authoritative=True,
                    matched_value=normalize_direction_text(candidate),
                )
            if key in _HEADSIGN_CONTEXT_KEYS:
                return DirectionResolution(
                    requested=requested_text,
                    resolved=requested_text,
                    authoritative=True,
                    matched_value=requested_text,
                )

    return DirectionResolution(requested_text, None, False)


def resolve_model_direction(
    value: object,
    route_ids: Iterable[str],
    *,
    session: Mapping[str, object] | None = None,
    gtfs: object = None,
) -> DirectionResolution:
    """Resolve a model field against accepted-trip and static route context."""

    return resolve_direction(
        value, _route_contexts(route_ids, session=session, gtfs=gtfs)
    )


def direction_clarification(
    route_ids: Iterable[str], requested: object
) -> dict[str, object]:
    """Return a bounded structured clarification for an unresolved direction."""

    routes = ", ".join(
        str(route).strip().upper() for route in route_ids if str(route).strip()
    )
    question = f"Which direction should I check for the {routes}—uptown or downtown?"
    return {
        "status": "clarification_required",
        "clarification": {
            "kind": "transit_direction",
            "requested": normalize_direction_text(requested),
            "question": question,
        },
    }


def direction_matches(left: object, right: object) -> bool:
    """Compare canonical direction values or exact normalized labels."""

    left_direction = normalize_direction(left)
    right_direction = normalize_direction(right)
    if left_direction and right_direction:
        return left_direction == right_direction
    return normalize_direction_text(left) == normalize_direction_text(right)


def _route_contexts(
    route_ids: Iterable[str],
    *,
    session: Mapping[str, object] | None,
    gtfs: object,
) -> list[dict[str, object]]:
    contexts: list[dict[str, object]] = []
    active_trip = session.get("active_trip") if isinstance(session, Mapping) else None
    boarding = (
        active_trip.get("first_boarding") if isinstance(active_trip, Mapping) else None
    )
    routes = {str(route).strip().upper() for route in route_ids if str(route).strip()}
    if not routes:
        return contexts
    if isinstance(boarding, Mapping):
        boarding_route = str(boarding.get("route_id") or "").strip().upper()
        if not routes or boarding_route in routes:
            contexts.append(dict(boarding))

    itinerary = _active_canonical_itinerary(active_trip)
    if itinerary is not None:
        contexts.extend(_itinerary_contexts(itinerary, routes))

    pattern_index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    route_patterns = getattr(pattern_index, "route_patterns", {})
    stops = getattr(pattern_index, "stops", {})
    if not isinstance(route_patterns, Mapping) or not isinstance(stops, Mapping):
        return contexts
    for route_id in sorted(routes)[:3]:
        patterns = route_patterns.get(route_id, [])
        for pattern in patterns[:8] if isinstance(patterns, list) else []:
            if not isinstance(pattern, Mapping):
                continue
            stop_ids = pattern.get("stop_ids")
            terminal_id = (
                stop_ids[-1] if isinstance(stop_ids, list) and stop_ids else None
            )
            terminal = stops.get(terminal_id) if terminal_id else None
            contexts.append(
                {
                    "route_id": route_id,
                    "direction_id": pattern.get("direction_id"),
                    "direction": pattern.get("direction"),
                    "direction_label": pattern.get("direction_label"),
                    "canonical_direction": pattern.get("canonical_direction"),
                    "semantic_direction": pattern.get("semantic_direction"),
                    "direction_id_map": pattern.get("direction_id_map"),
                    "headsign": pattern.get("trip_headsign") or pattern.get("headsign"),
                    "label": pattern.get("label"),
                    "stop_ids": stop_ids,
                    "origin_coords": _stop_coords(stops.get(stop_ids[0]))
                    if isinstance(stop_ids, list) and stop_ids
                    else None,
                    "destination_coords": _stop_coords(stops.get(stop_ids[-1]))
                    if isinstance(stop_ids, list) and stop_ids
                    else None,
                    "destination_stop_name": (
                        terminal.get("name") if isinstance(terminal, Mapping) else None
                    ),
                }
            )
    return contexts


def _active_canonical_itinerary(active_trip: object) -> Mapping[str, object] | None:
    if not isinstance(active_trip, Mapping):
        return None
    for key in ("canonical_itinerary", "itinerary"):
        value = active_trip.get(key)
        if isinstance(value, Mapping) and isinstance(value.get("legs"), list):
            return value
    return None


def _itinerary_contexts(
    itinerary: Mapping[str, object],
    routes: set[str],
) -> list[dict[str, object]]:
    contexts: list[dict[str, object]] = []
    for raw_leg in itinerary.get("legs") or []:
        if not isinstance(raw_leg, Mapping):
            continue
        route_id = (
            str(raw_leg.get("service_id") or raw_leg.get("route_id") or "")
            .strip()
            .upper()
        )
        if routes and route_id not in routes:
            continue
        mode = str(raw_leg.get("mode") or raw_leg.get("type") or "").strip().upper()
        row = dict(raw_leg)
        row.update({"route_id": route_id, "mode": mode})
        contexts.append(row)
    return contexts


def _context_direction(row: Mapping[str, object]) -> str | None:
    """Read semantic context without assigning meaning to a bare GTFS id."""

    for key in (
        "canonical_direction",
        "semantic_direction",
        "direction",
        "direction_label",
        "label",
        "headsign",
        "id",
    ):
        direction = normalize_direction(row.get(key))
        if direction:
            return direction
    raw_direction_id = row.get("direction_id")
    direction_id = normalize_direction_text(raw_direction_id)
    mapping = row.get("direction_id_map")
    if direction_id and isinstance(mapping, Mapping):
        # A route/pattern producer may explicitly publish this mapping; absent
        # that field, numeric ids stay opaque for buses and nonstandard routes.
        return normalize_direction(
            mapping.get(direction_id, mapping.get(raw_direction_id))
        )
    if _has_ordered_endpoints(row) and isinstance(row.get("stop_ids"), list):
        return _geometry_direction(row)
    return None


def _has_ordered_endpoints(row: Mapping[str, object]) -> bool:
    stop_order = row.get("stop_order")
    if isinstance(stop_order, Mapping):
        return bool(
            stop_order.get("origin_stop_id") and stop_order.get("destination_stop_id")
        )
    stop_ids = row.get("stop_ids")
    if isinstance(stop_ids, list):
        return len(stop_ids) >= 2
    return bool(row.get("departure_stop_id") and row.get("arrival_stop_id"))


def _geometry_direction(row: Mapping[str, object]) -> str | None:
    origin = _stop_coords(row.get("origin_coords"))
    destination = _stop_coords(row.get("destination_coords"))
    if origin is None or destination is None:
        return None
    latitude_delta = destination[0] - origin[0]
    longitude_delta = destination[1] - origin[1]
    if abs(latitude_delta) < max(0.001, abs(longitude_delta) * 1.75):
        return None
    return "uptown" if latitude_delta > 0 else "downtown"


def _stop_coords(value: object) -> tuple[float, float] | None:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        latitude, longitude = value
        if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
            return float(latitude), float(longitude)
    if not isinstance(value, Mapping):
        return None
    latitude = value.get("lat", value.get("latitude"))
    longitude = value.get("lon", value.get("lng", value.get("longitude")))
    if not isinstance(latitude, (int, float)) or not isinstance(
        longitude, (int, float)
    ):
        return None
    return float(latitude), float(longitude)


def _boarding_direction(
    boarding: Mapping[str, object] | None,
    routes: set[str],
    contexts: Iterable[Mapping[str, object]],
) -> str | None:
    """Resolve direction from the accepted trip's authoritative boarding leg."""
    if not isinstance(boarding, Mapping):
        return None
    route_id = str(boarding.get("route_id") or "").strip().upper()
    if route_id not in routes:
        return None
    direction = _semantic_direction(boarding, contexts)
    if direction:
        return direction
    return _resolve_context_label(boarding, ("headsign", "direction_label"), contexts)


def _itinerary_direction(
    itinerary: Mapping[str, object] | None,
    routes: set[str],
    contexts: Iterable[Mapping[str, object]],
) -> str | None:
    """Resolve direction from the accepted canonical itinerary's route leg."""
    if not isinstance(itinerary, Mapping):
        return None
    for context in _itinerary_contexts(itinerary, routes):
        direction = _semantic_direction(context, contexts)
        if direction:
            return direction
        direction = _resolve_context_label(
            context,
            ("direction", "direction_label", "headsign", "destination_stop_name"),
            contexts,
        )
        if direction:
            return direction
    return None


def accepted_trip_direction(ctx: object, route_ids: Iterable[str]) -> str | None:
    """Return the semantic direction on the active trip for one route."""

    session = getattr(ctx, "session", None)
    active_trip = session.get("active_trip") if isinstance(session, Mapping) else None
    boarding = (
        active_trip.get("first_boarding") if isinstance(active_trip, Mapping) else None
    )
    routes = {str(route).strip().upper() for route in route_ids if str(route).strip()}
    if not routes:
        return None
    contexts = _route_contexts(
        routes,
        session=session,
        gtfs=getattr(ctx, "gtfs", None),
    )
    direction = _boarding_direction(boarding, routes, contexts)
    if direction:
        return direction
    return _itinerary_direction(
        _active_canonical_itinerary(active_trip),
        routes,
        contexts,
    )


def _resolve_context_label(
    row: Mapping[str, object],
    keys: Iterable[str],
    contexts: Iterable[Mapping[str, object]],
) -> str | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        resolved = resolve_direction(value, contexts)
        if resolved.resolved:
            return resolved.resolved
    return None


def _semantic_direction(
    row: Mapping[str, object], contexts: Iterable[Mapping[str, object]] = ()
) -> str | None:
    """Read only explicit semantics preserved on the accepted leg/card."""

    resolved = _context_direction(row)
    if resolved:
        return resolved
    stop_order = row.get("stop_order")
    if isinstance(stop_order, Mapping):
        resolved = _context_direction(stop_order)
        if resolved:
            return resolved
    for context in contexts:
        if _same_ordered_segment(row, context):
            resolved = _context_direction(context)
            if resolved:
                return resolved
    return None


def _same_ordered_segment(
    row: Mapping[str, object], pattern: Mapping[str, object]
) -> bool:
    stop_ids = pattern.get("stop_ids")
    if not isinstance(stop_ids, list) or len(stop_ids) < 2:
        return False
    origin, destination = _endpoint_ids(row)
    if not origin or not destination:
        return False
    try:
        return stop_ids.index(origin) < stop_ids.index(destination)
    except ValueError:
        return False


def _endpoint_ids(row: Mapping[str, object]) -> tuple[str, str]:
    stop_order = row.get("stop_order")
    if isinstance(stop_order, Mapping):
        return (
            str(stop_order.get("origin_stop_id") or "").strip(),
            str(stop_order.get("destination_stop_id") or "").strip(),
        )
    return (
        str(row.get("departure_stop_id") or row.get("board_stop_id") or "").strip(),
        str(row.get("arrival_stop_id") or row.get("alight_stop_id") or "").strip(),
    )


__all__ = [
    "DirectionResolution",
    "accepted_trip_direction",
    "direction_clarification",
    "direction_matches",
    "normalize_direction",
    "normalize_direction_text",
    "resolve_model_direction",
    "resolve_direction",
    "stop_id_direction",
]
