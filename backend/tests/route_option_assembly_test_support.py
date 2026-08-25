"""Shared fixtures for route-option assembly and projection tests."""

from __future__ import annotations

from app.services.agent.tools._types import ToolContext
from tests.conversation.conversation_matrix_harness import make_leg


SCORE_CONTRACT_KEYS = {
    "index",
    "score",
    "total_minutes",
    "transfers",
    "alert_count",
    "transit_count",
    "alerts",
    "event_crowd_penalty",
    "street_walking_seconds",
    "in_station_transfer_seconds",
    "walk_minutes",
    "walking_penalty",
    "preferred_mode_penalty",
    "accessibility_status",
    "rank",
}


def route_context() -> ToolContext:
    return ToolContext(
        session={},
        session_id="sess-assembly",
        turn_id="t1",
        now_et="2026-08-06T12:00:00-04:00",
        origin={"lat": 40.75, "lng": -73.99},
        agent_mode="auto",
        agent_model="claude-test",
        agent_explanation_style="comparative",
    )


def evidence_legs() -> tuple:
    """Return two provider-shaped legs with distinct scoped evidence."""

    first = make_leg(
        route_ids=("Q",),
        destination="B Pizza",
        alerts=({"header": "Q disruption", "route_ids": ["Q"]},),
        event_impacts=(
            {
                "route_index": 0,
                "event_id": "ev-game",
                "title": "Game",
                "risk_score": 4.0,
            },
        ),
    )
    second = make_leg(
        route_ids=("A",),
        destination="Barclays Center",
        alerts=(
            {"header": "Q disruption", "route_ids": ["Q"]},
            {"header": "A outage", "route_ids": ["A"]},
        ),
        event_impacts=(
            {
                "route_index": 0,
                "event_id": "ev-game",
                "title": "Game",
                "risk_score": 5.0,
            },
            {
                "route_index": 0,
                "event_id": "ev-concert",
                "title": "Concert",
                "risk_score": 6.0,
            },
        ),
    )
    second.origin_place = first.destination_place
    return first, second
