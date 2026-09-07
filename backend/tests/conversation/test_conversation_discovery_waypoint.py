"""Batch D1: discovery -> waypoint -> removal transcript (Auto + Quick).

Drives the exact five-turn transcript required by the Batch D1 audit in ONE
real server-owned session, for both Auto and Quick:

  turn 1: "Get me to Barclays."               (accepted destination route)
  turn 2: "Find pizza near Barclays."         (discovery, no route mutation)
  turn 3: "Which one is easiest to reach?"    (comparison, no trip mutation)
  turn 4: "Take me to the second one first."  (ordinal-2 intermediate waypoint)
  turn 5: "Actually remove the pizza stop."   (waypoint removal, restore trip)

The real loop (``app.services.agent.loop.run_agent_turn``), production
state-scoped tool filtering, the real registered
executors for ``search_local_places`` / ``get_place_details`` /
``prepare_route_options`` / ``present_route``, the real discovery/candidate/
trip stores, the ledger, and the SSE event path run untouched. Only the
established provider/data seams (see ``conversation_discovery_waypoint_fixtures``)
and deterministic Anthropic rounds are scripted. Real ids are read back from
the real stores between turns.

Every per-turn invariant lives in ``conversation_discovery_waypoint_assertions``
so this driver stays a thin transcript runner. The OFFERED tool profile is
asserted BEFORE any state produced by a scripted tool call is credited on
every state-aware turn -- a scripted unoffered tool can never create a false
pass. Turn 5 runs only after turn 4's required gates pass; if turn 4 fails,
turn 5 is recorded blocked/not executed and the test stops at the earliest
missing production capability with the compact evidence blob.
"""

from __future__ import annotations

import pytest
from app.services.agent import discovery_store

from tests.conversation import (
    conversation_discovery_waypoint_assertions as _waypoint_assertions,
)
from tests.conversation import (
    conversation_discovery_waypoint_support as _waypoint_support,
)
from tests.conversation.conversation_discovery_fixtures import LEAK_MARKERS
from tests.conversation.conversation_discovery_waypoint_assertions import (
    _DiscoveryWaypointAssertions,
)
from tests.conversation.conversation_discovery_waypoint_fixtures import (
    CONTEXT_LEAK_MARKERS,
    DESTINATION_LABEL,
    FIXED_CANDIDATE_BARCLAYS,
    M1_GET_BARCLAYS,
    M2_FIND_PIZZA,
    M3_WHICH_EASIEST,
    TURN1_FORBIDDEN,
    TURN2_FORBIDDEN,
    TURN3_FORBIDDEN,
)
from tests.conversation.conversation_discovery_waypoint_support import TurnEvidence
from tests.conversation.conversation_matrix_harness import load_agent_loop, route_cards

INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)

# Local expectations for turns one and two; the inherited assertion module is
# rebound below without mutating the shared fixture values.
TURN1_EXPECTED_PROFILE = INITIAL_TOOL_PROFILE
TURN2_EXPECTED_PROFILE = INITIAL_TOOL_PROFILE


def _goal_for_call(name: str, tool_input: dict) -> tuple[str, str] | None:
    if name in {"discover_places", "present_places"}:
        return "places", "place_recommendation"
    if name in {"prepare_route_options", "present_route"}:
        return "route", "route"
    if name in {"check_transit", "present_transit"}:
        operation = str(tool_input.get("operation") or "service_status")
        kind = {
            "arrivals": "arrivals",
            "accessibility": "accessibility",
            "fact": "transit_fact",
            "area_conditions": "area_conditions",
            "event_schedule": "event_or_crowd",
            "venue_crowd_window": "event_or_crowd",
        }.get(operation, "service_status")
        return "transit", kind
    if name == "complete_turn":
        return "response", "general_response"
    return None


def _declared_call(call: dict) -> dict:
    name = str(call.get("name") or "")
    tool_input = dict(call.get("input") or {})
    goal = _goal_for_call(name, tool_input)
    if goal is not None:
        key, _kind = goal
        if name == "complete_turn":
            tool_input.pop("goal_key", None)
            tool_input["goal_keys"] = [key]
        else:
            tool_input["goal_key"] = key
    return {**call, "input": tool_input}


def _declared_rounds(rounds: list[dict]) -> list[dict]:
    goals: list[dict] = []
    seen: set[str] = set()
    for scripted in rounds:
        for call in scripted.get("tool_use") or []:
            goal = _goal_for_call(
                str(call.get("name") or ""), call.get("input") or {}
            )
            if goal is None:
                continue
            key, kind = goal
            if key not in seen:
                goals.append({"goal_key": key, "kind": kind, "depends_on": []})
                seen.add(key)
    if not goals:
        return rounds
    adapted: list[dict] = []
    declared = False
    for scripted in rounds:
        tool_uses = scripted.get("tool_use") or []
        if not tool_uses:
            adapted.append(scripted)
            continue
        transformed = [_declared_call(call) for call in tool_uses]
        if not declared:
            transformed.insert(
                0,
                {
                    "id": "tu-goals",
                    "name": "declare_goals",
                    "input": {"goals": goals},
                },
            )
            declared = True
        adapted.append({**scripted, "tool_use": transformed})
    return adapted


async def _model_led_run_turn(loop, *args, **kwargs):
    original = _model_led_run_turn.original
    if not getattr(loop, "_model_led_contract_test", False):
        return await original(loop, *args, **kwargs)
    kwargs["rounds"] = _declared_rounds(kwargs.get("rounds") or [])
    events, trace = await original(loop, *args, **kwargs)
    raw_calls = list(trace.tool_calls)
    if raw_calls:
        if raw_calls[0][0] != "declare_goals":
            pytest.fail("model-led waypoint turn must declare goals first")
        offered = frozenset(
            schema["name"] for schema in loop.client.messages.calls[0]["tools"]
        )
        if offered != INITIAL_TOOL_PROFILE:
            pytest.fail(
                f"unexpected initial waypoint tool profile: {sorted(offered)}"
            )
        trace.model_led_tool_calls = raw_calls
        trace.tool_calls = [call for call in raw_calls if call[0] != "declare_goals"]
    return events, trace


if not getattr(_waypoint_support.run_turn, "_model_led_adapter", False):
    _model_led_run_turn.original = _waypoint_support.run_turn
    _model_led_run_turn._model_led_adapter = True
    _waypoint_support.run_turn = _model_led_run_turn

_ORIGINAL_WAYPOINT_PROFILES = {
    _module: {
        _name: getattr(_module, _name)
        for _name in dir(_module)
        if _name.endswith("_PROFILE")
        and isinstance(getattr(_module, _name), set)
    }
    for _module in (_waypoint_assertions, _waypoint_support)
}


def _activate_model_led_contract(loop) -> None:
    for _module, _profiles in _ORIGINAL_WAYPOINT_PROFILES.items():
        for _name in _profiles:
            setattr(_module, _name, set(INITIAL_TOOL_PROFILE))
    loop._model_led_contract_test = True


def _deactivate_model_led_contract(loop) -> None:
    loop._model_led_contract_test = False
    for _module, _profiles in _ORIGINAL_WAYPOINT_PROFILES.items():
        for _name, _value in _profiles.items():
            setattr(_module, _name, _value)


class DiscoveryWaypointTranscriptTests(_DiscoveryWaypointAssertions):
    """D-WP-01 / D-WP-02: the exact five-turn lifecycle in one session."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()
        _activate_model_led_contract(cls.loop)

    @classmethod
    def tearDownClass(cls):
        _deactivate_model_led_contract(cls.loop)

    # ------------------------------------------------------------------
    # Turn 1: accepted destination route (no waypoints)
    # ------------------------------------------------------------------

    def _assert_turn1(self, *, scenario_id: str, mode: str, ev: TurnEvidence):
        blob = self._evidence(
            scenario_id=scenario_id,
            mode=mode,
            message=M1_GET_BARCLAYS,
            ev=ev,
        )
        assert ev.offered == TURN1_EXPECTED_PROFILE, blob
        names = self._names(ev)
        assert names == ["prepare_route_options", "present_route"], f"{scenario_id} turn1 sequence; {blob}"
        assert names.count("prepare_route_options") == 1, blob
        assert names.count("present_route") == 1, blob
        assert not set(names) & set(TURN1_FORBIDDEN), f"{scenario_id} turn1 forbidden tool; {blob}"
        assert ev.mocks["prepare_single_leg"].await_count == 1, f"{scenario_id} turn1 provider seam once; {blob}"
        cards = route_cards(ev.events)
        assert len(cards) == 1, f"{scenario_id} turn1 one card; {blob}"
        assert len(ev.mocks["stored_candidate_set_ids"]) == 1, f"{scenario_id} turn1 one stored set; {blob}"
        state = ev.state
        assert state["active_candidate_set_id"] == ev.mocks["stored_candidate_set_ids"][0], f"{scenario_id} turn1 accepted set; {blob}"
        assert state["selected_candidate_id"] == FIXED_CANDIDATE_BARCLAYS, f"{scenario_id} turn1 accepted candidate; {blob}"
        assert state["destination"] == DESTINATION_LABEL, blob
        assert state["waypoints"] == [], blob
        assert state["active_discovery_set_id"] is None, blob
        assert state["selected_place_id"] is None, blob
        self._no_temp(scenario_id, state)
        assert cards[0].destination.get("label") == DESTINATION_LABEL, blob
        assert ev.events[-1].type == "done", blob
        assert (ev.after_session.active_trip or {}).get("card_id") == cards[0].card_id, f"{scenario_id} turn1 active trip card; {blob}"
        self._policy_ok(scenario_id, ev, mode, calls=2)
        self._assert_no_leaks(scenario_id, "turn1", ev.trace.final_text)

    # ------------------------------------------------------------------
    # Turn 2: discovery without route mutation
    # ------------------------------------------------------------------

    def _assert_turn2(self, *, scenario_id: str, mode: str, ev: TurnEvidence):
        blob = self._evidence(
            scenario_id=scenario_id,
            mode=mode,
            message=M2_FIND_PIZZA,
            ev=ev,
        )
        assert ev.offered == TURN2_EXPECTED_PROFILE, blob
        names = self._names(ev)
        assert names == ["discover_places", "present_places"], f"{scenario_id} t2 seq; {blob}"
        assert not set(names) & set(TURN2_FORBIDDEN), f"{scenario_id} turn2 forbidden tool; {blob}"
        state = ev.state
        set_id = state["active_discovery_set_id"]
        assert set_id
        assert set_id.startswith("ds_"), f"{scenario_id} turn2 real discovery set; {blob}"
        record = discovery_store.load_discovery_set(set_id, session_id=ev.session_id)
        assert record is not None, f"{scenario_id} turn2 stored record; {blob}"
        assert [place["ordinal"] for place in record["places"]] == [1, 2, 3], f"{scenario_id} turn2 stored ordinals"
        assert record["places"][1]["name"] == "B Pizza", f"{scenario_id} turn2 ordinal 2"
        assert record["places"][1]["provider_place_id"] == "ChIJ-bbb", f"{scenario_id} turn2 ordinal-2 provider identity"
        for place in record["places"]:
            assert place["place_id"].startswith("pl_"), f"{scenario_id} turn2 opaque place id"
        assert ev.before_state is not None, f"{scenario_id} turn2 before state"
        self._unchanged(scenario_id, "turn2", state, ev.before_state, blob)
        assert state["active_candidate_set_id"] == ev.before_state["active_candidate_set_id"], f"{scenario_id} turn2 accepted set unchanged; {blob}"
        assert state["selected_candidate_id"] == ev.before_state["selected_candidate_id"], f"{scenario_id} turn2 accepted candidate unchanged; {blob}"
        assert state["selected_place_id"] is None, blob
        assert route_cards(ev.events) == [], blob
        assert ev.mocks["stored_candidate_set_ids"] == [], blob
        assert ev.events[-1].type == "done", blob
        self._assert_session_unchanged(scenario_id, "turn2", ev)
        for marker in CONTEXT_LEAK_MARKERS:
            assert marker not in ev.context, f"{scenario_id} ctx leak {marker}"
            assert marker not in ev.result_blob, f"{scenario_id} result leak {marker}"
        assert ev.trace.final_text.count("A Pizza") == 1, blob
        assert ev.trace.final_text.count("B Pizza") == 1, blob
        for marker in ("pl_", "ds_", "latitude", "ChIJ"):
            assert marker not in ev.trace.final_text, f"{scenario_id} text leak {marker}"
        self._policy_ok(scenario_id, ev, mode, calls=2)
        self._assert_no_leaks(scenario_id, "turn2", ev.trace.final_text)

    # ------------------------------------------------------------------
    # Turn 3: comparison question, no authoritative trip mutation
    # ------------------------------------------------------------------

    def _assert_turn3(self, *, scenario_id: str, mode: str, ev: TurnEvidence):
        blob = self._evidence(
            scenario_id=scenario_id,
            mode=mode,
            message=M3_WHICH_EASIEST,
            ev=ev,
        )
        names = self._names(ev)
        assert names == ["complete_turn"], f"{scenario_id} turn3 terminal only; {blob}"
        assert not set(names) & set(TURN3_FORBIDDEN), f"{scenario_id} turn3 forbidden tool; {blob}"
        assert ev.before_state is not None, f"{scenario_id} turn3 before state"
        self._unchanged(scenario_id, "turn3", ev.state, ev.before_state, blob)
        assert ev.state["active_discovery_set_id"] == ev.before_state["active_discovery_set_id"], f"{scenario_id} turn3 discovery set; {blob}"
        assert ev.state["selected_place_id"] == ev.before_state["selected_place_id"], f"{scenario_id} turn3 selected place; {blob}"
        assert route_cards(ev.events) == [], blob
        self._no_temp(scenario_id, ev.state)
        self._assert_session_unchanged(scenario_id, "turn3", ev)
        assert "active_discovery:" in ev.context, blob
        assert DESTINATION_LABEL in ev.context, blob
        for marker in CONTEXT_LEAK_MARKERS:
            assert marker not in ev.context, f"{scenario_id} ctx leak {marker}"
        self._policy_ok(scenario_id, ev, mode, calls=1)
        self._assert_no_leaks(scenario_id, "turn3", ev.trace.final_text)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _assert_no_leaks(self, scenario_id: str, tag: str, text: str) -> None:
        lowered = text.casefold()
        for marker in LEAK_MARKERS:
            assert marker not in lowered, f"{scenario_id} {tag} leak {marker}"

    def _assert_session_unchanged(
        self,
        scenario_id: str,
        tag: str,
        ev: TurnEvidence,
    ) -> None:
        """The accepted trip/card/slots survive the turn unchanged."""

        assert ev.before_session is not None, f"{scenario_id} {tag} before session"
        assert ev.after_session.active_trip == ev.before_session.active_trip, f"{scenario_id} {tag} active trip unchanged"
        assert ev.after_session.route_cards == ev.before_session.route_cards, f"{scenario_id} {tag} route cards unchanged"
        assert ev.after_session.slots == ev.before_session.slots, f"{scenario_id} {tag} slots unchanged"

    async def _transcript(self, mode: str, scenario_id: str):
        session_id, session = self._new_session(mode)
        ev1 = await self._turn1(mode=mode, session=session, session_id=session_id)
        self._assert_turn1(scenario_id=scenario_id, mode=mode, ev=ev1)
        ev2 = await self._turn2(
            mode=mode,
            session=session,
            session_id=session_id,
            before_state=ev1.state,
            before_session=ev1.after_session,
        )
        self._assert_turn2(scenario_id=scenario_id, mode=mode, ev=ev2)
        set_id = ev2.state["active_discovery_set_id"]
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        assert record is not None, f"{scenario_id} real stored discovery record"
        place2 = record["places"][1]
        ev3 = await self._turn3(
            mode=mode,
            session=session,
            session_id=session_id,
            before_state=ev2.state,
            before_session=ev2.after_session,
        )
        self._assert_turn3(scenario_id=scenario_id, mode=mode, ev=ev3)
        ev4 = await self._turn4(
            mode=mode,
            session=session,
            session_id=session_id,
            place2=place2,
            before_state=ev3.state,
            before_session=ev3.after_session,
        )
        self._assert_turn4(
            scenario_id=scenario_id,
            mode=mode,
            set_id=set_id,
            place2=place2,
            ev=ev4,
        )
        # Turn 5 runs only after turn 4's required gates pass; on failure the
        # assertion above stops the test and turn 5 is recorded blocked.
        ev5 = await self._turn5(
            mode=mode,
            session=session,
            session_id=session_id,
            before_state=ev4.state,
            before_session=ev4.after_session,
        )
        self._assert_turn5(scenario_id=scenario_id, mode=mode, ev=ev5)

    async def test_d_wp_01_auto(self):
        await self._transcript("auto", "D-WP-01")


__all__ = ()
