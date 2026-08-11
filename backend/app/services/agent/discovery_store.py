"""Server-owned local-discovery place sets for conversational references.

Place IDs are opaque, session-scoped, and expiring. Invented or cross-session
references fail safely. Routing resolves the stored canonical ResolvedPlace
and its coordinates; a model-retyped label is never re-geocoded.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import time
from typing import Any

from app.utils import cache

DISCOVERY_SET_PREFIX = "agent:dset:"
DEFAULT_TTL_S = 900
MAX_PLACES = 8

_PRICE_REFERENCE_WORDS = frozenset({"cheap", "cheaper", "cheapest", "affordable", "budget"})
_BOROUGH_REFERENCE_WORDS = frozenset(
    {"brooklyn", "manhattan", "queens", "bronx", "staten island"}
)
_PLACE_ID_PREFIX = "pl_"
_ALLOWED_RANKING_FACTORS = ("rating", "review_volume", "open_bonus", "price_level")

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
    """Map Google Places (New) priceLevel values to deterministic 0-4 ints.

    Full official enum strings (PRICE_LEVEL_FREE, PRICE_LEVEL_INEXPENSIVE,
    PRICE_LEVEL_MODERATE, PRICE_LEVEL_EXPENSIVE, PRICE_LEVEL_VERY_EXPENSIVE)
    map to 0-4; PRICE_LEVEL_UNSPECIFIED and unknown values become None. Short
    aliases (FREE, INEXPENSIVE, ...) remain backward-compatible. Numeric 0-4
    values are preserved so price references like "cheapest" work against
    real provider results instead of failing on enum strings.
    """

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
    """Strict allowlist for the compact ranking fields the model needs.

    Never passes through latitude, longitude, provider place ids, URLs, or
    arbitrary provider payload nested under ranking_factors.
    """

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
    """True only for finite real int/float values.

    Rejects arbitrary text, numeric strings, booleans, NaN, and infinities so
    numeric ranking and context fields never carry injected or non-numeric
    values into model-facing context.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number)


def _finite_optional(value: object) -> Any:
    """Pass through None and finite values; otherwise signal omission."""

    if value is None:
        return value
    return value if _is_finite_number(value) else _OMIT


_OMIT = object()


def _sanitized_option(place: dict[str, Any]) -> dict[str, Any]:
    """One model-facing discovery option with non-finite numerics omitted."""

    option = {
        "place_id": place.get("place_id"),
        "ordinal": place.get("ordinal"),
        "name": place.get("name"),
        "address": place.get("address"),
        "neighborhood": place.get("neighborhood"),
        "category": place.get("category"),
        "open_status": place.get("open_status"),
        "price_level": _finite_optional(place.get("price_level")),
        "rating": _finite_optional(place.get("rating")),
        "review_count": _finite_optional(place.get("review_count")),
        "baseline_score": _finite_optional(place.get("baseline_score")),
        "ranking_factors": sanitized_ranking_factors(place.get("ranking_factors")),
    }
    return {key: item for key, item in option.items() if item is not _OMIT}


def display_waypoint_labels(
    waypoints: list[str],
    *,
    session_id: str,
    discovery_set_id: str | None,
) -> list[str]:
    """Map opaque waypoint ids to stored display names; other entries pass through.

    Unresolvable ids are preserved so later preparation fails loudly instead of
    silently dropping a stop. Opaque ids that are resolvable never surface as
    rider-facing labels.
    """

    if not any(is_opaque_place_id(waypoint) for waypoint in waypoints):
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
    labels: list[str] = []
    for waypoint in waypoints:
        if is_opaque_place_id(waypoint) and waypoint in by_id:
            labels.append(str(by_id[waypoint].get("name") or waypoint))
        else:
            labels.append(waypoint)
    return labels


def _key(discovery_set_id: str) -> str:
    return f"{DISCOVERY_SET_PREFIX}{discovery_set_id}"


def store_discovery_set(
    *,
    session_id: str,
    places: list[dict[str, Any]],
    query: str = "",
    ttl_seconds: int = DEFAULT_TTL_S,
) -> str:
    set_id = new_discovery_set_id()
    now = time.time()
    normalized: list[dict[str, Any]] = []
    for index, place in enumerate(places[:MAX_PLACES]):
        if not isinstance(place, dict):
            continue
        place_id = new_place_id()
        normalized.append(
            {
                "place_id": place_id,
                "ordinal": index + 1,
                "name": str(place.get("name") or "")[:120],
                "address": str(place.get("address") or "")[:200],
                "neighborhood": str(place.get("neighborhood") or "")[:80],
                "category": str(place.get("category") or "")[:60],
                "open_status": place.get("open_status"),
                "price_level": normalize_price_level(place.get("price_level")),
                "rating": place.get("rating"),
                "review_count": place.get("review_count"),
                "baseline_score": place.get("baseline_score"),
                "ranking_factors": place.get("ranking_factors") or {},
                "latitude": place.get("latitude"),
                "longitude": place.get("longitude"),
                "provider_place_id": place.get("provider_place_id"),
                "transit_context": place.get("transit_context") or {},
            }
        )
    record = {
        "discovery_set_id": set_id,
        "session_id": session_id,
        "created_at": now,
        "expires_at": now + max(30, int(ttl_seconds)),
        "query": str(query or "")[:160],
        "places": normalized,
    }
    cache.cache_set(_key(set_id), json.dumps(record, separators=(",", ":"), default=str), int(ttl_seconds))
    return set_id


def load_discovery_set(discovery_set_id: str, *, session_id: str) -> dict[str, Any] | None:
    if not discovery_set_id or not session_id:
        return None
    raw = cache.cache_get(_key(discovery_set_id))
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


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _resolve_price_reference(places: list[dict[str, Any]], reference: str) -> tuple[dict[str, Any] | None, str | None]:
    """Cheapest finite known price level; ties and missing data fail safely."""

    known: list[tuple[dict[str, Any], float]] = []
    for place in places:
        price = _finite_price(place.get("price_level"))
        if price is not None:
            known.append((place, price))
    if not known:
        return None, "price information is unavailable for these places"
    lowest = min(price for _place, price in known)
    matches = [
        place for place, price in known if price == lowest
    ]
    if len(matches) == 1:
        return matches[0], None
    return None, "multiple places match that price reference"


def _finite_price(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _place_matches_borough(place: dict[str, Any], borough: str) -> bool:
    haystack = " ".join(
        _normalized_text(value)
        for value in (place.get("neighborhood"), place.get("address"))
        if value
    )
    return re.search(rf"\b{re.escape(borough)}\b", haystack) is not None


def _resolve_borough_reference(places: list[dict[str, Any]], borough: str) -> tuple[dict[str, Any] | None, str | None]:
    matches = [place for place in places if _place_matches_borough(place, borough)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "multiple places match that location reference"
    return None, "no place matches that location reference"


def _resolve_fragment_reference(places: list[dict[str, Any]], fragment: str) -> tuple[dict[str, Any] | None, str | None]:
    matches = [
        place
        for place in places
        if fragment in _normalized_text(place.get("name"))
        or fragment in _normalized_text(place.get("category"))
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "multiple places match that description"
    return None, "no place matches that description"


def _description_reference(description: str) -> str:
    """Normalize natural wording to a deterministic reference fragment.

    Strips leading articles ("the", "that") and trailing filler ("one",
    "place") so "the cheaper one", "the Brooklyn one", and "that pizza place"
    all reduce to a stable fragment. Matching stays exact-substring based;
    nothing is guessed or fuzzy-matched.
    """

    words = _normalized_text(description).split()
    if words and words[0] in ("the", "that"):
        words = words[1:]
    if words and words[-1] in ("one", "place"):
        words = words[:-1]
    return " ".join(words)


def _resolve_description(places: list[dict[str, Any]], description: str) -> tuple[dict[str, Any] | None, str | None]:
    reference = _description_reference(description)
    if not reference:
        return None, "place reference is incomplete"
    if reference in _PRICE_REFERENCE_WORDS:
        return _resolve_price_reference(places, reference)
    if reference in _BOROUGH_REFERENCE_WORDS:
        return _resolve_borough_reference(places, reference)
    return _resolve_fragment_reference(places, reference)


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
    places = [place for place in (record.get("places") or []) if isinstance(place, dict)]
    if place_id:
        for place in places:
            if str(place.get("place_id") or "") == place_id:
                return place, None
        return None, "place id is unknown for this discovery set"
    if ordinal is not None:
        for place in places:
            if int(place.get("ordinal") or 0) == int(ordinal):
                return place, None
        return None, "ordinal is out of range for this discovery set"
    if description is not None:
        return _resolve_description(places, description)
    return None, "place reference is incomplete"


def sanitized_discovery_context(
    session: dict | None,
    session_id: str,
) -> dict[str, Any] | None:
    """Compact, sanitized discovery metadata for the model's per-turn context.

    Never includes raw coordinates or provider place ids: the model must
    reference places by opaque place_id, ordinal, or deterministic
    description only.
    """

    from app.services.agent import trip_state as trip_state_module

    state = trip_state_module.get_trip_state(session)
    discovery_set_id = state.get("active_discovery_set_id")
    if not discovery_set_id:
        return None
    record = load_discovery_set(discovery_set_id, session_id=session_id)
    if record is None:
        return None
    options = [
        _sanitized_option(place)
        for place in (record.get("places") or [])
        if isinstance(place, dict) and place.get("place_id")
    ]
    return {
        "discovery_set_id": discovery_set_id,
        "query": record.get("query"),
        "selected_place_id": state.get("selected_place_id"),
        "options": options,
    }
