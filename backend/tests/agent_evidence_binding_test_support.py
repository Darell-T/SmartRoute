"""Shared builders for canonical transit evidence reliability tests."""

from __future__ import annotations


def transit_input(**overrides: object) -> dict[str, object]:
    """Build a complete strict check_transit payload for direct execution."""

    payload: dict[str, object] = {
        "operation": "accessibility",
        "route_ids": [],
        "stop_source": "auto",
        "stop_query": None,
        "direction": None,
        "area": None,
        "station": None,
        "station_source": "auto",
        "topic": None,
        "event_query": None,
        "venue": None,
        "at": None,
        "window_start": None,
        "window_end": None,
        "concerns": [],
        "goal_key": "accessibility",
        "activity_label": None,
    }
    payload.update(overrides)
    return payload
