"""Normalize request/session input for the prepare_route_options tool."""

from __future__ import annotations

from typing import Any

from app.services.agent import intelligence
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext


def merge_route_preparation_input(tool_input: dict, ctx: ToolContext) -> dict:
    """Merge server-owned trip/profile state into one canonical route input.

    Inherits trip-state origin/destination/waypoints, routing preferences,
    and departure/arrival targets; normalizes mode and route exclusions; and
    derives the active versus what-if scenario. The active preference patch
    must be applied at this exact point in execution, before destination
    resolution, so later what-if logic sees the merged session preferences.
    """
    session = ctx.session if isinstance(ctx.session, dict) else {}
    state = trip_state_module.get_trip_state(session)
    preferences = state.get("preferences") or {}
    destination = str(tool_input.get("destination") or state.get("destination") or "").strip()
    origin = str(tool_input.get("origin") or state.get("origin") or "user").strip() or "user"
    waypoints = tool_input.get("waypoints")
    if not isinstance(waypoints, list):
        waypoints = list(state.get("waypoints") or [])
    waypoints = [str(value).strip() for value in waypoints if str(value).strip()]
    exclude_modes = [
        str(value).strip().upper()
        for value in tool_input.get("exclude_modes") or []
        if str(value).strip().upper() in {"BUS", "SUBWAY", "RAIL"}
    ]
    excluded_route_ids = intelligence.normalize_route_ids(
        tool_input.get("excluded_route_ids") or []
    )
    routing_preference = tool_input.get("routing_preference")
    if routing_preference not in {"FEWER_TRANSFERS", "LESS_WALKING"}:
        routing_preference = None
    if not routing_preference:
        routing_preference = (
            "LESS_WALKING"
            if preferences.get("walking_preference") == "less_walking"
            else "FEWER_TRANSFERS"
        )
    preferred_modes = tool_input.get("preferred_modes")
    if not isinstance(preferred_modes, list):
        preferred_modes = list(preferences.get("preferred_modes") or [])
    avoid_crowds = _explicit_bool(tool_input, "avoid_crowds", preferences)
    avoid_stairs = _explicit_bool(tool_input, "avoid_stairs", preferences)
    accessibility_required = (
        _explicit_bool(tool_input, "accessibility_required", preferences)
        if "accessibility_required" in tool_input
        else bool(preferences.get("accessibility_required"))
    )
    if avoid_stairs:
        accessibility_required = True
    merged: dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "waypoints": waypoints,
        "exclude_modes": exclude_modes,
        "excluded_route_ids": list(excluded_route_ids),
        "preferred_modes": [
            mode
            for mode in (str(value).strip().upper() for value in preferred_modes)
            if mode in {"BUS", "SUBWAY", "RAIL"}
        ][:6],
        "routing_preference": routing_preference,
        "avoid_crowds": avoid_crowds,
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
    if not merged.get("departure_time") and state.get("requested_departure"):
        merged["departure_time"] = state["requested_departure"]
    if not merged.get("arrival_by") and state.get("requested_arrival"):
        merged["arrival_by"] = state["requested_arrival"]
    if merged.get("walking_tolerance_minutes") is None:
        merged["walking_tolerance_minutes"] = preferences.get("walking_tolerance_minutes")
    merged["scenario"] = (
        "what_if"
        if bool(tool_input.get("what_if")) or tool_input.get("scenario") == "what_if"
        else "active"
    )
    merged["preference_patch"] = trip_state_module.preference_patch_from_tool_input(
        tool_input
    )
    if isinstance(ctx.session, dict):
        if merged["scenario"] == "active" and merged["preference_patch"]:
            trip_state_module.apply_preference_patch(
                ctx.session,
                merged["preference_patch"],
            )
    return merged


def _explicit_bool(tool_input: dict, key: str, preferences: dict[str, Any]) -> bool:
    if key in tool_input:
        return tool_input[key] is True
    return bool(preferences.get(key))
