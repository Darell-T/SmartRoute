"""Small, bounded rider-text fallbacks for route tool turns."""

from __future__ import annotations

from collections.abc import Callable

from app.services.agent import events as agent_events


def terminal_route_text(
    result: object,
    *,
    route_card: agent_events.RouteCardEvent | None,
    route_card_text_fallback: Callable[[agent_events.RouteCardEvent], str],
    sanitize_rider_text: Callable[[str], str],
) -> str:
    data = getattr(result, "data", None)
    text = data.get("passenger_explanation") if isinstance(data, dict) else ""
    if not isinstance(text, str) or not text.strip():
        text = route_card_text_fallback(route_card) if route_card is not None else ""
    return sanitize_rider_text(str(text)).strip()


def fallback_route_acknowledgement(
    tool_use_blocks: list,
    *,
    sanitize_rider_text: Callable[[str], str],
) -> str | None:
    """Provide one safe acknowledgement when a route tool call has no prose."""

    for block in tool_use_blocks:
        if getattr(block, "name", "") not in {"plan_trip", "prepare_route_options"}:
            continue
        raw_input = getattr(block, "input", None)
        if not isinstance(raw_input, dict):
            continue
        destination_value = raw_input.get("destination")
        if not isinstance(destination_value, str):
            continue
        destination = sanitize_rider_text(" ".join(destination_value.split())).strip()
        if not destination:
            continue
        details: list[str] = []
        excluded = raw_input.get("exclude_modes")
        blocked_modes = {
            str(mode).strip().upper()
            for mode in (excluded if isinstance(excluded, (list, tuple, set)) else ())
        }
        if "BUS" in blocked_modes:
            details.append("without buses")
        if "SUBWAY" in blocked_modes:
            details.append("without subways")
        preference = str(raw_input.get("routing_preference") or "").upper()
        if preference == "FEWER_TRANSFERS":
            details.append("with fewer transfers")
        elif preference == "LESS_WALKING":
            details.append("with less walking")
        if raw_input.get("avoid_crowds") is True:
            details.append("using your crowd-avoidance preference")
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"I'll plan your trip to {destination}{suffix}."
    return None
