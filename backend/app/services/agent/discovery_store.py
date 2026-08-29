"""Server-owned, session-scoped discovery sets and place references."""

from __future__ import annotations

import json
import math
import re
import secrets
import time
from typing import Any

from app.services import cache
from app.services.agent import presented_entity_registry as entity_registry

DISCOVERY_SET_PREFIX = "agent:dset:"
DEFAULT_TTL_S = 1800
MAX_PLACES = 8
MAX_PRESENTED_ENTITIES = entity_registry.MAX_ENTRIES
PRESENTED_ENTITY_REGISTRY_FIELD = entity_registry.REGISTRY_FIELD

_PLACE_ID_PREFIX = "pl_"
_ALLOWED_RANKING_FACTORS = ("rating", "review_volume", "open_bonus", "price_level")
_QUEUE_CONTEXT_MODES = frozenset({"ignore", "heads_up", "decision", "historical"})
_DEFAULT_QUEUE_CONTEXT: dict[str, Any] = {
    "mode": "ignore",
    "max_wait_minutes": None,
}
_CONTINUATION_TOKEN_RE = re.compile(r"^target_[0-4]$")

_PLACES_PRICE_LEVELS = {
    "PRICE_LEVEL_UNSPECIFIED": None,
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
    # Backward-compatible aliases; provider payloads use the full forms.
    "FREE": 0,
    "INEXPENSIVE": 1,
    "MODERATE": 2,
    "EXPENSIVE": 3,
    "VERY_EXPENSIVE": 4,
}


def new_discovery_set_id() -> str:
    return f"ds_{secrets.token_urlsafe(12)}"


def new_place_id() -> str:
    return f"pl_{secrets.token_urlsafe(10)}"


def is_opaque_place_id(value: object) -> bool:
    return str(value or "").strip().startswith(_PLACE_ID_PREFIX)


def normalize_price_level(value: object) -> int | None:
    """Map provider price levels and numeric values to deterministic 0-4 ints."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number.is_integer() and 0 <= number <= 4:
            return int(number)
        return None
    return _PLACES_PRICE_LEVELS.get(str(value).strip().upper())


def sanitized_ranking_factors(value: object) -> dict[str, Any]:
    """Allow only bounded numeric ranking context; never provider payloads."""

    factors = value if isinstance(value, dict) else {}
    result = {
        key: factors[key]
        for key in _ALLOWED_RANKING_FACTORS
        if key in factors and _is_finite_number(factors[key])
    }
    price = _finite_price(result.get("price_level"))
    if price is None:
        result.pop("price_level", None)
    else:
        result["price_level"] = price
    return result


def _is_finite_number(value: object) -> bool:
    """Return whether value is a finite real number."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number)


def _finite_price(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_optional(value: object) -> Any:
    if value is None:
        return value
    return value if _is_finite_number(value) else _OMIT


_OMIT = object()


def _finite_number_or_none(value: object) -> float | None:
    return value if value is None or _is_finite_number(value) else None


def sanitized_queue_context(value: object) -> dict[str, Any] | None:
    """Validate the current discovery decision's private queue instructions."""

    if not isinstance(value, dict) or set(value) != {
        "mode",
        "max_wait_minutes",
    }:
        return None
    mode = value.get("mode")
    if not isinstance(mode, str) or mode not in _QUEUE_CONTEXT_MODES:
        return None
    max_wait = value.get("max_wait_minutes")
    if max_wait is not None and (
        not _is_finite_number(max_wait) or float(max_wait) < 0
    ):
        return None
    return {"mode": mode, "max_wait_minutes": max_wait}


def sanitized_continuation_tokens(value: object) -> dict[str, str]:
    tokens = value if isinstance(value, dict) else {}
    return {
        key: token[:4096]
        for key, raw_token in tokens.items()
        if isinstance(key, str)
        and _CONTINUATION_TOKEN_RE.fullmatch(key)
        and isinstance(raw_token, str)
        and (token := raw_token.strip())
    }


def _sanitized_option(place: dict[str, Any]) -> dict[str, Any]:
    option = {
        "place_id": place.get("place_id"),
        "ordinal": place.get("ordinal"),
        "name": place.get("name"),
        "address": place.get("address"),
        "neighborhood": place.get("neighborhood"),
        "borough": place.get("borough"),
        "category": place.get("category"),
        "search_area": place.get("search_area"),
        "open_status": place.get("open_status"),
        "price_level": _finite_optional(place.get("price_level")),
        "rating": _finite_optional(place.get("rating")),
        "review_count": _finite_optional(place.get("review_count")),
        "baseline_score": _finite_optional(place.get("baseline_score")),
        "ranking_factors": sanitized_ranking_factors(place.get("ranking_factors")),
    }
    for field in ("rider_distance_meters",):
        value = _finite_number_or_none(place.get(field))
        if value is not None:
            option[field] = value
    return {key: item for key, item in option.items() if item is not _OMIT}


def display_waypoint_labels(
    waypoints: list[str], *, session_id: str, discovery_set_id: str | None
) -> list[str]:
    """Resolve opaque waypoint ids to stored labels without re-geocoding."""

    if not any(is_opaque_place_id(item) for item in waypoints):
        return list(waypoints)
    record = (
        load_discovery_set(discovery_set_id, session_id=session_id)
        if discovery_set_id
        else None
    )
    by_id = {
        str(place.get("place_id")): place
        for place in (record or {}).get("places") or []
        if isinstance(place, dict) and place.get("place_id")
    }
    return [
        str(by_id[item].get("name") or item)
        if is_opaque_place_id(item) and item in by_id
        else item
        for item in waypoints
    ]


def _identity_key(place: dict[str, Any]) -> str:
    """Return the private, stable identity used by the session registry."""

    kind, value = _place_identity(place)
    return f"{kind}:{value}"


def presented_entity_registry(session: dict | None) -> list[dict[str, Any]]:
    """Expose a copy of the bounded registry for state/context projection."""

    return entity_registry.snapshot(session)


def clear_presented_entity_registry(session: dict | None) -> None:
    """Clear visible-place memory when the conversation is explicitly reset."""

    entity_registry.clear(session)


def _key(discovery_set_id: str) -> str:
    return f"{DISCOVERY_SET_PREFIX}{discovery_set_id}"


def store_discovery_set(
    *,
    session_id: str,
    places: list[dict[str, Any]],
    session: dict | None = None,
    query: str = "",
    search_scope: dict[str, Any] | None = None,
    requested_count: int | None = None,
    coverage: dict[str, Any] | None = None,
    queue_context: dict[str, Any] | None = None,
    continuation_tokens: dict[str, str] | None = None,
    ttl_seconds: int = DEFAULT_TTL_S,
) -> str:
    set_id = new_discovery_set_id()
    now = time.time()
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    prior_place_ids = entity_registry.place_ids(session)
    for place in places:
        if not isinstance(place, dict):
            continue
        identity = _place_identity(place)
        if identity in seen:
            continue
        seen.add(identity)
        if len(normalized) >= MAX_PLACES:
            break
        identity_key = _identity_key(place)
        place_id = prior_place_ids.get(identity_key) or new_place_id()
        normalized.append(
            {
                "place_id": place_id,
                "ordinal": len(normalized) + 1,
                "name": str(place.get("name") or "")[:120],
                "address": str(place.get("address") or "")[:200],
                "neighborhood": str(place.get("neighborhood") or "")[:80],
                "borough": str(place.get("borough") or "")[:40],
                "category": str(place.get("category") or "")[:60],
                "open_status": place.get("open_status"),
                "price_level": normalize_price_level(place.get("price_level")),
                "rating": place.get("rating"),
                "review_count": place.get("review_count"),
                "baseline_score": place.get("baseline_score"),
                "ranking_factors": place.get("ranking_factors") or {},
                "latitude": _finite_number_or_none(place.get("latitude")),
                "longitude": _finite_number_or_none(place.get("longitude")),
                "rider_distance_meters": _finite_number_or_none(
                    place.get("rider_distance_meters")
                ),
                "provider_place_id": place.get("provider_place_id"),
                "transit_context": place.get("transit_context") or {},
                "search_area": str(place.get("search_area") or "")[:80],
            }
        )
    record = {
        "discovery_set_id": set_id,
        "session_id": session_id,
        "created_at": now,
        "expires_at": now + max(30, int(ttl_seconds)),
        "query": str(query or "")[:160],
        "search_scope": _sanitized_search_scope(search_scope),
        "requested_count": _bounded_count(requested_count),
        "coverage": _sanitized_coverage(coverage),
        "queue_context": sanitized_queue_context(queue_context)
        or dict(_DEFAULT_QUEUE_CONTEXT),
        "continuation_tokens": sanitized_continuation_tokens(continuation_tokens),
        "places": normalized,
    }
    cache.cache_set(
        _key(set_id),
        json.dumps(record, separators=(",", ":"), default=str),
        int(ttl_seconds),
        fail_open=True,
    )
    return set_id


def _place_identity(place: dict[str, Any]) -> tuple[str, str]:
    provider_id = str(place.get("provider_place_id") or "").strip().casefold()
    if provider_id:
        return "provider", provider_id
    name = _normalized_name(place.get("name"))
    address = " ".join(str(place.get("address") or "").casefold().split())
    if name or address:
        return "name_address", f"{name}|{address}"
    return "coordinates", f"{place.get('latitude')}|{place.get('longitude')}"


def _bounded_count(value: object) -> int | None:
    if value is None:
        return None
    try:
        return max(1, min(MAX_PLACES, int(value)))
    except (TypeError, ValueError):
        return None


def _sanitized_coverage(value: object) -> dict[str, Any]:
    coverage = value if isinstance(value, dict) else {}
    status = str(coverage.get("status") or "complete").strip().casefold()
    if status not in {"complete", "partial"}:
        status = "complete"
    searched = _coverage_labels(coverage.get("searched_areas"))
    unavailable = _coverage_labels(coverage.get("unavailable_areas"))
    return {
        "status": status,
        "searched_areas": searched,
        "unavailable_areas": unavailable,
    }


def _coverage_labels(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    labels: list[str] = []
    for item in value:
        label = " ".join(str(item or "").split())[:80]
        if label and label.casefold() not in {item.casefold() for item in labels}:
            labels.append(label)
        if len(labels) >= 5:
            break
    return labels


def _sanitized_search_scope(value: object) -> dict[str, Any]:
    scope = value if isinstance(value, dict) else {}
    kind = str(scope.get("kind") or "citywide")
    if kind == "current_location":
        return {"kind": "current_location", "values": []}
    if kind == "nyc":
        return {"kind": "nyc", "values": []}
    if kind == "boroughs":
        values = [
            str(area).strip()[:80]
            for area in (scope.get("values") or scope.get("areas") or [])
            if str(area).strip()
        ][:5]
        return {"kind": "boroughs", "values": values}
    if kind == "named_area":
        values = [
            str(area).strip()[:120]
            for area in (scope.get("values") or [])
            if str(area).strip()
        ][:1]
        return (
            {"kind": "named_area", "values": values}
            if values
            else {"kind": "nyc", "values": []}
        )
    if kind == "areas":
        areas = [
            str(area).strip()[:80]
            for area in (scope.get("areas") or [])
            if str(area).strip()
        ][:5]
        return {"kind": "areas", "areas": areas}
    if kind == "nearby":
        return {"kind": "nearby"}
    if kind == "named":
        near = str(scope.get("near") or "").strip()[:120]
        return {"kind": "named", "near": near} if near else {"kind": "citywide"}
    return {"kind": "citywide"}


def load_discovery_set(
    discovery_set_id: str, *, session_id: str
) -> dict[str, Any] | None:
    if not discovery_set_id or not session_id:
        return None
    raw = cache.cache_get(_key(discovery_set_id), fail_open=True)
    if raw is None:
        return None
    try:
        blob = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        record = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    if str(record.get("session_id") or "") != session_id:
        return None
    if float(record.get("expires_at") or 0) < time.time():
        return None
    return record


def record_presented_places(
    session: dict | None,
    *,
    session_id: str,
    discovery_set_id: str,
    places: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return entity_registry.record(
        session,
        session_id=session_id,
        discovery_set_id=discovery_set_id,
        places=places,
    )


def resolve_presented_place_reference(
    *,
    session: dict | None,
    session_id: str,
    place_id: str | None = None,
    ordinal: int | None = None,
    description: str | None = None,
    discovery_set_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    return entity_registry.resolve(
        session=session,
        session_id=session_id,
        place_id=place_id,
        ordinal=ordinal,
        description=description,
        discovery_set_id=discovery_set_id,
    )


def _normalized_name(value: object) -> str:
    """Normalize a rider-visible place name for conservative name matching."""

    text = str(value or "").casefold().replace("\u2019", "'")
    # Possessive punctuation should not make ``Mike's`` and ``mikes``
    # different references. Every other separator becomes a word boundary.
    text = text.replace("'", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def resolve_place_reference(
    *,
    session_id: str,
    discovery_set_id: str | None,
    place_id: str | None = None,
    ordinal: int | None = None,
    description: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve a conversational place reference against a server-owned set."""

    if not discovery_set_id:
        return None, "no active discovery set"
    record = load_discovery_set(discovery_set_id, session_id=session_id)
    if record is None:
        return None, "discovery set is unknown, expired, or not owned by this session"
    places = [
        place for place in (record.get("places") or []) if isinstance(place, dict)
    ]
    if place_id:
        for place in places:
            if str(place.get("place_id") or "") == place_id:
                return place, None
        return None, "place id is unknown for this discovery set"
    if ordinal is not None:
        wanted = int(ordinal)
        for place in places:
            if int(place.get("ordinal") or 0) == wanted:
                return place, None
        return None, "ordinal is out of range for this discovery set"
    if description is not None:
        return entity_registry.resolve_description(places, description)
    return None, "place reference is incomplete"


def sanitized_discovery_context(
    session: dict | None,
    session_id: str,
) -> dict[str, Any] | None:
    """Compact, sanitized discovery metadata for the model's per-turn context.

    Includes bounded place distance but never place coordinates, rider GPS, or
    provider place ids. References remain opaque.
    """

    from app.services.agent import trip_state as trip_state_module

    state = trip_state_module.get_trip_state(session)
    discovery_set_id = state.get("active_discovery_set_id")
    record = (
        load_discovery_set(discovery_set_id, session_id=session_id)
        if discovery_set_id
        else None
    )
    options = [
        _sanitized_option(place)
        for place in (record or {}).get("places") or []
        if isinstance(place, dict) and place.get("place_id")
    ]
    registry = presented_entity_registry(session)
    presented = [
        {
            "place_id": entry.get("place_id"),
            "discovery_set_id": entry.get("discovery_set_id"),
            "ordinal": entry.get("ordinal"),
            "presentation_sequence": entry.get("presentation_sequence"),
            "name": entry.get("name"),
            "address": entry.get("address"),
            "neighborhood": entry.get("neighborhood"),
            "borough": entry.get("borough"),
            "category": entry.get("category"),
            "reason": entry.get("reason"),
        }
        for entry in registry
    ]
    if record is None and not presented:
        return None
    return {
        "discovery_set_id": discovery_set_id if record is not None else None,
        "query": record.get("query") if record is not None else None,
        "search_scope": (
            record.get("search_scope") if record is not None else {"kind": "citywide"}
        ),
        "requested_count": record.get("requested_count")
        if record is not None
        else None,
        "coverage": (
            record.get("coverage") if record is not None else {"status": "complete"}
        ),
        "selected_place_id": state.get("selected_place_id"),
        "options": options,
        "presented_entities": presented,
    }
