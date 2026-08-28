"""Agent adapter for route input and session/profile preference composition."""

from __future__ import annotations

from typing import Any

from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext
from app.services.trips.location import ResolvedPlace
from app.services.trips.preparation.input import (
    derive_arrive_by_departure,
    normalize_route_ids,
    parse_rfc3339,
    point_label,
    prepare_structural_candidates,
    recover_structural_route,
    route_with_recovery,
    summary_eta_minutes,
    validated_waypoints,
)

_RIDER_LOCATION_REFERENCES = {"user", "your location", "current location"}


def _canonical_origin_reference(raw_value: object) -> str:
    value = str(raw_value or "").strip()
    if not value or value.casefold() in _RIDER_LOCATION_REFERENCES:
        return "user"
    return value


def merge_route_preparation_input(tool_input: dict, ctx: ToolContext) -> dict:
    """Merge server-owned trip/profile state into one canonical route input."""

    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    preferences = state.get("preferences") or {}
    destination_source = str(tool_input.get("destination_source") or "").strip()
    destination = str(tool_input.get("destination") or "").strip()
    if not destination and destination_source == "accepted_trip":
        destination = str(state.get("destination") or "").strip()
    requested_preference = tool_input.get("routing_preference")
    if requested_preference in {"FEWER_TRANSFERS", "LESS_WALKING"}:
        routing_preference, routing_preference_source = requested_preference, "current_turn"
    elif preferences.get("walking_preference") == "less_walking":
        routing_preference, routing_preference_source = "LESS_WALKING", "persisted_rider"
    elif preferences.get("prefer_fewer_transfers") is True:
        routing_preference, routing_preference_source = "FEWER_TRANSFERS", "persisted_rider"
    else:
        routing_preference, routing_preference_source = "FEWER_TRANSFERS", "default"
    avoid_crowds = _explicit_bool(tool_input, "avoid_crowds", preferences)
    avoid_crowds_source = (
        "current_turn"
        if "avoid_crowds" in tool_input
        else "persisted_rider"
        if preferences.get("avoid_crowds") is True
        else "default"
    )
    avoid_stairs = _explicit_bool(tool_input, "avoid_stairs", preferences)
    accessibility_required = (
        _explicit_bool(tool_input, "accessibility_required", preferences)
        if "accessibility_required" in tool_input
        else bool(preferences.get("accessibility_required"))
    )
    accessibility_required |= avoid_stairs
    merged: dict[str, Any] = {
        "origin": _canonical_origin_reference(
            tool_input.get("origin") or state.get("origin") or "user"
        ),
        "destination": destination,
        "destination_source": destination_source,
        "waypoints": [
            str(value).strip()
            for value in (
                tool_input.get("waypoints")
                if isinstance(tool_input.get("waypoints"), list)
                else state.get("waypoints") or []
            )
            if str(value).strip()
        ],
        **_mode_preferences(tool_input, preferences),
        "routing_preference": routing_preference,
        "routing_preference_source": routing_preference_source,
        "avoid_crowds": avoid_crowds,
        "avoid_crowds_source": avoid_crowds_source,
        "avoid_stairs": avoid_stairs,
        "accessibility_required": accessibility_required,
    }
    for key in (
        "departure_time",
        "arrival_by",
        "waypoint_dwell_minutes",
        "walking_tolerance_minutes",
        "max_walking_minutes",
        "max_candidates",
        "required_route_ids",
        "crowd_search_mode",
        "include_first_leg_arrivals",
    ):
        if tool_input.get(key) is not None:
            merged[key] = tool_input[key]
    _apply_persisted_trip_constraints(merged, state, preferences)
    merged["scenario"] = (
        "what_if"
        if bool(tool_input.get("what_if")) or tool_input.get("scenario") == "what_if"
        else "active"
    )
    merged["preference_patch"] = trip_state_module.preference_patch_from_tool_input(
        tool_input
    )
    _apply_active_preference_patch(ctx, merged)
    return merged


def _apply_active_preference_patch(ctx: ToolContext, merged: dict[str, Any]) -> None:
    if not isinstance(ctx.session, dict):
        return
    if merged["scenario"] != "active":
        return
    if not merged["preference_patch"]:
        return
    trip_state_module.apply_preference_patch(
        ctx.session,
        merged["preference_patch"],
    )


def _apply_persisted_trip_constraints(
    merged: dict[str, Any],
    state: dict[str, Any],
    preferences: dict[str, Any],
) -> None:
    if not merged.get("departure_time") and state.get("requested_departure"):
        merged["departure_time"] = state["requested_departure"]
    if not merged.get("arrival_by") and state.get("requested_arrival"):
        merged["arrival_by"] = state["requested_arrival"]
    if merged.get("walking_tolerance_minutes") is None:
        merged["walking_tolerance_minutes"] = preferences.get(
            "walking_tolerance_minutes"
        )


def _mode_preferences(
    tool_input: dict[str, Any], preferences: dict[str, Any]
) -> dict[str, list[str]]:
    allowed_modes = {"BUS", "SUBWAY", "RAIL"}
    excluded_modes = [
        str(value).strip().upper()
        for value in tool_input.get("exclude_modes") or []
        if str(value).strip().upper() in allowed_modes
    ]
    preferred_modes = tool_input.get("preferred_modes")
    if not isinstance(preferred_modes, list):
        preferred_modes = list(preferences.get("preferred_modes") or [])
    return {
        "exclude_modes": excluded_modes,
        "excluded_route_ids": list(
            normalize_route_ids(tool_input.get("excluded_route_ids") or [])
        ),
        "preferred_modes": [
            mode
            for mode in (str(value).strip().upper() for value in preferred_modes)
            if mode in allowed_modes
        ][:6],
    }


def _explicit_bool(tool_input: dict, key: str, preferences: dict[str, Any]) -> bool:
    if key in tool_input:
        return tool_input[key] is True
    return bool(preferences.get(key))


__all__ = (
    "ResolvedPlace",
    "derive_arrive_by_departure",
    "merge_route_preparation_input",
    "parse_rfc3339",
    "point_label",
    "prepare_structural_candidates",
    "recover_structural_route",
    "route_with_recovery",
    "summary_eta_minutes",
    "validated_waypoints",
)
