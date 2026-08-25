"""Batch J1 fixtures: provider/data failure shapes for the real loop.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Every fixture is honest about what it scripts:

- ``prepare_single_leg`` failure shapes used by the real
  ``prepare_route_options`` executor: timeout-shaped (raises
  ``asyncio.TimeoutError``), exception-shaped (raises ``RuntimeError``),
  nonfatal no-route (returns a matching ``ToolResult`` that the real
  ``prepare_route_persistence.nonfatal_prepare_result`` converts to an audit
  set), malformed/unusable (returns a non-matching ``ToolResult`` that the
  real nonfatal gate leaves as a hard failure), and empty aggregate (a
  ``PreparedLeg`` with no parsed routes, exercised through the real
  aggregate path).
- ``PreparedLeg`` coverage/accessibility shapes: incident stale/unavailable
  scan metadata, accessibility facts absent (unknown) versus provider
  reported ``unavailable`` (canonical normalization maps it to unknown,
  never accessible), and a fully current control leg.
- Live-feed bytes for the real ``lookup_arrivals`` parse path (protobuf
  built with the real generated bindings, parsed by the real feed parser),
  including a stale-timestamp feed.

No fake executor, registry, store, loop, or SSE layer is used anywhere.
"""

from __future__ import annotations

import asyncio
import time

from app.services.agent.tools._types import ToolResult
from app.services.mta.feeds import _gtfs_realtime_pb2

from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    make_leg,
)


FAILURE_TEXT = "I could not find a route right now."
PRESENT_TEXT = "Here is the route."
EXACT_INCIDENT_STALE_COVERAGE = {
    "mta": "current",
    "vehicles": "current",
    "incidents": "stale",
    "events": "not_required",
}
EXACT_INCIDENT_UNAVAILABLE_COVERAGE = {
    "mta": "current",
    "vehicles": "current",
    "incidents": "unavailable",
    "events": "not_required",
}
VALID_COVERAGE = {
    "mta": "current",
    "vehicles": "current",
    "incidents": "current",
    "events": "not_required",
}


def goal_declaration_round(goal_key: str, kind: str) -> dict:
    """Declare one independent outcome goal through the public protocol."""

    return _turn_round(
        "declare_goals",
        f"tu_goals_{goal_key}",
        {
            "goals": [
                {
                    "goal_key": goal_key,
                    "kind": kind,
                    "depends_on": [],
                }
            ]
        },
    )


def goal_completion_round(
    goal_key: str,
    message: str,
    *,
    outcome: str = "answer",
    tool_id: str = "tu_complete",
) -> dict:
    """Complete a declared goal with a bounded recovery or answer."""

    return _turn_round(
        "complete_turn",
        tool_id,
        {
            "goal_keys": [goal_key],
            "outcome": outcome,
            "message": message,
        },
    )


def prepare_rounds(
    *,
    destination: str,
    tool_input_extra: dict | None = None,
    text: str = FAILURE_TEXT,
):
    """Scripted model rounds for one failed ``prepare_route_options`` turn."""

    tool_input = {
        "goal_key": "route",
        "origin": None,
        "destination": destination,
        "destination_place_id": None,
        "exclude_modes": None,
        "allowed_modes": None,
        "excluded_route_ids": None,
        "required_route_ids": None,
        "allowed_route_ids": None,
        "preferred_modes": None,
        "routing_preference": None,
        "departure_time": None,
        # Arrival differs from the accepted leave_now trip so the snapshot
        # proves accepted planning fields survive the failed replan.
        "arrival_by": "2026-08-06T13:00:00-04:00",
        "waypoints": None,
        "waypoint_dwell_minutes": None,
        "avoid_crowds": None,
        "avoid_stairs": None,
        "accessibility_required": None,
        "walking_tolerance_minutes": None,
        "what_if": None,
        "activity_label": None,
    }
    if tool_input_extra:
        tool_input.update(tool_input_extra)
    return [
        goal_declaration_round("route", "route"),
        _turn_round("prepare_route_options", "tu_1", tool_input),
        goal_completion_round(
            "route",
            text,
            outcome="unavailable",
            tool_id="tu_2",
        ),
    ]


def prepare_present_rounds(
    *,
    destination: str,
    candidate_id: str,
    tool_input_extra: dict | None = None,
):
    """Scripted model rounds for one valid prepare + present turn."""

    tool_input = {
        "goal_key": "route",
        "origin": None,
        "destination": destination,
        "destination_place_id": None,
        "exclude_modes": None,
        "allowed_modes": None,
        "excluded_route_ids": None,
        "required_route_ids": None,
        "allowed_route_ids": None,
        "preferred_modes": None,
        "routing_preference": None,
        "departure_time": None,
        "arrival_by": None,
        "waypoints": None,
        "waypoint_dwell_minutes": None,
        "avoid_crowds": None,
        "avoid_stairs": None,
        "accessibility_required": None,
        "walking_tolerance_minutes": None,
        "what_if": None,
        "activity_label": None,
    }
    if tool_input_extra:
        tool_input.update(tool_input_extra)
    return [
        goal_declaration_round("route", "route"),
        _turn_round("prepare_route_options", "tu_1", tool_input),
        _turn_round(
            "present_route",
            "tu_2",
            {
                "goal_key": "route",
                "candidate_id": candidate_id,
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
        ),
    ]

# ---------------------------------------------------------------------------
# prepare_single_leg failure shapes
# ---------------------------------------------------------------------------


def timeout_prepare_seam() -> asyncio.TimeoutError:
    """Provider seam hangs past its deadline: timeout-shaped failure."""

    return asyncio.TimeoutError("simulated provider timeout")


def exception_prepare_seam() -> RuntimeError:
    """Provider seam crashes: exception-shaped failure."""

    return RuntimeError("simulated provider exception")


def no_route_result() -> ToolResult:
    """Provider seam reports no route: nonfatal (token matches the real gate)."""

    return ToolResult(ok=False, error="no transit route found between those points")


def malformed_prepare_result() -> ToolResult:
    """Provider seam returns unusable data with a non-matching error.

    The real ``nonfatal_prepare_result`` gate only converts errors carrying
    no-route / no-modes / coverage tokens; any other provider error stays a
    hard tool failure, so this shape must not create a candidate set or
    mutate selection.
    """

    return ToolResult(
        ok=False,
        error="provider returned malformed route data",
    )


# ---------------------------------------------------------------------------
# PreparedLeg coverage / accessibility shapes
# ---------------------------------------------------------------------------


def valid_leg(destination: str = "Work") -> object:
    """Fully current provider leg: all live evidence current, no alerts."""

    return make_leg(
        route_ids=("R",),
        destination=destination,
        incident_status="complete",
        evidence_available=True,
    )


def incident_stale_leg(destination: str = "Work") -> object:
    """Provider leg whose incident scan is stale; other evidence current."""

    return make_leg(
        route_ids=("R",),
        destination=destination,
        incident_status="stale",
        evidence_available=True,
    )


def accessibility_unavailable_leg(destination: str = "Work") -> object:
    """Provider leg reporting unavailable accessibility facts and incident scan.

    The incident scan is unavailable (other evidence current) and every
    transit step explicitly reports accessibility facts as unavailable.

    The canonical ``transfer_semantics`` normalization maps a reported
    ``unavailable`` to ``unknown`` -- it must never surface as accessible --
    and the hard-constraint violation stays
    ``accessibility_unknown_or_unavailable``.
    """

    leg = make_leg(
        route_ids=("R",),
        destination=destination,
        incident_status="unavailable",
        evidence_available=True,
    )
    for step in leg.parsed_routes[0]:
        if step.get("type") == "SUBWAY":
            step["departure_accessibility"] = "unavailable"
            step["arrival_accessibility"] = "unavailable"
    return leg


def empty_aggregate_leg(destination: str = "Work") -> object:
    """Provider leg with no usable parsed routes (aggregate no-good path)."""

    leg = make_leg(
        route_ids=("R",),
        destination=destination,
        evidence_available=True,
    )
    leg.parsed_routes = []
    leg.scored = []
    return leg


# ---------------------------------------------------------------------------
# Status / arrival / discovery provider seams
# ---------------------------------------------------------------------------


def empty_poi_result() -> ToolResult:
    """Discovery provider returns no matching places (graceful empty)."""

    return ToolResult(ok=True, data={"places": []}, summary="no matching places found")


class FakeSubwayGtfs:
    """Minimal GTFS stop index the real arrivals lookup reads (no DB/network)."""

    def __init__(self) -> None:
        self.stops = [
            {
                "stop_id": "D28",
                "stop_name": "Newkirk Plaza",
                "stop_lat": 40.6351,
                "stop_lon": -73.9628,
                "route_ids": ["B", "Q"],
            },
            {
                "stop_id": "D26",
                "stop_name": "Prospect Park",
                "stop_lat": 40.6616,
                "stop_lon": -73.9623,
                "route_ids": ["B", "Q", "S"],
            },
        ]

    def get_subway_stops_with_routes(self, route_ids):
        wanted = {str(route).upper() for route in route_ids}
        return [
            stop
            for stop in self.stops
            if wanted.intersection({str(item).upper() for item in stop["route_ids"]})
        ]

    def get_child_stop_ids(self, stop_id):
        return [f"{stop_id}N", f"{stop_id}S"]


def subway_feed_bytes(
    predictions: list[tuple[str, int]],
    *,
    route_id: str = "Q",
    timestamp: int | None = None,
) -> bytes:
    """Build real GTFS-RT feed bytes parsed by the real feed parser."""

    pb = _gtfs_realtime_pb2()
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = (
        int(time.time()) if timestamp is None else timestamp
    )
    for index, (stop_id, arrival_time) in enumerate(predictions):
        entity = feed.entity.add()
        entity.id = f"entity-{index}"
        update = entity.trip_update
        update.trip.trip_id = f"trip-{index}"
        update.trip.route_id = route_id
        stop = update.stop_time_update.add()
        stop.stop_id = stop_id
        stop.arrival.time = arrival_time
    return feed.SerializeToString()


def stale_subway_feed_bytes() -> bytes:
    """Feed with future predictions but a timestamp far past the 120s TTL."""

    return subway_feed_bytes(
        [("D28N", int(time.time()) + 600)],
        timestamp=int(time.time()) - 3600,
    )


__all__ = (
    "EXACT_INCIDENT_STALE_COVERAGE",
    "EXACT_INCIDENT_UNAVAILABLE_COVERAGE",
    "FAILURE_TEXT",
    "PRESENT_TEXT",
    "VALID_COVERAGE",
    "FakeSubwayGtfs",
    "accessibility_unavailable_leg",
    "empty_aggregate_leg",
    "empty_poi_result",
    "exception_prepare_seam",
    "goal_completion_round",
    "goal_declaration_round",
    "incident_stale_leg",
    "malformed_prepare_result",
    "no_route_result",
    "prepare_present_rounds",
    "prepare_rounds",
    "stale_subway_feed_bytes",
    "subway_feed_bytes",
    "timeout_prepare_seam",
    "valid_leg",
)
