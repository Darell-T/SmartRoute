"""Small, explicit session-scoped profile fallback for conversational turns."""

from __future__ import annotations

from typing import Any

MAX_SAVED_PLACES = 8
MAX_FREQUENT_PLACES = 8
_PREFERENCE_KEYS = (
    "avoid_stairs",
    "avoid_crowds",
    "prefer_fewer_transfers",
    "walking_preference",
    "walking_tolerance_minutes",
    "preferred_modes",
    "accessibility_required",
)


def default_preferences() -> dict[str, Any]:
    return {
        "avoid_stairs": False,
        "avoid_crowds": False,
        "prefer_fewer_transfers": False,
        "walking_preference": "any",
        "walking_tolerance_minutes": None,
        "preferred_modes": [],
        "accessibility_required": False,
    }


def empty_profile() -> dict[str, Any]:
    return {
        "places": {"home": None, "work": None},
        "saved_places": [],
        "frequent_places": [],
        "preferences": default_preferences(),
    }


def get_profile(session: dict | None) -> dict[str, Any]:
    if not isinstance(session, dict):
        return empty_profile()
    profile = normalize_profile(session.get("profile"))
    session["profile"] = profile
    return profile


def save_place(
    session: dict,
    place: dict[str, Any],
    *,
    slot: str | None = None,
    frequent: bool = False,
) -> dict[str, Any]:
    normalized = normalize_place(place)
    if normalized is None:
        return get_profile(session)
    profile = get_profile(session)
    if slot in {"home", "work"}:
        profile["places"][slot] = normalized
    else:
        key = "frequent_places" if frequent else "saved_places"
        values = [
            value
            for value in profile[key]
            if not _same_place(value, normalized)
        ]
        profile[key] = [normalized, *values][:_limit_for(key)]
    session["profile"] = normalize_profile(profile)
    return session["profile"]


def profile_place(session: dict | None, label: object) -> dict[str, Any] | None:
    place, _error = resolve_profile_place(session, label)
    return place


def resolve_profile_place(
    session: dict | None, label: object
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve one canonical saved place or reject an ambiguous label.

    The explicit Home/Work slot names are authoritative. Other label-based
    references must identify exactly one distinct stored place; duplicate
    copies of the same identity are harmless, while distinct identities with
    the same label require rider clarification instead of first-match routing.
    """

    query = " ".join(str(label or "").casefold().split())
    if not query:
        return None, None
    profile = get_profile(session)
    places = profile.get("places") or {}
    if query in {"home", "work"}:
        place = places.get(query)
        return (dict(place), None) if isinstance(place, dict) else (None, None)

    matches = [
        dict(place)
        for place in places.values()
        if isinstance(place, dict) and query == _place_key(place)
    ]
    matches.extend(
        dict(place)
        for key in ("saved_places", "frequent_places")
        for place in profile.get(key) or []
        if isinstance(place, dict) and query == _place_key(place)
    )
    unique: dict[tuple[object, ...], dict[str, Any]] = {}
    for place in matches:
        place_id = str(place.get("place_id") or "").strip()
        identity = (
            ("place_id", place_id)
            if place_id
            else (
                "coordinates",
                float(place["latitude"]),
                float(place["longitude"]),
            )
        )
        unique.setdefault(identity, place)
    if len(unique) > 1:
        return None, "saved place reference is ambiguous"
    return (dict(next(iter(unique.values()))), None) if unique else (None, None)


def update_preferences(session: dict, patch: dict[str, Any]) -> dict[str, Any]:
    profile = get_profile(session)
    preferences = dict(profile.get("preferences") or default_preferences())
    preferences.update(_validated_preferences(patch))
    profile["preferences"] = normalize_preferences(preferences)
    session["profile"] = profile
    return profile


def normalize_profile(raw: object) -> dict[str, Any]:
    profile = empty_profile()
    if not isinstance(raw, dict):
        return profile
    places = raw.get("places")
    if isinstance(places, dict):
        for slot in ("home", "work"):
            profile["places"][slot] = normalize_place(places.get(slot))
    for key in ("saved_places", "frequent_places"):
        values = raw.get(key)
        if isinstance(values, list):
            profile[key] = [
                place
                for place in (normalize_place(value) for value in values)
                if place is not None
            ][:_limit_for(key)]
    profile["preferences"] = normalize_preferences(raw.get("preferences"))
    return profile


def normalize_preferences(raw: object) -> dict[str, Any]:
    defaults = default_preferences()
    if not isinstance(raw, dict):
        return defaults
    defaults.update(_validated_preferences(raw))
    return defaults


def normalize_place(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label") or raw.get("name") or raw.get("address") or "").strip()
    if not label or len(label) > 160:
        return None
    try:
        latitude = float(raw["latitude"] if "latitude" in raw else raw["lat"])
        longitude = float(raw["longitude"] if "longitude" in raw else raw["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return {
        "label": label[:160],
        "address": str(raw.get("address") or "").strip()[:200] or None,
        "latitude": latitude,
        "longitude": longitude,
        "place_id": str(raw.get("place_id") or "").strip()[:160] or None,
    }


def _validated_preferences(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("avoid_stairs", "avoid_crowds", "prefer_fewer_transfers", "accessibility_required"):
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    preference = raw.get("walking_preference")
    if preference in {"any", "less_walking"}:
        result["walking_preference"] = preference
    tolerance = raw.get("walking_tolerance_minutes")
    if tolerance is None:
        result["walking_tolerance_minutes"] = None
    elif isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool):
        result["walking_tolerance_minutes"] = max(0, min(180, round(tolerance)))
    modes = raw.get("preferred_modes")
    if isinstance(modes, list):
        result["preferred_modes"] = [
            str(mode).strip().upper()
            for mode in modes
            if str(mode).strip().upper() in {"SUBWAY", "BUS", "RAIL"}
        ][:6]
    if result.get("avoid_stairs") is True:
        result["accessibility_required"] = True
    return result


def _same_place(left: object, right: dict[str, Any]) -> bool:
    if not isinstance(left, dict):
        return False
    left_id = str(left.get("place_id") or "").strip()
    right_id = str(right.get("place_id") or "").strip()
    if left_id and right_id:
        return left_id == right_id
    return _place_key(left) == _place_key(right)


def _place_key(place: dict[str, Any]) -> str:
    return " ".join(str(place.get("label") or "").casefold().split())


def _limit_for(key: str) -> int:
    return MAX_FREQUENT_PLACES if key == "frequent_places" else MAX_SAVED_PLACES


__all__ = (
    "default_preferences",
    "empty_profile",
    "get_profile",
    "normalize_place",
    "normalize_preferences",
    "profile_place",
    "resolve_profile_place",
    "save_place",
    "update_preferences",
)
