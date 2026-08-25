"""Regression gates for route preference, crowd, and nearby-place decisions.

These tests exercise the public agent capability/turn seams with deterministic
provider fixtures.  The model is scripted only to make the otherwise
non-deterministic decision reproducible; route/place facts still pass through
the real server-owned executors and stores.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.preparation_adapter import PreparedLeg
from app.services.trips import scoring
from app.services import cache
from tests._fake_anthropic import reload_agent_loop_module

def _tool(tool_id: str, name: str, tool_input: dict) -> dict:
    return {"id": tool_id, "name": name, "input": tool_input}

def _round(*calls: dict) -> dict:
    return {"tool_use": list(calls), "stop_reason": "tool_use"}

def _route(
    *,
    route_ids: tuple[str, ...],
    walking_seconds: int,
    total_seconds: int,
) -> list[dict]:
    """Build a minimal parsed route with canonical transfer/walk facts."""

    steps: list[dict] = []
    cursor = datetime.fromisoformat("2026-08-20T12:00:00-04:00")
    ride_total = max(60, total_seconds - walking_seconds)
    ride_base, ride_remainder = divmod(ride_total, max(1, len(route_ids)))
    for index, route_id in enumerate(route_ids):
        if index == 0:
            walk = walking_seconds if len(route_ids) == 1 else 60
        else:
            walk = max(0, walking_seconds - 60)
        if walk:
            walk_start = cursor
            cursor += timedelta(seconds=walk)
            steps.append(
                {
                    "type": "WALK",
                    "duration_seconds": walk,
                    "departure_time_iso": walk_start.isoformat(),
                    "arrival_time_iso": cursor.isoformat(),
                    "start_point": {"latitude": 40.672, "longitude": -73.98},
                    "end_point": {"latitude": 40.673, "longitude": -73.979},
                    "polyline": {"encodedPolyline": "fixture-walk"},
                }
            )
        ride_seconds = ride_base + (ride_remainder if index == len(route_ids) - 1 else 0)
        departure = cursor
        cursor += timedelta(seconds=ride_seconds)
        steps.append(
            {
                "type": "SUBWAY",
                "route_id": route_id,
                "departure_stop": f"{route_id} origin",
                "arrival_stop": f"{route_id} destination",
                "duration_seconds": ride_seconds,
                "departure_time_iso": departure.isoformat(),
                "arrival_time_iso": cursor.isoformat(),
            }
        )
    steps[0]["route_total_seconds"] = total_seconds
    return steps

def _prepared_leg(
    *,
    destination: str,
    destination_place: ResolvedPlace,
    routes: list[list[dict]],
    event_evidence_status: str = "not_required",
    event_impacts: list[dict] | None = None,
) -> PreparedLeg:
    scored = [
        {
            "index": index,
            **scoring._route_score(
                route,
                [],
                route_index=index,
                routing_preference="FEWER_TRANSFERS",
            ),
        }
        for index, route in enumerate(routes)
    ]
    return PreparedLeg(
        tool_input={"origin": "user", "destination": destination},
        origin_raw="user",
        destination_raw=destination,
        origin_place=ResolvedPlace("Your location", 40.672, -73.98, "user"),
        destination_place=destination_place,
        departure_time=None,
        arrival_by=None,
        excluded=set(),
        parsed_routes=routes,
        scored=scored,
        relevant_alerts=[],
        event_evidence_status=event_evidence_status,
        event_impacts=list(event_impacts or []),
        event_failures=[],
        crowd_search_metadata={"grok_status": "not_required"},
        incident_scan_metadata={
            "status": "complete",
            "lookup_status": "ok",
            "coverage_status": "complete",
            "lookup_kind": "index",
            "warning_count": 0,
            "cache_hit": False,
            "sources": {"attempted": [], "completed": []},
        },
        evidence_envelopes={},
        collect_crowd_evidence=event_evidence_status != "not_required",
        incidents=[],
        stalled=[],
        stalled_buses=[],
        timings={},
        leg_telemetry=None,
        plan_origin=0.0,
    )

def _route_goal_round(
    *,
    destination: str | None,
    routing_preference: str | None = None,
    avoid_crowds: bool = False,
    destination_place_id: str | None = None,
    destination_place_ids: list[str] | None = None,
    walking_tolerance_minutes: int | None = None,
) -> dict:
    route_input = {
        "goal_key": "route",
        "origin": "user",
        "destination": destination,
        "destination_source": "current_turn",
        "avoid_crowds": avoid_crowds,
        "destination_place_ids": destination_place_ids,
        "walking_tolerance_minutes": walking_tolerance_minutes,
    }
    if destination_place_id is not None:
        route_input["destination_place_id"] = destination_place_id
    if routing_preference is not None:
        route_input["routing_preference"] = routing_preference
    return _round(
        _tool(
            "goals",
            "declare_goals",
            {
                "goals": [
                    {"goal_key": "route", "kind": "route", "depends_on": []}
                ]
            },
        ),
        _tool("prepare", "prepare_route_options", route_input),
    )

def _present_round(
    candidate_id: str,
    *,
    lead_in: str,
    reason_code: str,
) -> dict:
    return _round(
        _tool(
            "present",
            "present_route",
            {
                "goal_key": "route",
                "candidate_id": candidate_id,
                "lead_in": lead_in,
                "follow_up": "",
                "reason_code": reason_code,
            },
        )
    )


class AgentRouteDecisionTestMixin:
    @classmethod
    def setUpClass(cls) -> None:
        cls.loop = reload_agent_loop_module(
            env={"ANTHROPIC_API_KEY": "server-test-key"}
        )

    def setUp(self) -> None:
        cache._mem.clear()
