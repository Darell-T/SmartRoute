"""Validated, session-owned conversational trip and scenario state."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Literal

from app.services.agent import profile as profile_module
from app.services.trips.preparation.input import normalize_route_ids

PlanningMode = Literal["leave_now", "depart_at", "arrive_by"]
MAX_WAYPOINTS = 3
MAX_WAYPOINT_CHARS = 160

_DEFAULT_PREFERENCES: dict[str, Any] = profile_module.default_preferences()
_EMPTY_STATE: dict[str, Any] = {
    "origin": None,
    "destination": None,
    "waypoints": [],
    "planning_mode": "leave_now",
    "requested_departure": None,
    "requested_arrival": None,
    "preferences": dict(_DEFAULT_PREFERENCES),
    "active_candidate_set_id": None,
    "selected_candidate_id": None,
    "temporary_candidate_set_id": None,
    "temporary_selected_candidate_id": None,
    "temporary_base_candidate_set_id": None,
    "active_discovery_set_id": None,
    "selected_place_id": None,
}


def empty_trip_state(preferences: dict[str, Any] | None = None) -> dict[str, Any]:
    state = dict(_EMPTY_STATE)
    state["preferences"] = profile_module.normalize_preferences(
        preferences if isinstance(preferences, dict) else _DEFAULT_PREFERENCES
    )
    state["waypoints"] = []
    return state


def get_trip_state(session: dict | None) -> dict[str, Any]:
    """Return normalized state, creating it from explicit profile defaults."""

    if not isinstance(session, dict):
        return empty_trip_state()
    profile = profile_module.get_profile(session)
    raw = session.get("trip_state")
    if not isinstance(raw, dict):
        state = empty_trip_state(profile.get("preferences"))
        session["trip_state"] = state
        return state
    return _normalize(raw, profile.get("preferences"))


def save_trip_state(session: dict, state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(
        state,
        profile_module.get_profile(session).get("preferences"),
    )
    session["trip_state"] = normalized
    return normalized


def update_trip_state(session: dict, **updates: Any) -> dict[str, Any]:
    state = get_trip_state(session)
    for key, value in updates.items():
        if key == "preferences" and isinstance(value, dict):
            merged = dict(state.get("preferences") or _DEFAULT_PREFERENCES)
            merged.update(value)
            state["preferences"] = profile_module.normalize_preferences(merged)
        elif key in _EMPTY_STATE:
            state[key] = value
    return save_trip_state(session, state)


def apply_preference_patch(session: dict, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge only bounded, validated preference values into active trip state."""

    state = get_trip_state(session)
    merged = dict(state.get("preferences") or _DEFAULT_PREFERENCES)
    merged.update(patch)
    state["preferences"] = profile_module.normalize_preferences(merged)
    return save_trip_state(session, state)


def preference_patch_from_tool_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Extract only explicit, bounded preference changes from route input."""

    patch = {
        key: tool_input[key]
        for key in (
            "avoid_stairs",
            "avoid_crowds",
            "accessibility_required",
            "walking_tolerance_minutes",
            "preferred_modes",
        )
        if key in tool_input
    }
    routing_preference = str(tool_input.get("routing_preference") or "").upper()
    if routing_preference == "LESS_WALKING":
        patch["walking_preference"] = "less_walking"
        patch["prefer_fewer_transfers"] = False
    elif routing_preference == "FEWER_TRANSFERS":
        patch["prefer_fewer_transfers"] = True
        patch["walking_preference"] = "any"
    if tool_input.get("avoid_stairs") is True:
        patch["accessibility_required"] = True
    return patch


def set_destination(session: dict, destination: str) -> dict[str, Any]:
    return update_trip_state(
        session,
        destination=str(destination or "").strip() or None,
        selected_candidate_id=None,
        active_candidate_set_id=None,
    )


def set_origin(session: dict, origin: str) -> dict[str, Any]:
    return update_trip_state(session, origin=str(origin or "").strip() or None)


def replace_waypoints(session: dict, waypoints: list[str]) -> dict[str, Any]:
    cleaned = [
        point.strip()
        for point in waypoints
        if isinstance(point, str) and point.strip() and len(point.strip()) <= MAX_WAYPOINT_CHARS
    ][:MAX_WAYPOINTS]
    return update_trip_state(session, waypoints=cleaned)


def add_waypoint_before_destination(session: dict, place_label: str) -> dict[str, Any]:
    label = str(place_label or "").strip()
    if not label:
        return get_trip_state(session)
    state = get_trip_state(session)
    waypoints = list(state.get("waypoints") or [])
    if len(label) > MAX_WAYPOINT_CHARS:
        return get_trip_state(session)
    if label not in waypoints and len(waypoints) < MAX_WAYPOINTS:
        waypoints.append(label)
    return update_trip_state(session, waypoints=waypoints)


def set_planning_time(
    session: dict,
    *,
    planning_mode: PlanningMode,
    requested_departure: str | None = None,
    requested_arrival: str | None = None,
) -> dict[str, Any]:
    return update_trip_state(
        session,
        planning_mode=planning_mode,
        requested_departure=requested_departure,
        requested_arrival=requested_arrival,
    )


def bind_candidate_set(session: dict, candidate_set_id: str) -> dict[str, Any]:
    return update_trip_state(
        session,
        active_candidate_set_id=candidate_set_id,
        selected_candidate_id=None,
    )


def bind_selected_candidate(session: dict, candidate_id: str) -> dict[str, Any]:
    return update_trip_state(session, selected_candidate_id=candidate_id)


def bind_temporary_candidate_set(
    session: dict,
    candidate_set_id: str,
    *,
    base_candidate_set_id: str | None = None,
) -> dict[str, Any]:
    state = get_trip_state(session)
    state["temporary_candidate_set_id"] = str(candidate_set_id or "").strip() or None
    state["temporary_selected_candidate_id"] = None
    state["temporary_base_candidate_set_id"] = (
        str(base_candidate_set_id or "").strip()
        or state.get("active_candidate_set_id")
    )
    return save_trip_state(session, state)


def bind_temporary_selected_candidate(session: dict, candidate_id: str) -> dict[str, Any]:
    return update_trip_state(session, temporary_selected_candidate_id=candidate_id)


def bind_discovery_set(session: dict, discovery_set_id: str) -> dict[str, Any]:
    return update_trip_state(
        session,
        active_discovery_set_id=discovery_set_id,
        selected_place_id=None,
    )


def bind_discovery_context(
    session: dict,
    *,
    discovery_set_id: str,
    selected_place_id: str | None = None,
) -> dict[str, Any]:
    """Atomically bind an active discovery set and its resolved selected place.

    Callers must pass ids that were already validated through
    ``discovery_store.resolve_place_reference`` (session-owned, unexpired) so
    this never activates an invented, cross-session, or expired set. Binding
    both in one write keeps the active set and the selected place mutually
    consistent (``bind_discovery_set`` resets the place and
    ``bind_selected_place`` updates it alone).
    """

    return update_trip_state(
        session,
        active_discovery_set_id=str(discovery_set_id or "").strip() or None,
        selected_place_id=str(selected_place_id or "").strip() or None,
    )


def bind_selected_place(session: dict, place_id: str) -> dict[str, Any]:
    return update_trip_state(session, selected_place_id=place_id)


def clear_route_selection(session: dict) -> dict[str, Any]:
    return update_trip_state(
        session,
        active_candidate_set_id=None,
        selected_candidate_id=None,
    )


def discard_scenario(session: dict) -> dict[str, Any]:
    state = get_trip_state(session)
    state["temporary_candidate_set_id"] = None
    state["temporary_selected_candidate_id"] = None
    state["temporary_base_candidate_set_id"] = None
    return save_trip_state(session, state)


def commit_scenario(
    session: dict,
    *,
    candidate_set_id: str,
    candidate_id: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    state = get_trip_state(session)
    if tool_input.get("origin"):
        state["origin"] = str(tool_input["origin"]).strip()
    if tool_input.get("destination"):
        state["destination"] = str(tool_input["destination"]).strip()
    if isinstance(tool_input.get("waypoints"), list):
        state["waypoints"] = [
            str(value).strip()
            for value in tool_input["waypoints"]
            if str(value).strip()
        ][:3]
    preference_patch = tool_input.get("preference_patch")
    if not isinstance(preference_patch, dict):
        preference_patch = preference_patch_from_tool_input(tool_input)
    if preference_patch:
        profile_module.update_preferences(session, preference_patch)
        state["preferences"] = profile_module.normalize_preferences(
            {**(state.get("preferences") or {}), **preference_patch}
        )
    excluded_route_ids = normalize_route_ids(
        tool_input.get("excluded_route_ids") or []
    )
    if excluded_route_ids:
        # A committed what-if makes its temporary route exclusion active
        # through the same server-owned slots.constraints convention as
        # exclude_modes, so follow-up turns keep enforcing it.
        slots = session.setdefault("slots", {}).setdefault("constraints", {})
        slots["excluded_route_ids"] = list(excluded_route_ids)
    state.update(
        {
            "active_candidate_set_id": candidate_set_id,
            "selected_candidate_id": candidate_id,
            "planning_mode": (
                "arrive_by"
                if tool_input.get("arrival_by")
                else "depart_at"
                if tool_input.get("departure_time")
                else "leave_now"
            ),
            "requested_departure": _optional_str(tool_input.get("departure_time")),
            "requested_arrival": _optional_str(tool_input.get("arrival_by")),
        }
    )
    state["temporary_candidate_set_id"] = None
    state["temporary_selected_candidate_id"] = None
    state["temporary_base_candidate_set_id"] = None
    return save_trip_state(session, state)


def reset_for_new_trip(
    session: dict,
    *,
    preserve_active_discovery_set: bool = False,
) -> dict[str, Any]:
    """Clear route/scenario fields while retaining history and profile defaults.

    ``preserve_active_discovery_set`` keeps only the currently bound
    server-owned active discovery set id so an unambiguous discovery-result
    handoff (e.g. "Take me to the second one.") can still resolve against the
    real store on the next turn; every other route field -- including the
    selected place -- is cleared exactly as the ordinary reset does.
    """

    profile = profile_module.get_profile(session)
    state = empty_trip_state(profile.get("preferences"))
    if preserve_active_discovery_set:
        state["active_discovery_set_id"] = get_trip_state(session).get(
            "active_discovery_set_id"
        )
    session["trip_state"] = state
    return state


def _normalize(
    raw: dict[str, Any],
    profile_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode = str(raw.get("planning_mode") or "leave_now")
    if mode not in {"leave_now", "depart_at", "arrive_by"}:
        mode = "leave_now"
    requested_departure = _optional_timestamp(raw.get("requested_departure"))
    requested_arrival = _optional_timestamp(raw.get("requested_arrival"))
    if mode == "depart_at" and requested_departure is None:
        mode = "leave_now"
    if mode == "arrive_by" and requested_arrival is None:
        mode = "leave_now"
    try:
        updated_at = float(raw.get("updated_at") or time.time())
    except (TypeError, ValueError):
        updated_at = time.time()
    waypoints = raw.get("waypoints")
    base_preferences = (
        profile_preferences
        if isinstance(profile_preferences, dict)
        else _DEFAULT_PREFERENCES
    )
    preferences = dict(base_preferences)
    if isinstance(raw.get("preferences"), dict):
        preferences.update(raw["preferences"])
    return {
        "origin": _optional_str(raw.get("origin")),
        "destination": _optional_str(raw.get("destination")),
        "waypoints": (
            [
                item.strip()
                for item in waypoints
                if isinstance(item, str)
                and item.strip()
                and len(item.strip()) <= MAX_WAYPOINT_CHARS
            ][:MAX_WAYPOINTS]
            if isinstance(waypoints, list)
            else []
        ),
        "planning_mode": mode,
        "requested_departure": requested_departure,
        "requested_arrival": requested_arrival,
        "preferences": profile_module.normalize_preferences(preferences),
        "active_candidate_set_id": _optional_str(raw.get("active_candidate_set_id")),
        "selected_candidate_id": _optional_str(raw.get("selected_candidate_id")),
        "temporary_candidate_set_id": _optional_str(raw.get("temporary_candidate_set_id")),
        "temporary_selected_candidate_id": _optional_str(raw.get("temporary_selected_candidate_id")),
        "temporary_base_candidate_set_id": _optional_str(raw.get("temporary_base_candidate_set_id")),
        "active_discovery_set_id": _optional_str(raw.get("active_discovery_set_id")),
        "selected_place_id": _optional_str(raw.get("selected_place_id")),
        "updated_at": updated_at,
    }


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_timestamp(value: object) -> str | None:
    text = _optional_str(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return text if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


__all__ = (
    "PlanningMode",
    "apply_preference_patch",
    "bind_candidate_set",
    "bind_discovery_context",
    "bind_discovery_set",
    "bind_selected_candidate",
    "bind_selected_place",
    "bind_temporary_candidate_set",
    "bind_temporary_selected_candidate",
    "clear_route_selection",
    "commit_scenario",
    "discard_scenario",
    "empty_trip_state",
    "get_trip_state",
    "preference_patch_from_tool_input",
    "reset_for_new_trip",
    "save_trip_state",
    "set_destination",
    "set_origin",
)
