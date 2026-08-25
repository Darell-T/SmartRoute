"""Batch D1 support: discovery -> waypoint lifecycle transcript runner.

Non-test module (no ``Test*``/``test_*`` names): pytest never collects it.
Drives the exact five-turn D1 transcript through the REAL agent loop
(``loop.run_agent_turn``) in ONE server-owned session, for Auto and Quick:

  turn 1: "Get me to Barclays."               -> real prepare_route_options +
                                                real present_route (accepted
                                                destination route, no waypoints)
  turn 2: "Find pizza near Barclays."         -> real search_local_places
                                                (narrow POI seam) binds a real
                                                ds_ set; no route mutation
  turn 3: "Which one is easiest to reach?"    -> comparison, no executed tools
  turn 4: "Take me to the second one first."  -> real get_place_details
                                                (ordinal=2) -> real
                                                prepare_route_options with
                                                waypoints=[real ordinal-2 id]
                                                -> real present_route
  turn 5: "Actually remove the pizza stop."   -> real prepare_route_options with
                                                explicit empty waypoints ->
                                                real present_route (restore)

Every canonical executor, registry, store, ledger, intent/tool filter, prompt
context, and SSE path is real. Only the established provider/data seams of
``tests.conversation.conversation_matrix_harness`` plus the discovery provider seam are
scripted; Anthropic inference is deterministic mock text. All real ids are
read back out of the real stores between turns.

The per-turn required invariants live in ``test_conversation_discovery_waypoint``
so this module stays small: it owns how the transcript runs and how evidence
is captured. Turn 5 runs only after turn 4's gates pass; otherwise it is
recorded blocked/not executed.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.places import discover_places
from tests.conversation.conversation_discovery_support import _DiscoveryRouteBase
from tests.conversation.conversation_discovery_waypoint_fixtures import (
    BARCLAYS_CANONICAL_NAME,
    DESTINATION_LABEL,
    FIXED_CANDIDATE_BARCLAYS,
    FIXED_CANDIDATE_REMOVAL,
    FIXED_CANDIDATE_WAYPOINT,
    M1_GET_BARCLAYS,
    M2_FIND_PIZZA,
    M3_WHICH_EASIEST,
    M4_SECOND_FIRST,
    M5_REMOVE_STOP,
    POI_RESULT,
    SEARCH_INPUT,
    barclays_leg,
    waypoint_segment_legs,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    complete_turn_round,
    policy_model,
    route_cards,
    run_turn,
    text_round,
)

TEMP_FIELDS = (
    "temporary_candidate_set_id",
    "temporary_selected_candidate_id",
    "temporary_base_candidate_set_id",
)
UNCHANGED_FIELDS = (
    "destination",
    "origin",
    "waypoints",
    "planning_mode",
    "requested_departure",
    "requested_arrival",
    "preferences",
)


@dataclasses.dataclass(frozen=True)
class SessionProjection:
    """Immutable deep-copied session facts captured at one turn boundary."""

    active_trip: dict | None
    route_cards: tuple
    slots: dict
    pending_trip: dict
    trip_state: dict

    @classmethod
    def capture(cls, session: dict) -> "SessionProjection":
        return cls(
            active_trip=copy.deepcopy(session.get("active_trip")),
            route_cards=tuple(copy.deepcopy(session.get("route_cards") or [])),
            slots=copy.deepcopy(session.get("slots") or {}),
            pending_trip=copy.deepcopy(session.get("pending_trip") or {}),
            trip_state=copy.deepcopy(trip_state_module.get_trip_state(session)),
        )


@dataclasses.dataclass(frozen=True)
class TurnEvidence:
    """One turn's snapshot: events, ledger trace, mocks, trip state, session,
    and the real model-request surfaces (offered tools, context, tool blob)."""

    events: list
    trace: object
    mocks: dict
    state: dict
    offered: frozenset
    context: str
    result_blob: str
    models: tuple
    session: dict
    session_id: str
    before_state: dict | None = None
    before_session: SessionProjection | None = None
    after_session: SessionProjection | None = None


class _DiscoveryWaypointBase(_DiscoveryRouteBase):
    """Shared Batch D1 turn runner and evidence capture."""

    loop = None  # set in setUpClass by subclasses

    async def _run_turn(
        self,
        *,
        mode: str,
        session: dict,
        session_id: str,
        message: str,
        turn_id: str,
        rounds: list[dict],
        prepare_leg=None,
        prepare_legs=None,
        fixed_candidate_id: str | None = None,
        patch_poi: bool = False,
        before_state: dict | None = None,
        before_session: SessionProjection | None = None,
    ) -> TurnEvidence:
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        poi_patcher = None
        if patch_poi:
            poi_patcher = patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=POI_RESULT()),
            )
            poi_patcher.start()
        try:
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                message=message,
                rounds=rounds,
                mode=mode,
                trace=trace,
                mocks=mocks,
                turn_id=turn_id,
                prepare_leg=prepare_leg,
                prepare_legs=prepare_legs,
                fixed_candidate_id=fixed_candidate_id,
            )
        finally:
            if poi_patcher is not None:
                poi_patcher.stop()
        calls = self.loop.client.messages.calls
        return TurnEvidence(
            events=events,
            trace=trace,
            mocks=mocks,
            state=copy.deepcopy(trip_state_module.get_trip_state(session)),
            offered=frozenset(schema["name"] for schema in calls[0]["tools"]),
            context=str(calls[0]["messages"][-1]["content"]),
            result_blob=self._model_tool_result_blob(round_index=len(calls) - 1),
            models=tuple(call["model"] for call in calls),
            session=session,
            session_id=session_id,
            before_state=(
                copy.deepcopy(before_state) if before_state is not None else None
            ),
            before_session=before_session,
            after_session=SessionProjection.capture(session),
        )

    # ------------------------------------------------------------------
    # Turn runners (scripted model rounds; real executors run underneath)
    # ------------------------------------------------------------------

    async def _turn1(self, *, mode: str, session: dict, session_id: str):
        rounds = [
            _turn_round(
                "prepare_route_options",
                "tu1-prepare",
                {"destination": DESTINATION_LABEL},
            ),
            _turn_round(
                "present_route",
                "tu1-present",
                {"candidate_id": FIXED_CANDIDATE_BARCLAYS},
            ),
            text_round(f"Here is your route to {BARCLAYS_CANONICAL_NAME}."),
        ]
        return await self._run_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            message=M1_GET_BARCLAYS,
            turn_id="t1",
            rounds=rounds,
            prepare_leg=barclays_leg(),
            fixed_candidate_id=FIXED_CANDIDATE_BARCLAYS,
        )

    async def _turn2(
        self,
        *,
        mode: str,
        session: dict,
        session_id: str,
        before_state: dict,
        before_session: SessionProjection,
    ):
        rounds = [
            _turn_round("discover_places", "tu2-search", dict(SEARCH_INPUT)),
            _turn_round(
                "present_places",
                "tu2-present",
                {
                    "discovery_set_id": "ds_wp_1",
                    "selections": [
                        {"place_id": "pl_wp_1", "reason": "top_pick"},
                        {"place_id": "pl_wp_2", "reason": "preference_match"},
                        {"place_id": "pl_wp_3", "reason": "preference_match"},
                    ],
                    "research_used": False,
                },
            ),
        ]
        place_ids = iter(("pl_wp_1", "pl_wp_2", "pl_wp_3"))
        with (
            patch.object(discovery_store, "new_discovery_set_id", return_value="ds_wp_1"),
            patch.object(discovery_store, "new_place_id", side_effect=lambda: next(place_ids)),
        ):
            return await self._run_turn(
                mode=mode,
                session=session,
                session_id=session_id,
                message=M2_FIND_PIZZA,
                turn_id="t2",
                rounds=rounds,
                patch_poi=True,
                before_state=before_state,
                before_session=before_session,
            )

    async def _turn3(
        self,
        *,
        mode: str,
        session: dict,
        session_id: str,
        before_state: dict,
        before_session: SessionProjection,
    ):
        rounds = [
            complete_turn_round(
                "tu3-done",
                "I can compare how easy each of these places is to reach.",
            ),
        ]
        return await self._run_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            message=M3_WHICH_EASIEST,
            turn_id="t3",
            rounds=rounds,
            before_state=before_state,
            before_session=before_session,
        )

    async def _turn4(
        self,
        *,
        mode: str,
        session: dict,
        session_id: str,
        place2: dict,
        before_state: dict,
        before_session: SessionProjection,
    ):
        # The waypoints entry is the EXACT real ordinal-2 opaque id read back
        # from the real store (never a retyped label); the accepted destination
        # is inherited as "Barclays".
        rounds = [
            _turn_round(
                "prepare_route_options",
                "tu4-prepare",
                {
                    "destination": DESTINATION_LABEL,
                    "waypoints": [place2["place_id"]],
                },
            ),
            _turn_round(
                "present_route",
                "tu4-present",
                {"candidate_id": FIXED_CANDIDATE_WAYPOINT},
            ),
            text_round("Here is your route with the pizza stop first."),
        ]
        return await self._run_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            message=M4_SECOND_FIRST,
            turn_id="t4",
            rounds=rounds,
            prepare_legs=waypoint_segment_legs(place2),
            fixed_candidate_id=FIXED_CANDIDATE_WAYPOINT,
            before_state=before_state,
            before_session=before_session,
        )

    async def _turn5(
        self,
        *,
        mode: str,
        session: dict,
        session_id: str,
        before_state: dict,
        before_session: SessionProjection,
    ):
        rounds = [
            _turn_round(
                "prepare_route_options",
                "tu5-prepare",
                {"destination": DESTINATION_LABEL, "waypoints": []},
            ),
            _turn_round(
                "present_route",
                "tu5-present",
                {"candidate_id": FIXED_CANDIDATE_REMOVAL},
            ),
            text_round("Done -- the pizza stop is removed."),
        ]
        return await self._run_turn(
            mode=mode,
            session=session,
            session_id=session_id,
            message=M5_REMOVE_STOP,
            turn_id="t5",
            rounds=rounds,
            prepare_leg=barclays_leg(),
            fixed_candidate_id=FIXED_CANDIDATE_REMOVAL,
            before_state=before_state,
            before_session=before_session,
        )

    # ------------------------------------------------------------------
    # Shared evidence helpers (used by the per-turn invariants in the test)
    # ------------------------------------------------------------------

    def _names(self, ev: TurnEvidence) -> list:
        return [name for name, _input in ev.trace.tool_calls]

    def _end_map(self, ev: TurnEvidence) -> dict:
        return {
            event.tool: (event.ok, event.summary)
            for event in ev.events
            if event.type == "tool_end"
        }

    def _facts(self, state: dict | None) -> str:
        return (
            f"destination: {(state or {}).get('destination')!r}, "
            f"waypoints: {(state or {}).get('waypoints')}, "
            f"discovery_set: {(state or {}).get('active_discovery_set_id')!r}, "
            f"selected_place: {(state or {}).get('selected_place_id')!r}, "
            f"candidate_set: {(state or {}).get('active_candidate_set_id')!r}, "
            f"selected_candidate: {(state or {}).get('selected_candidate_id')!r}"
        )

    def _evidence(
        self,
        *,
        scenario_id: str,
        mode: str,
        message: str,
        ev: TurnEvidence,
        extra: str | None = None,
    ) -> str:
        trip = _context_trip_state(ev.context) or {}
        parts = [
            f"{scenario_id} mode={mode}",
            f"message={message!r}",
            f"offered={sorted(ev.offered)}",
            f"executed={self._names(ev)}",
            f"tool_ends={self._end_map(ev)}",
            f"before={{{self._facts(ev.before_state)}}}",
            f"after={{{self._facts(ev.state)}}}",
            f"cards={len(route_cards(ev.events))}",
            "ctx={"
            f"active_discovery: {'active_discovery:' in ev.context}, "
            f"trip_state: {json.dumps(trip, sort_keys=True)}, "
            f"destination: {trip.get('destination')!r}, "
            f"waypoints: {trip.get('waypoints')!r}, "
            f"has_selected_place: {trip.get('has_selected_place')!r}, "
            f"has_active_discovery_set: {trip.get('has_active_discovery_set')!r}, "
            f"has_active_candidate_set: {trip.get('has_active_candidate_set')!r}, "
            f"has_selected_candidate: {trip.get('has_selected_candidate')!r}"
            "}",
        ]
        if extra:
            parts.append(extra)
        return "; ".join(parts)

    def _policy_ok(self, scenario_id: str, ev: TurnEvidence, mode: str, calls: int) -> None:
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual(
            (ev.trace.initial_mode, ev.trace.final_mode),
            (expected_mode, expected_mode),
            f"{scenario_id} policy mode",
        )
        self.assertEqual(
            list(ev.models),
            [expected_model] * calls,
            f"{scenario_id} policy models",
        )

    def _no_temp(self, scenario_id: str, state: dict) -> None:
        self.assertEqual(
            tuple(state[field] for field in TEMP_FIELDS),
            (None, None, None),
            f"{scenario_id} no temporary scenario",
        )

    def _unchanged(self, scenario_id: str, tag: str, state: dict, before: dict, blob: str) -> None:
        for field in UNCHANGED_FIELDS:
            self.assertEqual(
                state[field],
                before[field],
                f"{scenario_id} {tag} keeps {field}; {blob}",
            )


def _context_trip_state(context: str) -> dict | None:
    """Parse the real ``trip_state: {...}`` JSON out of the request context."""

    match = re.search(r"trip_state: (\{.*\})", context)
    if match is None:
        return None
    try:
        value = json.loads(match.group(1))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


__all__ = (
    "SessionProjection",
    "TEMP_FIELDS",
    "TurnEvidence",
    "UNCHANGED_FIELDS",
    "_DiscoveryWaypointBase",
    "_context_trip_state",
)
