"""Tests for the unflagged prepare_route_options / present_route path."""
from __future__ import annotations
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.route.preparation_adapter import PreparedLeg
from app.services.trips.itinerary import build_canonical_itinerary

def _ctx(session_id: str = "sess-test") -> ToolContext:
    return ToolContext(
        session={},
        session_id=session_id,
        turn_id="t1",
        now_et="2026-08-06T12:00:00-04:00",
        origin={"lat": 40.75, "lng": -73.99},
        agent_mode="auto",
        agent_model="claude-test",
        agent_explanation_style="comparative",
    )

def _prepared_leg() -> PreparedLeg:
    origin = ResolvedPlace("Your location", 40.75, -73.99, "user")
    destination = ResolvedPlace("Barclays Center", 40.6826, -73.9754, "fallback")
    route = [
        {
            "type": "WALK",
            "duration_seconds": 180,
            "departure_time_iso": "2026-08-06T12:00:00-04:00",
            "arrival_time_iso": "2026-08-06T12:03:00-04:00",
        },
        {
            "type": "SUBWAY",
            "route_id": "Q",
            "duration_seconds": 1200,
            "departure_stop": "Canal St",
            "arrival_stop": "Atlantic Av",
            "departure_time_iso": "2026-08-06T12:05:00-04:00",
            "arrival_time_iso": "2026-08-06T12:25:00-04:00",
        },
    ]
    scored = [
        {
            "index": 0,
            "score": 22,
            "total_minutes": 23,
            "transfers": 0,
            "alert_count": 0,
            "transit_count": 1,
            "event_crowd_penalty": 0,
            "rank": 1,
        }
    ]
    return PreparedLeg(
        tool_input={
            "origin": "user",
            "destination": "Barclays Center",
            "destination_source": "current_turn",
        },
        origin_raw="user",
        destination_raw="Barclays Center",
        origin_place=origin,
        destination_place=destination,
        departure_time=None,
        arrival_by=None,
        excluded=set(),
        parsed_routes=[route],
        scored=scored,
        relevant_alerts=[],
        event_evidence_status="not_required",
        event_impacts=[],
        event_failures=[],
        crowd_search_metadata={"grok_status": "not_required"},
        incident_scan_metadata={
            "status": "complete",
            "sources": {"attempted": [], "completed": []},
        },
        evidence_envelopes={},
        collect_crowd_evidence=False,
        incidents=[],
        stalled=[],
        stalled_buses=[],
        timings={},
        leg_telemetry=None,
        plan_origin=0.0,
    )

def _present_route_input(candidate_id: str, **overrides) -> dict:
    return {
        "candidate_id": candidate_id,
        "lead_in": "The route options were close, so I chose this one for your trip.",
        "follow_up": "",
        "reason_code": "meets_hard_constraints",
        **overrides,
    }

def _stored_itinerary(route: list[dict], origin: dict, destination: dict) -> dict:
    return build_canonical_itinerary(
        route,
        origin=origin,
        destination=destination,
    )
