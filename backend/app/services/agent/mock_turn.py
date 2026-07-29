"""Deterministic local-development turn fixture for the conversational agent."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.services.agent import events as agent_events
from app.services.agent import session as session_module

if TYPE_CHECKING:
    from app.services.agent.loop import TurnTrace


def mock_step_delay_s() -> float:
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
            "Take the A train to Costco in this preview \u2014 about 34 minutes "
            "with no transfers."
            if response_presentation == "quick"
            else (
                "I'd take the A train to Costco in this preview. It is the best fit "
                "because it uses one train and keeps the final walk short with your cart."
            )
        )
        return text, {"label": "Costco Sunset Park", "lat": 40.6559, "lng": -74.0089}, 34, ["A"]
    if "pizza" in query:
        text = (
            "Take the N and Q in this preview \u2014 about 27 minutes with one transfer."
            if response_presentation == "quick"
            else (
                "I'd take the N and Q for this preview and stop for pizza near Midtown. "
                "I picked it because the sample itinerary keeps the transfer count low."
            )
        )
        return text, {"label": "Pizza stop near Midtown", "lat": 40.7549, "lng": -73.9840}, 27, ["N", "Q"]
    return (
        "I\u2019m showing a simulated transit option so you can preview the chat experience. "
        "Switch off mock mode when you\u2019re ready to use live route data.",
        {"label": "Demo destination", "lat": 40.7306, "lng": -73.9866},
        22,
        ["Q"],
    )


def mock_token_chunks(text: str) -> list[str]:
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
    """Stream a small deterministic fixture without model or provider work."""
    started_at = time.monotonic()
    delay_s = mock_step_delay_s()
    text, destination, eta_minutes, lines = mock_trip_copy(message, response_presentation)
    mock_origin = {
        "label": "Your location",
        "lat": float((origin or {}).get("lat", 40.7484)),
        "lng": float((origin or {}).get("lng", -73.9857)),
    }
    tool_calls = [("mock_plan_trip", {"destination": destination["label"]})]

    session_module.append_history(session, "user", message)
    yield agent_events.ToolStartEvent(
        tool_call_id=f"mock-route-{turn_id}", tool="plan_trip", label="Previewing a cart-friendly subway route\u2026"
    )
    await asyncio.sleep(delay_s)
    yield agent_events.ToolEndEvent(
        tool_call_id=f"mock-route-{turn_id}", tool="plan_trip", ok=True,
        duration_ms=round(delay_s * 1000), summary="Preview route ready",
    )
    yield agent_events.ToolStartEvent(
        tool_call_id=f"mock-service-{turn_id}", tool="transit_snapshot", label="Loading simulated service conditions\u2026"
    )
    await asyncio.sleep(delay_s)
    yield agent_events.ToolEndEvent(
        tool_call_id=f"mock-service-{turn_id}", tool="transit_snapshot", ok=True,
        duration_ms=round(delay_s * 1000), summary="Preview data only",
    )
    for chunk in mock_token_chunks(text):
        await asyncio.sleep(min(0.06, delay_s))
        yield agent_events.TokenEvent(text=chunk)

    card_id = f"mock-{turn_id}"
    yield agent_events.RouteCardEvent(
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
    session_module.append_history(session, "assistant", text)
    session_module.append_tool_summary(session, "mock_agent", "served deterministic preview data")
    session_module.add_route_cards(
        session, [{"card_id": card_id, "role": "recommended", "lines": lines, "eta_minutes": eta_minutes}]
    )
    if trace is not None:
        trace.tool_calls = tool_calls
        trace.final_text = text

    elapsed_ms = round((time.monotonic() - started_at) * 1000)
    print(f"[agent] turn={turn_id} sess={session_id[:6]} mock=1 total_ms={elapsed_ms}")
    yield agent_events.DoneEvent(
        session_id=session_id,
        turn_id=turn_id,
        stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
