"""Normalize transit transfer movement before scoring and projection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.trips.itinerary import TRANSIT_MODES
from app.services.trips.transfer_station_identity import (
    endpoint_fields,
    endpoint_id,
    endpoint_identity,
    endpoint_label,
    stop_details,
)
from app.utils import geo

WALK_SPEED_MPS = 1.4


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
        end = index
        previous = route[start - 1] if start > 0 else None
        following = route[end + 1] if end + 1 < len(route) else None
        if _is_transit(previous) and _is_transit(following):
            fact = _transfer_fact(
                previous,
                following,
                route[start : end + 1],
                gtfs,
                group_number,
            )
            group_number += 1
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
        index += 1
    return route


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
        if _mode(step) != "WALK":
            continue
        fact = step.get("transfer_semantics")
        group_id = str(step.get("semantic_transfer_group_id") or "")
        if isinstance(fact, dict) and group_id:
            if group_id in seen_groups:
                continue
            seen_groups.add(group_id)
            street += int(fact.get("street_walking_seconds") or 0)
            in_station += int(fact.get("in_station_transfer_seconds") or 0)
            continue
        street += _walk_seconds(step)
    return street, in_station


def route_accessibility(route: list[dict]) -> str:
    statuses = [
        str(fact.get("accessibility") or "unknown")
        for fact in route_transfer_facts(route)
    ]
    for step in route or []:
        if not _is_transit(step):
            continue
        for side in ("departure", "arrival"):
            for key in (f"{side}_accessibility", f"{side}_accessible"):
                if key in step:
                    statuses.append(_normalize_accessibility(step[key]))
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
    # Exact authoritative PLATFORM id equality. Equal canonical parent ids
    # (e.g. resolver-derived 'R14' == 'R14') are same_station, never silently
    # promoted to a same-platform claim.
    if (
        from_id
        and to_id
        and from_id == to_id
        and not from_is_parent
        and not to_is_parent
    ):
        return "same_platform"
    if from_parent and to_parent and from_parent == to_parent:
        return "same_station"
    if from_complex and to_complex and from_complex == to_complex:
        return "station_complex"
    return "street_transfer"


def _accessibility(previous: dict, following: dict, gtfs: Any) -> str:
    side_values: list[list[object]] = []
    for step, side in ((previous, "arrival"), (following, "departure")):
        values: list[object] = []
        values.extend(
            step.get(key)
            for key in (
                f"{side}_accessibility",
                f"{side}_accessible",
            )
            if key in step
        )
        details = stop_details(gtfs, endpoint_id(step, side) or "")
        values.extend(
            details.get(key)
            for key in ("accessibility", "accessible", "wheelchair_boarding")
            if key in details
        )
        side_values.append(values)
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
        return max(0, int(round(value)))
    dep = _parse_time(step.get("departure_time_iso"))
    arr = _parse_time(step.get("arrival_time_iso"))
    if dep is not None and arr is not None:
        return max(0, int(round((arr - dep).total_seconds())))
    start = _coords(step.get("start_point"))
    end = _coords(step.get("end_point"))
    if start is None or end is None:
        return 0
    return max(0, int(round(geo.distance_meters(*start, *end) / WALK_SPEED_MPS)))


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
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
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


__all__ = (
    "normalize_route",
    "normalize_routes",
    "route_accessibility",
    "route_transfer_facts",
    "route_walking_totals",
)
