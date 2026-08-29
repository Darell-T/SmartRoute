"""Focused, reusable harness for canonical conversational route-loop tests.

Batch A scope: deterministic coverage of canonical no-good-options behavior
(``prepare_route_options`` returning ``no_hard_constraint_match`` /
``insufficient_coverage`` / ``all_materially_degraded``) and one
constraint-relaxation follow-up (scenario A-NG-06), exercised through the
*real* agent loop (``app.services.agent.loop``) with the *real*
``TOOL_REGISTRY`` executors for ``prepare_route_options`` and
``present_route``.

Only genuine provider/data seams are scripted:

- ``prepare_single_leg`` -- the narrow provider-scoring seam inside the real
  canonical prepare executor (route/evidence fixtures come from here).
- ``trips.enrichment._enrich_route`` -- live route enrichment called by the
  real canonical ``present_route`` executor.
- ``tools.lookup_arrivals.execute`` -- live MTA arrival fetch that the real
  presentation path may call for first-leg context.
- ``candidate_store.new_candidate_id`` -- opaque id generation, only when a
  test needs a deterministic candidate id to script ``present_route`` input.
- ``candidate_store.store_candidate_set`` -- observed, never replaced: a
  recording wrapper calls the original real store and records the returned
  set id, so tests can prove a separate non-presentable audit set was
  persisted without locating it through ``trip_state.active_candidate_set_id``.

Anthropic inference is scripted through ``tests/_fake_anthropic``; model
prose is deterministic mock text, never a claim of model linguistic accuracy.

Not a test module: no ``Test*``/``test_*`` names at module level, so pytest
never collects it.
"""

from __future__ import annotations

import copy
import dataclasses
from typing import Any
from unittest.mock import AsyncMock, patch

from app.services import cache
from app.services.agent import candidate_store
from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module
from app.services.agent.public_surface import PUBLIC_TOOL_NAMES
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.preparation_adapter import PreparedLeg

from tests._fake_anthropic import reload_agent_loop_module

PUBLIC_TOOL_PROFILE = frozenset(PUBLIC_TOOL_NAMES)


def check_transit_input(operation: str = "service_status", **fields) -> dict:
    payload = {
        "operation": operation,
        "route_ids": [],
        "stop_query": None,
        "direction": None,
        "area": None,
        "station": None,
        "topic": None,
        "event_query": None,
        "venue": None,
        "at": None,
        "window_start": None,
        "window_end": None,
    }
    payload.update(fields)
    return payload


def discover_search_input(query: str, *, borough: str | None = "Brooklyn") -> dict:
    if borough:
        scope = {"kind": "boroughs", "values": [borough]}
    else:
        scope = {"kind": "nyc", "values": []}
    return {
        "operation": "search",
        "query": query,
        "scope": scope,
        "open_now": None,
        "max_results": 8,
        "candidate_names": [],
        "exclude_presented": False,
        "queue_context": {"mode": "ignore", "max_wait_minutes": None},
    }


def load_agent_loop(env: dict[str, str] | None = None):
    """(Re)load the real agent loop against scripted fake-Anthropic."""

    merged_env = {"ANTHROPIC_API_KEY": "server-test-key"}
    if env:
        merged_env.update(env)
    return reload_agent_loop_module(env=merged_env)


def clear_caches() -> None:
    """Reset the in-memory cache used by candidate/discovery stores."""

    cache._mem.clear()


def policy_model(loop, mode: str) -> tuple[str, str]:
    policy = loop.agent_policy.policy_for_mode(mode)
    return policy.mode, policy.model


# ---------------------------------------------------------------------------
# PreparedLeg fixtures (the canonical prepare seam's "provider" output)
# ---------------------------------------------------------------------------


def _route_steps(route_id: str, *, destination: str) -> list[dict]:
    return [
        {
            "type": "WALK",
            "duration_seconds": 180,
            "departure_time_iso": "2026-08-06T12:00:00-04:00",
            "arrival_time_iso": "2026-08-06T12:03:00-04:00",
        },
        {
            "type": "SUBWAY",
            "route_id": route_id,
            "duration_seconds": 1200,
            "departure_stop": "Home St",
            "arrival_stop": destination,
            "departure_time_iso": "2026-08-06T12:05:00-04:00",
            "arrival_time_iso": "2026-08-06T12:25:00-04:00",
        },
    ]


def make_leg(
    *,
    route_ids: tuple[str, ...] = ("Q",),
    destination: str = "Work",
    alerts: tuple[dict, ...] = (),
    incidents: tuple[dict, ...] = (),
    event_impacts: tuple[dict, ...] = (),
    incident_status: str = "complete",
    evidence_available: bool = False,
    event_evidence_status: str = "not_required",
) -> PreparedLeg:
    """One deterministic provider fixture shaped like canonical prepared data."""

    routes = [_route_steps(route_id, destination=destination) for route_id in route_ids]
    scored = [
        {
            "index": index,
            "score": 22 + index,
            "total_minutes": 23 + index,
            "transfers": 0,
            "alert_count": 0,
            "transit_count": 1,
            "event_crowd_penalty": 0,
            "rank": index + 1,
        }
        for index in range(len(routes))
    ]
    envelopes: dict[str, Any] = {}
    if evidence_available:
        from app.services import evidence

        envelopes = {
            "alerts": evidence.evidence_envelope("mta_alerts", [], ttl_seconds=120),
            "subway_vehicles": evidence.evidence_envelope(
                "mta_gtfs_rt", [], ttl_seconds=120
            ),
        }
    return PreparedLeg(
        tool_input={"origin": "user", "destination": destination},
        origin_raw="user",
        destination_raw=destination,
        origin_place=ResolvedPlace("Your location", 40.75, -73.99, "user"),
        destination_place=ResolvedPlace(destination, 40.6826, -73.9754, "fallback"),
        departure_time=None,
        arrival_by=None,
        excluded=set(),
        parsed_routes=routes,
        scored=scored,
        relevant_alerts=list(alerts),
        event_evidence_status=event_evidence_status,
        event_impacts=list(event_impacts),
        event_failures=[],
        crowd_search_metadata={"grok_status": "not_required"},
        incident_scan_metadata={
            "status": incident_status,
            "lookup_status": (
                "ok" if incident_status == "complete" else "unavailable"
            ),
            "coverage_status": incident_status,
            "lookup_kind": "index",
            "warning_count": 0,
            "cache_hit": False,
            "sources": {"attempted": [], "completed": []},
        },
        evidence_envelopes=envelopes,
        collect_crowd_evidence=False,
        incidents=list(incidents),
        stalled=[],
        stalled_buses=[],
        timings={},
        leg_telemetry=None,
        plan_origin=0.0,
    )


def q_only_leg(destination: str = "Work") -> PreparedLeg:
    """Provider yields only the Q route (hard-excluded in A-NG-01)."""

    return make_leg(route_ids=("Q",), destination=destination)


def insufficient_coverage_leg(destination: str = "Work") -> PreparedLeg:
    """Satisfied candidate but only neutral/unusable evidence coverage."""

    return make_leg(
        route_ids=("R",),
        destination=destination,
        incident_status="unscanned",
        evidence_available=False,
    )


def all_materially_degraded_leg(destination: str = "Work") -> PreparedLeg:
    """Satisfied candidate, current coverage, but every option degraded."""

    return make_leg(
        route_ids=("R",),
        destination=destination,
        alerts=({"header": "R service change", "route_ids": ["R"]},),
        incident_status="complete",
        evidence_available=True,
    )


def no_transit_modes_result() -> ToolResult:
    """Provider seam rejects routing before it starts: no transit modes.

    Mirrors ``prepare_single_leg``'s real early return when ``exclude_modes``
    covers every allowed transit mode, which the real
    ``prepare_route_options`` executor routes through
    ``prepare_route_persistence.nonfatal_prepare_result``.
    """

    return ToolResult(
        ok=False,
        error="no transit modes left after excluding all of them",
    )


def no_route_found_result() -> ToolResult:
    """Provider seam finds no transit route for the prepared request."""

    return ToolResult(ok=False, error="no transit route found between those points")


# ---------------------------------------------------------------------------
# Accepted active-trip seeding
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SeedSnapshot:
    """The recorded canonical accepted-trip identity before a test turn."""

    session_id: str
    card_id: str
    candidate_set_id: str
    candidate_id: str
    card: dict
    destination: str
    origin: str
    planning_mode: str
    requested_departure: str | None
    requested_arrival: str | None


def seed_accepted_active_trip(
    session: dict,
    session_id: str,
    *,
    card_id: str = "rc_active_seed",
    route_id: str = "R",
    destination: str = "Work",
    origin: str = "Home",
) -> SeedSnapshot:
    """Seed one internally consistent accepted active trip.

    The candidate-set record is marked presented with its selected candidate,
    mirroring what the real ``present_route`` leaves behind, so a later
    non-presentable replan has a concrete accepted selection to preserve.
    """

    candidate_id = "cd_active_seed"
    set_id = candidate_store.store_candidate_set(
        session_id=session_id,
        payload={
            "tool_input": {"origin": origin, "destination": destination},
            "origin_place": {
                "name": origin,
                "latitude": 40.75,
                "longitude": -73.99,
            },
            "destination_place": {
                "name": destination,
                "latitude": 40.68,
                "longitude": -73.98,
            },
            "parsed_routes": [[{"type": "SUBWAY", "route_id": route_id}]],
            "scored": [
                {"index": 0, "score": 1, "total_minutes": 20, "transfers": 0}
            ],
            "candidates": [{"candidate_id": candidate_id, "index": 0}],
            "route_status": "good",
            "evidence_coverage": {
                "mta": "current",
                "vehicles": "current",
                "incidents": "current",
                "events": "not_required",
            },
        },
    )
    error = candidate_store.mark_presented(set_id, candidate_id, session_id=session_id)
    if error:
        raise AssertionError(f"seed presentation failed: {error}")
    trip_state_module.update_trip_state(
        session,
        origin=origin,
        destination=destination,
        waypoints=[],
        planning_mode="leave_now",
        requested_departure=None,
        requested_arrival=None,
        active_candidate_set_id=set_id,
        selected_candidate_id=candidate_id,
    )
    card = {
        "card_id": card_id,
        "role": "recommended",
        "lines": [route_id],
        "eta_minutes": 20,
        "destination": destination,
    }
    session["active_trip"] = card
    session["route_cards"] = [card]
    return SeedSnapshot(
        session_id=session_id,
        card_id=card_id,
        candidate_set_id=set_id,
        candidate_id=candidate_id,
        card=card,
        destination=destination,
        origin=origin,
        planning_mode="leave_now",
        requested_departure=None,
        requested_arrival=None,
    )


# ---------------------------------------------------------------------------
# Turn execution
# ---------------------------------------------------------------------------


def _turn_round(tool_name: str, tool_id: str, tool_input: dict) -> dict:
    payload = dict(tool_input)
    if tool_name == "prepare_route_options":
        has_explicit_destination = bool(
            payload.get("destination") or payload.get("destination_place_id")
        )
        payload.setdefault(
            "destination_source",
            "current_turn" if has_explicit_destination else "accepted_trip",
        )
    elif tool_name == "present_route":
        payload.setdefault(
            "lead_in",
            "The route options were close, so I chose this one for your trip.",
        )
        payload.setdefault("follow_up", "")
        payload.setdefault("reason_code", "meets_hard_constraints")
    elif tool_name == "present_transit":
        payload.setdefault("lead_in", "")
        payload.setdefault("follow_up", "")
    return {
        "tool_use": [{"id": tool_id, "name": tool_name, "input": payload}],
        "stop_reason": "tool_use",
    }


def complete_turn_round(
    tool_id: str, message: str, *, outcome: str = "answer"
) -> dict:
    return _turn_round(
        "complete_turn",
        tool_id,
        {"outcome": outcome, "message": message},
    )


def discovery_id_tokens(session_id: str, turn_id: str) -> tuple[str, tuple[str, str, str]]:
    suffix = f"{str(session_id)[-8:]}-{turn_id}"
    return f"ds_{suffix}", (
        f"pl_{suffix}-1",
        f"pl_{suffix}-2",
        f"pl_{suffix}-3",
    )


def present_places_round(
    tool_id: str,
    set_id: str,
    place_ids: tuple[str, ...] | list[str],
    *,
    research_used: bool = False,
) -> dict:
    selections = [
        {
            "place_id": place_id,
            "reason": "top_pick" if index == 0 else "preference_match",
        }
        for index, place_id in enumerate(place_ids)
    ]
    return _turn_round(
        "present_places",
        tool_id,
        {
            "discovery_set_id": set_id,
            "selections": selections,
            "research_used": research_used,
        },
    )


def text_round(text: str) -> dict:
    return {"text": [text], "stop_reason": "end_turn"}


def _turn_prepare_mock(prepare_leg, prepare_legs):
    if prepare_legs is not None:
        return AsyncMock(side_effect=prepare_legs)
    if prepare_leg is not None:
        return AsyncMock(return_value=prepare_leg)
    return None


def _turn_seam_patchers(
    *,
    enrich_mock,
    arrivals_mock,
    prepare_mock,
    fixed_candidate_id,
    recording_store,
    mocks,
):
    patchers = [
        patch(
            "app.services.trips.enrichment._enrich_route",
            new=enrich_mock,
        ),
        patch(
            "app.services.agent.tools.transit.lookup_arrivals.execute",
            new=arrivals_mock,
        ),
    ]
    if mocks is not None:
        patchers.append(
            patch(
                "app.services.agent.candidate_store.store_candidate_set",
                new=recording_store,
            )
        )
    if prepare_mock is not None:
        patchers.extend(
            [
                patch(
                    "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
                    new=prepare_mock,
                ),
                patch(
                    "app.services.agent.tools.route.prepare_route_branches.prepare_single_leg",
                    new=prepare_mock,
                ),
            ]
        )
    if fixed_candidate_id is not None:
        patchers.append(
            patch(
                "app.services.agent.candidate_store.new_candidate_id",
                return_value=fixed_candidate_id,
            )
        )
    return patchers


async def run_turn(
    loop,
    *,
    session: dict,
    session_id: str,
    message: str,
    rounds: list[dict],
    mode: str = "auto",
    prepare_leg: PreparedLeg | ToolResult | None = None,
    prepare_legs: list | None = None,
    fixed_candidate_id: str | None = None,
    trace=None,
    turn_id: str = "t1",
    origin: dict | None = None,
    mocks: dict | None = None,
):
    """Run one real loop turn on a live session with scripted model rounds.

    The canonical registry/executors run untouched; only the seams named in
    the module docstring are patched. ``prepare_legs`` scripts an ordered
    sequence of provider legs for multi-stop chains (``prepare_single_leg``
    is called once per segment in call order); ``prepare_leg`` keeps the
    single-leg form for existing callers. Returns ``(events, trace)`` and,
    when ``mocks`` is a dict, records the created AsyncMocks plus an
    immutable ``session_at_store`` snapshot of the live session's
    ``active_trip``/``route_cards`` taken each time the real
    ``store_candidate_set`` runs, so tests can prove the accepted trip/card
    survives until a successful replacement commits.
    """

    loop.client.messages._rounds = list(rounds)
    loop.client.messages.calls = []
    prepare_mock = _turn_prepare_mock(prepare_leg, prepare_legs)
    enrich_mock = AsyncMock(return_value=None)
    arrivals_mock = AsyncMock(
        return_value=ToolResult(ok=False, error="fixture: no live arrivals")
    )
    original_store = candidate_store.store_candidate_set
    stored_set_ids: list[str] = []

    def _recording_store(*args, **kwargs):
        set_id = original_store(*args, **kwargs)
        stored_set_ids.append(set_id)
        if mocks is not None:
            mocks.setdefault("session_at_store", []).append(
                {
                    "active_trip": copy.deepcopy(session.get("active_trip")),
                    "route_cards": copy.deepcopy(session.get("route_cards") or []),
                }
            )
        return set_id

    patchers = _turn_seam_patchers(
        enrich_mock=enrich_mock,
        arrivals_mock=arrivals_mock,
        prepare_mock=prepare_mock,
        fixed_candidate_id=fixed_candidate_id,
        recording_store=_recording_store,
        mocks=mocks,
    )
    if mocks is not None:
        mocks["prepare_single_leg"] = prepare_mock
        mocks["enrich_route"] = enrich_mock
        mocks["lookup_arrivals"] = arrivals_mock
        mocks["stored_candidate_set_ids"] = stored_set_ids
    for patcher in patchers:
        patcher.start()
    try:
        events = [
            event
            async for event in loop.run_agent_turn(
                session=session,
                session_id=session_id,
                turn_id=turn_id,
                message=message,
                now_et="2026-08-06T12:00:00-04:00",
                gtfs=None,
                origin=origin if origin is not None else {"lat": 40.75, "lng": -73.99},
                response_presentation=mode,
                trace=trace,
            )
        ]
    finally:
        for patcher in patchers:
            patcher.stop()
    return events, trace


def route_cards(events: list) -> list:
    return [event for event in events if event.type == "route_card"]


def new_session() -> tuple[str, dict]:
    return session_module.new_session()


__all__ = (
    "SeedSnapshot",
    "_turn_round",
    "all_materially_degraded_leg",
    "clear_caches",
    "insufficient_coverage_leg",
    "load_agent_loop",
    "make_leg",
    "new_session",
    "no_route_found_result",
    "no_transit_modes_result",
    "policy_model",
    "q_only_leg",
    "route_cards",
    "run_turn",
    "seed_accepted_active_trip",
    "text_round",
)
