"""Focused tests for the discovery-to-route handoff (Phase 2A).

Covers routing by opaque destination_place_id, server-side canonical
identity preservation, the tool-start label, and the single-leg provider
handoff. Moved out of test_single_agent_route_tools.py so that phase does
not grow further.
"""
from __future__ import annotations
from app.services.agent import discovery_store
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.route.preparation_adapter import PreparedLeg

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
        tool_input={"origin": "user", "destination": "Barclays Center"},
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


class DiscoveryRouteHandoffTestMixin:
    def _seed_set(self, session_id: str = "sess-test") -> str:
        return discovery_store.store_discovery_set(
            session_id=session_id,
            places=[
                {
                    "name": "Di Fara Pizza",
                    "address": "1424 Av J",
                    "latitude": 40.6298,
                    "longitude": -73.9616,
                    "provider_place_id": "ChIJ-dest",
                    "price_level": 2,
                    "rating": 4.7,
                    "review_count": 500,
                    "baseline_score": 0.82,
                    "ranking_factors": {
                        "rating": 0.94,
                        "review_volume": 0.1,
                        "open_bonus": 0.15,
                        "price_level": 2,
                    },
                },
                {
                    "name": "Di Fara Pizza",
                    "address": "123 Newkirk Ave",
                    "latitude": 40.6360,
                    "longitude": -73.9600,
                    "provider_place_id": "ChIJ-wp2",
                    "price_level": 2,
                    "rating": 4.6,
                    "review_count": 400,
                    "baseline_score": 0.8,
                    "ranking_factors": {
                        "rating": 0.92,
                        "review_volume": 0.08,
                        "open_bonus": 0.15,
                        "price_level": 2,
                    },
                },
                {
                    "name": "Lucali",
                    "address": "575 Henry St",
                    "latitude": 40.6810,
                    "longitude": -73.9980,
                    "provider_place_id": "ChIJ-lucali",
                    "price_level": 3,
                    "rating": 4.8,
                    "review_count": 600,
                    "baseline_score": 0.85,
                    "ranking_factors": {
                        "rating": 0.96,
                        "review_volume": 0.12,
                        "open_bonus": 0.15,
                        "price_level": 3,
                    },
                },
            ],
            query="pizza",
        )
