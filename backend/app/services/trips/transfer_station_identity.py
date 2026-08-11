"""Canonical endpoint and station identity resolution for transfer semantics."""

from __future__ import annotations

from typing import Any


def endpoint_fields(step: dict, route_id: str | None, gtfs: Any) -> dict[str, Any]:
    """Annotate one transit step's endpoint ids via the route-pattern index.

    ``route_id`` is the caller's already-derived route identity for this
    step; the resolver supplies canonical parent station ids when the step
    carries no explicit stop ids.
    """
    result: dict[str, Any] = {}
    resolver = getattr(getattr(gtfs, "_pattern_index", None), "resolve_route_segment", None)
    if callable(resolver) and route_id:
        try:
            resolved = resolver(
                route_id,
                step.get("departure_stop"),
                step.get("arrival_stop"),
                step.get("departure_coords"),
                step.get("arrival_coords"),
            )
        except (AttributeError, TypeError, ValueError):
            resolved = None
        if isinstance(resolved, dict):
            origin_id = resolved.get("origin_stop_id")
            destination_id = resolved.get("destination_stop_id")
            # Preserve an explicit platform stop id already on the step.
            # Resolver ids are canonical PARENT station ids (pattern
            # stop_ids): record them as such so equal parent ids classify as
            # same_station and never masquerade as platform identity.
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
    key = f"{side}_stop_id"
    value = str(step.get(key) or "").strip()
    return value or None


def endpoint_identity(step: dict | None, side: str, gtfs: Any) -> dict[str, Any]:
    """Resolve one endpoint's canonical station identity.

    Order: explicit step fields, then the in-memory stop-pattern index when
    present, then the legacy DB details lookup (no index attached). Never
    infers identity from names, coordinates, or route overlap.

    Returns {stop_id, is_parent, parent, complex}:
      - ``is_parent``: True when the stop id is a canonical PARENT identity
        (e.g. 'R14') rather than a platform identity ('R14N'). Unknown stops
        with an index are parent-like: they can never claim same_platform.
      - ``parent`` / ``complex``: canonical parent station id and GTFS
        transfer-component identity, or None when unknown.
    """
    result: dict[str, Any] = {
        "stop_id": endpoint_id(step, side),
        "is_parent": False,
        "parent": None,
        "complex": None,
    }
    if not isinstance(step, dict):
        return result
    result["parent"] = str(step.get(f"{side}_parent_station") or "").strip() or None
    result["complex"] = str(
        step.get(f"{side}_station_complex_id")
        or step.get(f"{side}_complex_id")
        or ""
    ).strip() or None
    stop_id = result["stop_id"]
    if not stop_id:
        return result

    marker = step.get(f"{side}_stop_is_parent")
    if marker is True:
        # Resolver-derived ids are canonical parent station ids.
        result["is_parent"] = True
        if result["parent"] is None:
            result["parent"] = stop_id
        # Resolver parents are also index members: enrich the optional GTFS
        # transfer-component identity before returning. Never a request-time
        # DB lookup when an index is attached.
        index = getattr(getattr(gtfs, "_pattern_index", None), "identity_for_stop", None)
        if callable(index):
            identity = index(stop_id)
            result["parent"] = result["parent"] or identity.get("parent_station")
            result["complex"] = result["complex"] or identity.get("station_complex_id")
        return result

    index = getattr(getattr(gtfs, "_pattern_index", None), "identity_for_stop", None)
    if callable(index):
        identity = index(stop_id)
        known = identity.get("parent_station")
        if known:
            result["parent"] = result["parent"] or known
            result["complex"] = result["complex"] or identity.get("station_complex_id")
            result["is_parent"] = not identity.get("is_platform", False)
        else:
            # Unknown stop: degrade to unknown identity -- no same_platform
            # claim and no invented parent, and never a DB fallback.
            result["is_parent"] = True
        return result

    # Legacy path: no in-memory pattern index attached.
    details = stop_details(gtfs, stop_id)
    result["parent"] = (
        result["parent"]
        or str(details.get("parent_station") or details.get("parent_stop_id") or "").strip()
        or None
    )
    result["complex"] = (
        result["complex"]
        or str(details.get("station_complex_id") or details.get("complex_id") or "").strip()
        or None
    )
    is_parent = stop_id == stop_id.rstrip("NS")
    result["is_parent"] = is_parent
    if is_parent and result["parent"] is None:
        # A canonical parent id IS its own parent (e.g. 'R14' -> 'R14').
        result["parent"] = stop_id
    return result


def stop_details(gtfs: Any, stop_id: str) -> dict[str, Any]:
    """Canonical stop details for identity/label lookups.

    Prefers the in-memory stop-pattern index (parent, transfer component,
    name) whenever it is attached; unknown stops degrade to {} -- never a
    request-time database query. The remote lookup runs only when no pattern
    index exists (legacy/test path).
    """
    if not stop_id:
        return {}
    index = getattr(getattr(gtfs, "_pattern_index", None), "identity_for_stop", None)
    if callable(index):
        identity = index(stop_id)
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
