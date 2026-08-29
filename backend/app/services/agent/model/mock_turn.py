"""Deterministic local-development turn fixture for the conversational agent."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.services.agent import events as agent_events
from app.services.agent import session as session_module

if TYPE_CHECKING:
    from app.services.agent.turn.finalization import TurnTrace


def _mock_step_delay_s() -> float:
    try:
        return max(0.0, float(os.getenv("AGENT_MOCK_STEP_DELAY_MS", "280")) / 1000)
    except ValueError:
        return 0.28


def mock_trip_copy(
    message: str,
    response_presentation: str = "auto",
) -> tuple[str, dict, int, list[str]]:
    """Return stable preview content without inferring live service status."""
    query = message.casefold()
    if "costco" in query:
        text = (
            "Take the A train to Costco in this preview — about 34 minutes "
            "with no transfers."
            if response_presentation == "quick"
            else (
                "I'd take the A train to Costco in this preview. It is the best fit "
                "because it uses one train and keeps the final walk short with your cart."
            )
        )
        return (
            text,
            {"label": "Costco Sunset Park", "lat": 40.6559, "lng": -74.0089},
            34,
            ["A"],
        )
    if "pizza" in query:
        text = (
            "Take the N and Q in this preview — about 27 minutes with one transfer."
            if response_presentation == "quick"
            else (
                "I'd take the N and Q for this preview and stop for pizza near Midtown. "
                "I picked it because the sample itinerary keeps the transfer count low."
            )
        )
        destination = {
            "label": "Pizza stop near Midtown",
            "lat": 40.7549,
            "lng": -73.9840,
        }
        return text, destination, 27, ["N", "Q"]
    return (
        "I'm showing a simulated transit option so you can preview the chat experience. "
        "Switch off mock mode when you're ready to use live route data.",
        {"label": "Demo destination", "lat": 40.7306, "lng": -73.9866},
        22,
        ["Q"],
    )


def _mock_token_chunks(text: str) -> list[str]:
    words = text.split(" ")
    return [
        ("" if index == 0 else " ") + " ".join(words[index : index + 3])
        for index in range(0, len(words), 3)
    ]


async def stream_mock_turn(
    *,
    session: dict,
    session_id: str,
    turn_id: str,
    message: str,
    origin: dict | None,
    trace: TurnTrace | None,
    response_presentation: str,
) -> AsyncIterator[agent_events.AgentEvent]:
    """Stream a deterministic local preview without model or provider work."""
    delay_s = _mock_step_delay_s()
    text, destination, eta_minutes, lines = mock_trip_copy(
        message, response_presentation
    )
    mock_origin = {
        "label": "Your location",
        "lat": float((origin or {}).get("lat", 40.7484)),
        "lng": float((origin or {}).get("lng", -73.9857)),
    }
    tool_calls = [("prepare_route_options", {"destination": destination["label"]})]

    session_module.append_history(session, "user", message, turn_id=turn_id)
    yield agent_events.ToolStartEvent(
        tool_call_id=f"mock-route-{turn_id}",
        tool="prepare_route_options",
        label="Previewing a cart-friendly subway route…",
    )
    await asyncio.sleep(delay_s)
    yield agent_events.ToolEndEvent(
        tool_call_id=f"mock-route-{turn_id}",
        tool="prepare_route_options",
        ok=True,
        duration_ms=round(delay_s * 1000),
        summary="Preview route ready",
    )
    yield agent_events.ToolStartEvent(
        tool_call_id=f"mock-service-{turn_id}",
        tool="transit_snapshot",
        label="Loading simulated service conditions…",
    )
    await asyncio.sleep(delay_s)
    yield agent_events.ToolEndEvent(
        tool_call_id=f"mock-service-{turn_id}",
        tool="transit_snapshot",
        ok=True,
        duration_ms=round(delay_s * 1000),
        summary="Preview data only",
    )
    for chunk in _mock_token_chunks(text):
        await asyncio.sleep(min(0.06, delay_s))
        yield agent_events.TokenEvent(text=chunk)

    card_id = f"mock-{turn_id}"
    route_card = agent_events.RouteCardEvent(
        card_id=card_id,
        turn_id=turn_id,
        role="recommended",
        origin=mock_origin,
        destination=destination,
        summary={
            "eta_minutes": eta_minutes,
            "transfers": max(0, len(lines) - 1),
            "lines": lines,
            "reason": "A simple sample route with a short final walk.",
        },
        route=[],
        alerts=[],
    )
    yield route_card
    session_module.add_visible_events(session, [route_card])
    session_module.append_history(session, "assistant", text, turn_id=turn_id)
    session_module.append_tool_summary(
        session, "mock_agent", "served deterministic preview data"
    )
    session_module.add_route_cards(
        session,
        [
            {
                "card_id": card_id,
                "role": "recommended",
                "lines": lines,
                "eta_minutes": eta_minutes,
            }
        ],
    )
    if trace is not None:
        trace.tool_calls = tool_calls
        trace.final_text = text

    yield agent_events.DoneEvent(
        session_id=session_id,
        turn_id=turn_id,
        stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


__all__ = ("mock_trip_copy", "stream_mock_turn")
