"""Normalize transit transfer movement before scoring and projection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from app.services import geography as geo
from app.services.trips.itinerary import TRANSIT_MODES

WALK_SPEED_MPS = 1.4


@dataclass(frozen=True)
class EndpointIdentity:
    stop_id: str | None
    is_parent: bool = False
    parent: str | None = None
    complex: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stop_id": self.stop_id,
            "is_parent": self.is_parent,
            "parent": self.parent,
            "complex": self.complex,
        }


def normalize_routes(routes: list[list[dict]], gtfs: Any = None) -> list[list[dict]]:
    """Annotate every route with server-owned transfer facts in place."""

    for route in routes or []:
        normalize_route(route, gtfs)
    return routes


def normalize_route(route: list[dict], gtfs: Any = None) -> list[dict]:
    if not isinstance(route, list):
        return route
    for index in range(len(route)):
        if str(route[index].get("type") or "").upper() not in TRANSIT_MODES:
            continue
        route[index].update(endpoint_fields(route[index], _route_id(route[index]), gtfs))

    index = 0
    group_number = 0
    while index < len(route):
        if _mode(route[index]) != "WALK":
            index += 1
            continue
        start = index
        while index + 1 < len(route) and _mode(route[index + 1]) == "WALK":
            index += 1
        group_number = _annotate_walk_transfer(
            route, start, index, gtfs, group_number
        )
        index += 1
    return route


def _annotate_walk_transfer(
    route: list[dict],
    start: int,
    end: int,
    gtfs: Any,
    group_number: int,
) -> int:
    previous = route[start - 1] if start > 0 else None
    following = route[end + 1] if end + 1 < len(route) else None
    if not (_is_transit(previous) and _is_transit(following)):
        return group_number
    fact = _transfer_fact(
        previous,
        following,
        route[start : end + 1],
        gtfs,
        group_number,
    )
    fragments = route[start : end + 1]
    first_fragment = fragments[0]
    last_fragment = fragments[-1]
    if last_fragment.get("arrival_time_iso"):
        first_fragment["arrival_time_iso"] = last_fragment["arrival_time_iso"]
    if last_fragment.get("end_point"):
        first_fragment["end_point"] = last_fragment["end_point"]
    first_fragment["transfer_duration_seconds"] = fact["total_seconds"]
    for fragment_index in range(start, end + 1):
        route[fragment_index]["semantic_transfer_group_id"] = fact["group_id"]
        route[fragment_index]["transfer_semantics"] = fact
        route[fragment_index]["semantic_transfer"] = fact
        route[fragment_index]["transfer_kind"] = fact["kind"]
        route[fragment_index]["semantic_transfer_fragment"] = fragment_index != start
    return group_number + 1


def route_transfer_facts(route: list[dict]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for step in route or []:
        fact = step.get("transfer_semantics")
        group_id = str(step.get("semantic_transfer_group_id") or "")
        if isinstance(fact, dict) and group_id and group_id not in seen:
            facts.append(dict(fact))
            seen.add(group_id)
    return facts


def route_walking_totals(route: list[dict]) -> tuple[int, int]:
    """Return (street_walking_seconds, in_station_transfer_seconds)."""

    street = 0
    in_station = 0
    seen_groups: set[str] = set()
    for step in route or []:
        contribution = _walk_step_seconds(step)
        if contribution is None:
            continue
        group_id, street_seconds, station_seconds = contribution
        if group_id:
            if group_id in seen_groups:
                continue
            seen_groups.add(group_id)
        street += street_seconds
        in_station += station_seconds
    return street, in_station


def _walk_step_seconds(step: dict) -> tuple[str | None, int, int] | None:
    if _mode(step) != "WALK":
        return None
    fact = step.get("transfer_semantics")
    group_id = str(step.get("semantic_transfer_group_id") or "")
    if isinstance(fact, dict) and group_id:
        return (
            group_id,
            int(fact.get("street_walking_seconds") or 0),
            int(fact.get("in_station_transfer_seconds") or 0),
        )
    return None, _walk_seconds(step), 0


def route_accessibility(route: list[dict]) -> str:
    statuses = [
        str(fact.get("accessibility") or "unknown")
        for fact in route_transfer_facts(route)
    ]
    statuses.extend(_collect_transit_accessibility(route))
    return _select_accessibility(statuses)


def _collect_transit_accessibility(route: list[dict]) -> list[str]:
    statuses: list[str] = []
    for step in route or []:
        if not _is_transit(step):
            continue
        statuses.extend(
            _normalize_accessibility(step[key])
            for side in ("departure", "arrival")
            for key in (f"{side}_accessibility", f"{side}_accessible")
            if key in step
        )
    return statuses


def _select_accessibility(statuses: list[str]) -> str:
    if "inaccessible" in statuses:
        return "inaccessible"
    if statuses and all(status == "accessible" for status in statuses):
        return "accessible"
    return "unknown"


def _transfer_fact(
    previous: dict,
    following: dict,
    fragments: list[dict],
    gtfs: Any,
    group_number: int,
) -> dict[str, Any]:
    from_identity = endpoint_identity(previous, "arrival", gtfs)
    to_identity = endpoint_identity(following, "departure", gtfs)
    kind = _classify_transfer(
        from_identity["stop_id"],
        to_identity["stop_id"],
        from_identity["parent"],
        to_identity["parent"],
        from_identity["complex"],
        to_identity["complex"],
        from_identity["is_parent"],
        to_identity["is_parent"],
    )
    total_seconds = sum(_walk_seconds(fragment) for fragment in fragments)
    in_station_seconds = total_seconds if kind in {
        "same_platform",
        "same_station",
        "station_complex",
    } else 0
    street_seconds = total_seconds - in_station_seconds
    return {
        "group_id": f"transfer_{group_number}",
        "kind": kind,
        "from_route_id": _route_id(previous),
        "to_route_id": _route_id(following),
        "from_stop_id": from_identity["stop_id"],
        "to_stop_id": to_identity["stop_id"],
        "from_parent_station": from_identity["parent"],
        "to_parent_station": to_identity["parent"],
        "from_station_label": endpoint_label(previous, "arrival", gtfs),
        "to_station_label": endpoint_label(following, "departure", gtfs),
        "street_walking_seconds": street_seconds,
        "in_station_transfer_seconds": in_station_seconds,
        "total_seconds": total_seconds,
        "fragment_count": len(fragments),
        "accessibility": _accessibility(previous, following, gtfs),
    }


def _same_platform_ids(
    from_id: str | None,
    to_id: str | None,
    from_is_parent: bool,
    to_is_parent: bool,
) -> bool:
    # Exact authoritative PLATFORM id equality. Equal canonical parent ids
    # (e.g. resolver-derived 'R14' == 'R14') are same_station, never silently
    # promoted to a same-platform claim.
    return bool(
        from_id
        and to_id
        and from_id == to_id
        and not from_is_parent
        and not to_is_parent
    )


def _classify_transfer(
    from_id: str | None,
    to_id: str | None,
    from_parent: str | None,
    to_parent: str | None,
    from_complex: str | None,
    to_complex: str | None,
    from_is_parent: bool,
    to_is_parent: bool,
) -> str:
    if _same_platform_ids(from_id, to_id, from_is_parent, to_is_parent):
        return "same_platform"
    if from_parent and to_parent and from_parent == to_parent:
        return "same_station"
    if from_complex and to_complex and from_complex == to_complex:
        return "station_complex"
    return "street_transfer"


def _accessibility(previous: dict, following: dict, gtfs: Any) -> str:
    side_values = [
        _endpoint_accessibility_values(step, side, gtfs)
        for step, side in ((previous, "arrival"), (following, "departure"))
    ]
    normalized = [
        _normalize_accessibility(value)
        for values in side_values
        for value in values
    ]
    if "inaccessible" in normalized:
        return "inaccessible"
    if _all_sides_accessible(side_values):
        return "accessible"
    return "unknown"


def _endpoint_accessibility_values(step: dict, side: str, gtfs: Any) -> list[object]:
    values: list[object] = [
        step.get(key)
        for key in (f"{side}_accessibility", f"{side}_accessible")
        if key in step
    ]
    details = stop_details(gtfs, endpoint_id(step, side) or "")
    values.extend(
        details.get(key)
        for key in ("accessibility", "accessible", "wheelchair_boarding")
        if key in details
    )
    return values


def _all_sides_accessible(side_values: list[list[object]]) -> bool:
    return all(
        values
        and all(_normalize_accessibility(value) == "accessible" for value in values)
        for values in side_values
    )


def _normalize_accessibility(value: object) -> str:
    if isinstance(value, bool):
        return "accessible" if value else "inaccessible"
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes", "accessible", "available", "full"}:
        return "accessible"
    if text in {"0", "false", "no", "inaccessible", "closed", "outage"}:
        return "inaccessible"
    return "unknown"


def _walk_seconds(step: dict) -> int:
    value = step.get("duration_seconds")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, round(value))
    dep = _parse_time(step.get("departure_time_iso"))
    arr = _parse_time(step.get("arrival_time_iso"))
    if dep is not None and arr is not None:
        return max(0, round((arr - dep).total_seconds()))
    start = _coords(step.get("start_point"))
    end = _coords(step.get("end_point"))
    if start is None or end is None:
        return 0
    return max(0, round(geo.distance_meters(*start, *end) / WALK_SPEED_MPS))


def _coords(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        lat = float(value.get("latitude", value.get("lat")))
        lng = float(value.get("longitude", value.get("lng", value.get("lon"))))
    except (TypeError, ValueError):
        return None
    return lat, lng


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _mode(step: dict | None) -> str:
    return str(step.get("type") or "").strip().upper() if isinstance(step, dict) else ""


def _is_transit(step: dict | None) -> bool:
    return _mode(step) in TRANSIT_MODES


def _route_id(step: dict | None) -> str | None:
    if not isinstance(step, dict):
        return None
    value = str(step.get("route_id") or step.get("train_line") or "").strip().upper()
    return value or None


def _pattern_index_callable(gtfs: Any, name: str):
    method = getattr(getattr(gtfs, "_pattern_index", None), name, None)
    return method if callable(method) else None


def _parse_optional_id(*values: object) -> str | None:
    current: object = ""
    for value in values:
        current = current or value
    text = str(current or "").strip()
    return text or None


def endpoint_fields(step: dict, route_id: str | None, gtfs: Any) -> dict[str, Any]:
    """Annotate one transit step with canonical endpoint ids when available."""
    resolved = _select_resolved_segment(step, route_id, gtfs)
    if resolved is None:
        return {}
    return _project_resolver_parent_ids(step, resolved)


def _select_resolved_segment(
    step: dict, route_id: str | None, gtfs: Any
) -> dict[str, Any] | None:
    resolver = _pattern_index_callable(gtfs, "resolve_route_segment")
    if resolver is None or not route_id:
        return None
    try:
        resolved = resolver(
            route_id,
            step.get("departure_stop"),
            step.get("arrival_stop"),
            step.get("departure_coords"),
            step.get("arrival_coords"),
        )
    except (AttributeError, TypeError, ValueError):
        return None
    return resolved if isinstance(resolved, dict) else None


def _project_resolver_parent_ids(step: dict, resolved: dict) -> dict[str, Any]:
    result: dict[str, Any] = {}
    origin_id = resolved.get("origin_stop_id")
    destination_id = resolved.get("destination_stop_id")
    # Resolver ids are canonical parent station ids. Keep explicit
    # platform ids authoritative and mark inferred ids as parents.
    if origin_id and not endpoint_id(step, "departure"):
        result["departure_stop_id"] = origin_id
        result["departure_stop_is_parent"] = True
    if destination_id and not endpoint_id(step, "arrival"):
        result["arrival_stop_id"] = destination_id
        result["arrival_stop_is_parent"] = True
    result.setdefault("direction_id", resolved.get("direction_id"))
    return {key: value for key, value in result.items() if value not in (None, "")}


def endpoint_id(step: dict | None, side: str) -> str | None:
    if not isinstance(step, dict):
        return None
    value = str(step.get(f"{side}_stop_id") or "").strip()
    return value or None


def endpoint_identity(step: dict | None, side: str, gtfs: Any) -> dict[str, Any]:
    """Resolve one endpoint's canonical station identity."""
    identity = _parse_endpoint_fields(step, side)
    if not identity.stop_id:
        return identity.as_dict()
    if isinstance(step, dict) and step.get(f"{side}_stop_is_parent") is True:
        return _project_marked_parent(identity, gtfs).as_dict()
    lookup = _pattern_index_callable(gtfs, "identity_for_stop")
    if lookup is not None:
        return _project_indexed_identity(identity, lookup(identity.stop_id)).as_dict()
    return _project_legacy_identity(identity, gtfs).as_dict()


def _parse_endpoint_fields(step: dict | None, side: str) -> EndpointIdentity:
    stop_id = endpoint_id(step, side)
    if not isinstance(step, dict):
        return EndpointIdentity(stop_id=stop_id)
    parent = _parse_optional_id(step.get(f"{side}_parent_station"))
    complex_id = _parse_optional_id(
        step.get(f"{side}_station_complex_id"), step.get(f"{side}_complex_id")
    )
    return EndpointIdentity(stop_id=stop_id, parent=parent, complex=complex_id)


def _project_marked_parent(identity: EndpointIdentity, gtfs: Any) -> EndpointIdentity:
    parent = identity.parent or identity.stop_id
    lookup = _pattern_index_callable(gtfs, "identity_for_stop")
    if lookup is None:
        return replace(identity, is_parent=True, parent=parent)
    indexed = lookup(identity.stop_id)
    return replace(
        identity,
        is_parent=True,
        parent=parent or indexed.get("parent_station"),
        complex=identity.complex or indexed.get("station_complex_id"),
    )


def _project_indexed_identity(
    identity: EndpointIdentity, indexed: Any
) -> EndpointIdentity:
    known = indexed.get("parent_station")
    if known:
        return replace(
            identity,
            parent=identity.parent or known,
            complex=identity.complex or indexed.get("station_complex_id"),
            is_parent=not indexed.get("is_platform", False),
        )
    # Unknown indexed stops cannot claim same_platform.
    return replace(identity, is_parent=True)


def _project_legacy_identity(identity: EndpointIdentity, gtfs: Any) -> EndpointIdentity:
    details = stop_details(gtfs, identity.stop_id or "")
    parent = identity.parent or _parse_optional_id(
        details.get("parent_station"), details.get("parent_stop_id")
    )
    complex_id = identity.complex or _parse_optional_id(
        details.get("station_complex_id"), details.get("complex_id")
    )
    stop_id = identity.stop_id or ""
    is_parent = stop_id == stop_id.rstrip("NS")
    if is_parent and parent is None:
        parent = identity.stop_id
    return replace(identity, parent=parent, complex=complex_id, is_parent=is_parent)


def stop_details(gtfs: Any, stop_id: str) -> dict[str, Any]:
    """Return canonical stop details from the attached index or legacy GTFS."""
    if not stop_id:
        return {}
    lookup = _pattern_index_callable(gtfs, "identity_for_stop")
    if lookup is not None:
        identity = lookup(stop_id)
        parent = identity.get("parent_station")
        if not parent:
            return {}
        details: dict[str, Any] = {
            "parent_station": parent,
            "station_complex_id": identity.get("station_complex_id"),
        }
        info = getattr(gtfs._pattern_index, "stops", {}).get(parent)
        if isinstance(info, dict):
            details["name"] = info.get("name")
            details["stop_name"] = info.get("name")
            details["station_name"] = info.get("name")
        return details
    getter = getattr(gtfs, "get_stop_locations", None)
    if not callable(getter):
        return {}
    try:
        result = getter([stop_id])
    except (AttributeError, TypeError, ValueError):
        return {}
    if not isinstance(result, dict):
        return {}
    return result.get(stop_id) or result.get(stop_id.rstrip("NS")) or {}


def endpoint_label(step: dict | None, side: str, gtfs: Any) -> str | None:
    if not isinstance(step, dict):
        return None
    for key in (
        f"{side}_station_name",
        f"{side}_stop_name",
        f"{side}_stop",
        f"{side}_station",
    ):
        value = str(step.get(key) or "").strip()
        if value:
            return value
    details = stop_details(gtfs, endpoint_id(step, side) or "")
    for key in ("station_name", "stop_name", "name"):
        value = str(details.get(key) or "").strip()
        if value:
            return value
    return None


__all__ = (
    "endpoint_fields",
    "endpoint_id",
    "endpoint_identity",
    "endpoint_label",
    "normalize_route",
    "normalize_routes",
    "route_accessibility",
    "route_transfer_facts",
    "route_walking_totals",
    "stop_details",
)
