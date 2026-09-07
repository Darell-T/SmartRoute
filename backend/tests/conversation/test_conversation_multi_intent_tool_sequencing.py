"""Batch F2 audit: multi-intent conversational sequencing and negative tools.

Every scenario drives the REAL agent loop, real ``TOOL_REGISTRY`` executors,
ledger, stores, and SSE path; Anthropic inference is scripted through
``tests._fake_anthropic`` and only genuine provider/data/id seams are patched.
Each test first asserts the exact per-turn OFFERED tool profile before
crediting any scripted outcome, and separates attempted rejected calls (paired
ToolStart/ToolEnd failures) from executor-authorized ``trace.tool_calls``.
Auto and Quick run inside one method per case; scenario ids name the mode.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.agent import candidate_store

import tests.conversation.conversation_multi_intent_fixtures as f2
from tests.conversation.conversation_discovery_fixtures import (
    discovery_leg_for,
    poi_result,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    check_transit_input,
    complete_turn_round,
    discover_search_input,
    load_agent_loop,
    present_places_round,
)
from tests.conversation.conversation_multi_intent_support import (
    _MultiIntentBase,
    _preamble_normalized,
    fail_loud_spy,
)

_alerts_fetch = f2.ALERTS_FETCH_SEAM
_alerts_parse = f2.ALERTS_PARSE_SEAM
_poi_seam = f2.POI_SEAM
_prepare_seam = f2.PREPARE_SEAM


def _alerts_seams() -> dict:
    return {
        "alerts_fetch": (_alerts_fetch, AsyncMock(return_value=b"fixture-alerts")),
        "alerts_parse": (
            _alerts_parse,
            MagicMock(return_value=f2.q_alerts_fixture()),
        ),
        "stalled_trains": (
            "app.services.mta.realtime.get_stalled_trains",
            AsyncMock(return_value=[]),
        ),
        "incident_lookup": (
            "app.services.incidents.index.lookup_incidents",
            MagicMock(return_value={"incidents": [], "coverage_status": "current"}),
        ),
    }


def _search_round(tool_id: str = "tu-2s") -> dict:
    return _turn_round(
        "discover_places",
        tool_id,
        discover_search_input("pizza near Barclays", borough="Brooklyn"),
    )


def _status_round(tool_id: str) -> dict:
    return _turn_round(
        "check_transit",
        tool_id,
        check_transit_input(
            "service_status",
            route_ids=["Q"],
            direction="uptown",
        ),
    )


def _present_transit_round(tool_id: str) -> dict:
    return _turn_round(
        "present_transit",
        tool_id,
        {"goal_key": "status"},
    )


def _present_places_round(tool_id: str) -> dict:
    return present_places_round(
        tool_id,
        f2.DISCOVERY_SET_ID,
        f2.DISCOVERY_PLACE_IDS,
    )


def _mixed_round() -> dict:
    """One adversarial round: offered search + registered-unoffered snapshot."""
    return {
        "tool_use": [
            {
                "id": "tu-8a",
                "name": "discover_places",
                "input": discover_search_input(
                    "pizza near Barclays", borough="Brooklyn"
                ),
            },
            {"id": "tu-8b", "name": "transit_snapshot",
             "input": dict(f2.TRANSIT_SNAPSHOT_Q_INPUT)},
        ],
        "stop_reason": "tool_use",
    }


class RoutePlusStatusSequencingTests(_MultiIntentBase):
    """F2-01: route + status compound on the route surface."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_01_route_plus_status_executes_status_then_route(self):
        for mode in ("auto",):
            sid = f"F2-01a-{mode}"
            seams = _alerts_seams()
            rounds = [
                _status_round("tu-1s"),
                _present_transit_round("tu-1t"),
                _turn_round("prepare_route_options", "tu-1p",
                            dict(f2.PREPARE_TIMES_SQUARE_INPUT)),
                _turn_round("present_route", "tu-1c",
                            {"candidate_id": f2.FIXED_CANDIDATE_ID}),
            ]
            ev = await self._probe(mode=mode, message=f2.ROUTE_PLUS_STATUS_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   prepare_leg=f2.times_square_leg(),
                                   fixed_candidate_id=f2.FIXED_CANDIDATE_ID)
            self._assert_offered_exact(
                ev, f2.ROUTE_PLUS_STATUS_TOOL_PROFILE, sid
            )
            self._assert_policy(ev, mode, sid, model_calls=4)
            self._assert_executed(
                ev,
                (
                    "check_transit",
                    "present_transit",
                    "prepare_route_options",
                    "present_route",
                ),
                sid,
            )
            self._assert_declared_goals(
                ev,
                (
                    {"goal_key": "status", "kind": "service_status", "depends_on": []},
                    {"goal_key": "route", "kind": "route", "depends_on": []},
                ),
                sid,
            )
            self._assert_state_valid_presenter(ev, "present_transit", sid)
            self._assert_one_card(ev, sid, expected_selected=f2.FIXED_CANDIDATE_ID)
            assert ev.state_after["trip_state"]["destination"] == "Times Square", sid
            assert "check_transit" in ev.state_after["history_tool_summaries"]

    async def test_02_route_plus_status_validator_fills_missing_status(self):
        for mode in ("auto",):
            sid = f"F2-01c-{mode}"
            seams = _alerts_seams()
            rounds = [
                _turn_round("prepare_route_options", "tu-1p",
                            dict(f2.PREPARE_TIMES_SQUARE_INPUT)),
                _status_round("tu-1s"),
                _present_transit_round("tu-1t"),
                _turn_round("present_route", "tu-1c",
                            {"candidate_id": f2.FIXED_CANDIDATE_ID}),
            ]
            ev = await self._probe(mode=mode, message=f2.ROUTE_PLUS_STATUS_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   prepare_leg=f2.times_square_leg(),
                                   fixed_candidate_id=f2.FIXED_CANDIDATE_ID)
            self._assert_offered_exact(
                ev, f2.ROUTE_PLUS_STATUS_TOOL_PROFILE, sid
            )
            self._assert_policy(ev, mode, sid, model_calls=4)
            self._assert_executed(
                ev,
                (
                    "prepare_route_options",
                    "check_transit",
                    "present_transit",
                    "present_route",
                ),
                sid,
            )
            self._assert_declared_goals(
                ev,
                (
                    {"goal_key": "status", "kind": "service_status", "depends_on": []},
                    {"goal_key": "route", "kind": "route", "depends_on": []},
                ),
                sid,
            )
            self._assert_state_valid_presenter(ev, "present_transit", sid)
            self._assert_one_card(ev, sid, expected_selected=f2.FIXED_CANDIDATE_ID)
            assert ev.spies["alerts_fetch"].await_count == 1, ev.compact()
            assert self.loop.client.messages.calls[1]["tool_choice"] == {"type": "any"}
            assert ev.final_text.strip(), sid


class DiscoveryPlusRouteSequencingTests(_MultiIntentBase):
    """F2-02: discovery + route-to-second compound in one turn."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_03_discovery_route_second_one(self):
        for mode in ("auto",):
            sid = f"F2-02a-{mode}"
            seams = {"poi": (_poi_seam, AsyncMock(return_value=poi_result())),
                     "prepare": (_prepare_seam,
                                 AsyncMock(return_value=discovery_leg_for(f2.stored_place2())))}
            rounds = [
                _search_round("tu-2s"),
                _turn_round("prepare_route_options", "tu-2p",
                            {"destination": f2.CONFLICTING_LABEL,
                             "destination_place_id": f2.ORDINAL_TWO_PLACE_ID}),
                _turn_round("present_route", "tu-2c",
                            {"candidate_id": f2.FIXED_CANDIDATE_ID}),
            ]
            ev = await self._probe(mode=mode, message=f2.DISCOVERY_PLUS_ROUTE_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   discovery_set_id=f2.DISCOVERY_SET_ID,
                                   place_ids=f2.DISCOVERY_PLACE_IDS,
                                   fixed_candidate_id=f2.FIXED_CANDIDATE_ID)
            self._assert_offered_exact(ev, f2.DISCOVERY_ROUTE_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=3)
            self._assert_executed(
                ev,
                (
                    "discover_places",
                    "prepare_route_options",
                    "present_route",
                ),
                sid,
            )
            self._assert_declared_goals(
                ev,
                (
                    {
                        "goal_key": "destination",
                        "kind": "destination_selection",
                        "depends_on": [],
                    },
                    {
                        "goal_key": "route",
                        "kind": "route",
                        "depends_on": ["destination"],
                    },
                ),
                sid,
            )
            assert ev.tool_calls[1][1]["destination_place_id"] == f2.ORDINAL_TWO_PLACE_ID, f"{sid}: real ordinal-2 opaque id; {ev.compact()}"
            resolved = ev.spies["prepare"].await_args.kwargs.get("resolved_destination")
            assert resolved is not None, f"{sid}: provider boundary place"
            assert resolved.name == "B Pizza", sid
            assert resolved.place_id == f2.ORDINAL_TWO_PLACE_ID, sid
            assert resolved.provider_place_id == "ChIJ-bbb", sid
            assert ev.spies["prepare"].await_count == 1, sid
            self._assert_one_card(ev, sid, expected_selected=f2.FIXED_CANDIDATE_ID)
            self._assert_discovery_bound(ev, sid)
            assert ev.state_after["trip_state"]["selected_place_id"] == f2.ORDINAL_TWO_PLACE_ID, sid
            assert ev.state_after["trip_state"]["destination"] == "B Pizza", sid
            assert "web_search" not in [t for t, *_r in ev.tool_ends], sid

    async def test_03b_ramen_compound_cannot_stop_after_place_presentation(self):
        message = "Find a good ramen spot and route me there by subway."
        ramen_result = poi_result()
        for place, name in zip(
            ramen_result.data["results"],
            ("Ichiran Ramen", "Kajiken Ramen", "Tonchin Ramen"), strict=False,
        ):
            place["name"] = name
        selected_ramen = dict(f2.stored_place2())
        selected_ramen["name"] = "Kajiken Ramen"
        seams = {
            "poi": (_poi_seam, AsyncMock(return_value=ramen_result)),
            "prepare": (
                _prepare_seam,
                AsyncMock(return_value=discovery_leg_for(selected_ramen)),
            ),
        }
        rounds = [
            _turn_round(
                "discover_places",
                "tu-ramen-search",
                discover_search_input("ramen", borough=None),
            ),
            _turn_round(
                "prepare_route_options",
                "tu-ramen-prepare",
                {
                    "destination_place_id": f2.ORDINAL_TWO_PLACE_ID,
                    "exclude_modes": ["BUS"],
                },
            ),
            _turn_round(
                "present_route",
                "tu-ramen-present",
                {"candidate_id": f2.FIXED_CANDIDATE_ID},
            ),
        ]

        for mode in ("auto",):
            sid = f"F2-02b-{mode}"
            ev = await self._probe(
                mode=mode,
                message=message,
                rounds=rounds,
                seams=seams,
                discovery_set_id=f2.DISCOVERY_SET_ID,
                place_ids=f2.DISCOVERY_PLACE_IDS,
                prepare_leg=discovery_leg_for(selected_ramen),
                fixed_candidate_id=f2.FIXED_CANDIDATE_ID,
            )

            self._assert_executed(
                ev,
                (
                    "discover_places",
                    "prepare_route_options",
                    "present_route",
                ),
                sid,
            )
            assert ev.tool_calls[1][1].get("exclude_modes") == ["BUS"]
            self._assert_one_card(
                ev,
                sid,
                expected_selected=f2.FIXED_CANDIDATE_ID,
            )


class CompareRouteSequencingTests(_MultiIntentBase):
    """F2-03: compound compare on the status surface vs the route surface."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_04_compare_exact_message_safe_partial(self):
        for mode in ("auto",):
            sid = f"F2-03a-{mode}"
            rounds = [
                _turn_round("prepare_route_options", "tu-3a-p", {}),
                complete_turn_round(
                    "tu-3a-done",
                    "Where are you traveling from and to?",
                    outcome="clarification",
                ),
            ]
            ev = await self._probe(mode=mode, message=f2.COMPARE_EXACT_MESSAGE,
                                   rounds=rounds)
            self._assert_offered_exact(ev, f2.ROUTE_TOOL_PROFILE, sid)
            self._assert_policy(
                ev,
                mode,
                sid,
                model_calls=2,
                stop_reason="clarification_required",
            )
            self._assert_executed(
                ev, ("prepare_route_options", "complete_turn"), sid
            )
            self._assert_no_card(ev, sid)
            self._assert_no_discovery(ev, sid)
            assert ev.state_after["trip_state"] == ev.state_before["trip_state"], f"{sid}: clarification must not mutate canonical trip state"
            assert ev.state_after["route_cards"] == ev.state_before["route_cards"], f"{sid}: clarification must not add a route card"
            assert "accessibility_status" not in [t for t, _i in ev.tool_calls], sid
            # The default preference dump always shows accessibility_required;
            # only an activated hard constraint (true) would be a fabrication.
            assert '"accessibility_required":true' not in ev.context, sid

    async def test_05_compare_route_two_candidates_one_card(self):
        for mode in ("auto",):
            sid = f"F2-03c-{mode}"
            seams = {"prepare": (_prepare_seam,
                                 AsyncMock(return_value=f2.work_two_routes_leg()))}
            rounds = [
                _turn_round("prepare_route_options", "tu-3p",
                            dict(f2.PREPARE_WORK_INPUT)),
                _turn_round("present_route", "tu-3c",
                            {"candidate_id": f2.TWO_CANDIDATE_IDS[1]}),
            ]
            ev = await self._probe(mode=mode, message=f2.COMPARE_ROUTE_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   new_candidate_ids=f2.TWO_CANDIDATE_IDS)
            self._assert_offered_exact(ev, f2.ROUTE_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=2)
            self._assert_executed(ev, ("prepare_route_options", "present_route"), sid)
            assert ev.spies["prepare"].await_count == 1, sid
            self._assert_one_card(ev, sid, expected_selected=f2.TWO_CANDIDATE_IDS[1])
            prepare_input = ev.tool_calls[0][1]
            assert prepare_input.get("avoid_stairs") is None, sid
            assert prepare_input.get("accessibility_required") is None, sid
            assert "accessibility_status" not in [t for t, _i in ev.tool_calls], sid
            record = candidate_store.load_candidate_set(ev.stored_candidate_set_ids[0],
                                                        session_id=ev.session_id)
            assert record is not None, sid
            assert len(record["candidates"]) == 2, f"{sid}: two candidates"
            assert record["route_status"] == "good", sid
            assert ev.state_after["trip_state"]["destination"] == "Work", sid


class StatusPlusReplanSequencingTests(_MultiIntentBase):
    """F2-04: status + conditional replan against an accepted trip."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_06_status_grounded_replan_uses_accepted_trip(self):
        for mode in ("auto",):
            sid = f"F2-04a-{mode}"
            seams = dict(_alerts_seams())
            seams["prepare"] = (
                _prepare_seam,
                AsyncMock(return_value=f2.r_only_work_leg()),
            )
            rounds = [
                _status_round("tu-4s"),
                _present_transit_round("tu-4t"),
                _turn_round("prepare_route_options", "tu-4p",
                            dict(f2.PREPARE_WORK_AVOID_Q_INPUT)),
                _turn_round(
                    "present_route",
                    "tu-4c",
                    {"candidate_id": f2.FIXED_CANDIDATE_ID},
                ),
            ]
            ev = await self._probe(mode=mode, message=f2.STATUS_PLUS_REPLAN_MESSAGE,
                                   rounds=rounds, seams=seams, seed=True,
                                   fixed_candidate_id=f2.FIXED_CANDIDATE_ID)
            self._assert_offered_exact(
                ev, f2.ROUTE_PLUS_STATUS_TOOL_PROFILE, sid
            )
            self._assert_policy(ev, mode, sid, model_calls=4)
            self._assert_executed(
                ev,
                (
                    "check_transit",
                    "present_transit",
                    "prepare_route_options",
                    "present_route",
                ),
                sid,
            )
            self._assert_declared_goals(
                ev,
                (
                    {"goal_key": "status", "kind": "service_status", "depends_on": []},
                    {"goal_key": "route", "kind": "route", "depends_on": []},
                ),
                sid,
            )
            ends = {t: (ok, summary) for t, ok, summary, _i in ev.tool_ends}
            assert ends["check_transit"][0], f"{sid}: grounded status"
            assert ends["check_transit"][1] is None, sid
            assert ev.tool_calls[2][1]["excluded_route_ids"] == ["Q"], sid
            self._assert_one_card(
                ev, sid, expected_selected=f2.FIXED_CANDIDATE_ID
            )
            assert ev.state_after["trip_state"]["active_candidate_set_id"] != ev.seed.candidate_set_id, sid


class DiscoveryPlusStatusSequencingTests(_MultiIntentBase):
    """F2-05: discovery + status compound without a navigation request."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_07_discovery_plus_status_no_navigation(self):
        for mode in ("auto",):
            sid = f"F2-05a-{mode}"
            seams = {
                "poi": (_poi_seam, AsyncMock(return_value=poi_result())),
                **_alerts_seams(),
            }
            rounds = [
                _search_round("tu-5s"),
                _status_round("tu-5t"),
                _present_transit_round("tu-5v"),
                _present_places_round("tu-5p"),
            ]
            ev = await self._probe(mode=mode, message=f2.DISCOVERY_PLUS_STATUS_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   discovery_set_id=f2.DISCOVERY_SET_ID,
                                   place_ids=f2.DISCOVERY_PLACE_IDS)
            self._assert_offered_exact(
                ev, f2.DISCOVERY_PLUS_STATUS_TOOL_PROFILE, sid
            )
            self._assert_policy(ev, mode, sid, model_calls=4)
            self._assert_executed(
                ev,
                (
                    "discover_places",
                    "check_transit",
                    "present_transit",
                    "present_places",
                ),
                sid,
            )
            self._assert_declared_goals(
                ev,
                (
                    {"goal_key": "places", "kind": "place_recommendation", "depends_on": []},
                    {"goal_key": "status", "kind": "service_status", "depends_on": []},
                ),
                sid,
            )
            self._assert_state_valid_presenter(ev, "present_transit", sid)
            self._assert_state_valid_presenter(ev, "present_places", sid)
            self._assert_discovery_bound(ev, sid)
            state = ev.state_after["trip_state"]
            assert state["selected_place_id"] is None, f"{sid}: no selection"
            assert state["destination"] is None, f"{sid}: no destination"
            assert state["active_candidate_set_id"] is None, f"{sid}: no candidate"
            assert ev.cards == (), sid


class RoutePlusExplainSequencingTests(_MultiIntentBase):
    """F2-06: route + explanation follow-up in one turn."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_08_route_plus_explain_one_chain(self):
        for mode in ("auto",):
            sid = f"F2-06a-{mode}"
            seams = {"prepare": (_prepare_seam,
                                 AsyncMock(return_value=f2.times_square_leg()))}
            rounds = [
                _turn_round("lookup_facts", "tu-6f", dict(f2.LOOKUP_FACTS_INPUT)),
                _turn_round("prepare_route_options", "tu-6p",
                            dict(f2.PREPARE_TIMES_SQUARE_INPUT)),
                _turn_round("present_route", "tu-6c",
                            {"candidate_id": f2.FIXED_CANDIDATE_ID}),
            ]
            ev = await self._probe(mode=mode, message=f2.ROUTE_PLUS_EXPLAIN_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   fixed_candidate_id=f2.FIXED_CANDIDATE_ID)
            self._assert_offered_exact(ev, f2.ROUTE_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=3)
            self._assert_rejected(ev, "lookup_facts", sid, zero_spies=())
            self._assert_executed(ev, ("prepare_route_options", "present_route"), sid)
            self._assert_one_card(ev, sid, expected_selected=f2.FIXED_CANDIDATE_ID)
            assert ev.spies["prepare"].await_count == 1, sid
            assert "lookup_facts" not in ev.state_after["history_tool_summaries"], sid
            assert ev.final_text.strip(), sid


class NegativeToolControlTests(_MultiIntentBase):
    """F2-07: negative-tool controls across pure and no-good turns."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_09_greeting_no_tools(self):
        for mode in ("auto",):
            sid = f"F2-07a-{mode}"
            ev = await self._probe(mode=mode, message=f2.GREETING_MESSAGE,
                                   rounds=[])
            assert ev.offered == f2.INITIAL_TOOL_PROFILE, sid
            assert ev.model_call_count == 2, sid
            assert ev.tool_calls == (), sid
            assert ev.tool_starts == (), sid
            assert ev.tool_ends == (), sid
            assert ev.cards == (), sid
            assert ev.final_text.strip(), sid
            assert _preamble_normalized(ev.state_after) == _preamble_normalized(ev.state_before), f"{sid}: greeting must not mutate state"

    async def test_10_status_only_no_route(self):
        for mode in ("auto",):
            sid = f"F2-07c-{mode}"
            rounds = [
                _status_round("tu-7s"),
                complete_turn_round(
                    "tu-7s-done",
                    "The Q is running with delays.",
                ),
            ]
            ev = await self._probe(mode=mode, message=f2.STATUS_ONLY_MESSAGE,
                                   rounds=rounds, seams=_alerts_seams())
            self._assert_offered_exact(ev, f2.TRANSIT_QUESTION_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=2)
            self._assert_executed(ev, ("check_transit", "present_transit"), sid)
            self._assert_declared_goals(
                ev,
                (
                    {"goal_key": "status", "kind": "service_status", "depends_on": []},
                ),
                sid,
            )
            self._assert_state_valid_presenter(ev, "present_transit", sid)
            self._assert_no_forbidden(ev, f2.STATUS_FORBIDDEN_TOOLS, sid)
            self._assert_no_card(ev, sid)
            self._assert_no_discovery(ev, sid)

    async def test_11_explain_only_no_route(self):
        for mode in ("auto",):
            sid = f"F2-07e-{mode}"
            ev = await self._probe(mode=mode, message=f2.EXPLAIN_ONLY_MESSAGE,
                                   rounds=[complete_turn_round(
                                       "tu-7e-done",
                                       "I picked the Q because it was the fastest option.",
                                   )],
                                   seed=True)
            self._assert_offered_exact(ev, f2.NO_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=1)
            self._assert_executed(ev, ("complete_turn",), sid)
            self._assert_seed_preserved(ev, sid)
            assert ev.cards == (), sid
            assert ev.stored_candidate_set_ids == (), sid

    async def test_12_discovery_only_no_route(self):
        for mode in ("auto",):
            sid = f"F2-07g-{mode}"
            seams = {"poi": (_poi_seam, AsyncMock(return_value=poi_result()))}
            rounds = [_search_round("tu-7d"), _present_places_round("tu-7p")]
            ev = await self._probe(mode=mode, message=f2.DISCOVERY_ONLY_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   discovery_set_id=f2.DISCOVERY_SET_ID,
                                   place_ids=f2.DISCOVERY_PLACE_IDS)
            self._assert_offered_exact(ev, f2.DISCOVERY_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=2)
            self._assert_executed(ev, ("discover_places", "present_places"), sid)
            self._assert_discovery_bound(ev, sid)
            assert ev.state_after["trip_state"]["selected_place_id"] is None, f"{sid}: discovery-only must not select a place"
            self._assert_no_card(ev, sid)

    async def test_13_no_good_no_silent_presentation(self):
        for mode in ("auto",):
            sid = f"F2-07i-{mode}"
            rounds = [
                _turn_round("prepare_route_options", "tu-7n",
                            dict(f2.PREPARE_WORK_AVOID_Q_INPUT)),
                complete_turn_round(
                    "tu-7n-done",
                    "I could not find a route that meets your constraints.",
                    outcome="unavailable",
                ),
            ]
            ev = await self._probe(mode=mode, message=f2.NO_GOOD_MESSAGE,
                                   rounds=rounds, prepare_leg=f2.q_only_work_leg(),
                                   seed=True)
            self._assert_offered_exact(ev, f2.ROUTE_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=2)
            self._assert_executed(ev, ("prepare_route_options", "complete_turn"), sid)
            terminal_attempts = [
                attempt
                for attempt in ev.capability_attempts
                if attempt["capability"] == "complete_turn"
            ]
            assert terminal_attempts, f"{sid}: truthful terminal"
            assert terminal_attempts[0]["ok"], f"{sid}: truthful terminal"
            assert ev.cards == (), f"{sid}: no silent presentation"
            assert len(ev.stored_candidate_set_ids) == 1, sid
            assert ev.stored_candidate_set_ids[0] != ev.seed.candidate_set_id, sid
            audit = candidate_store.load_candidate_set(ev.stored_candidate_set_ids[0],
                                                       session_id=ev.session_id)
            assert audit["route_status"] == "no_hard_constraint_match", sid
            self._assert_seed_preserved(ev, sid)
            self._assert_no_forbidden(ev, f2.ROUTE_FORBIDDEN_TOOLS, sid)


class MixedAdversarialRoundTests(_MultiIntentBase):
    """F2-08: one round emits an offered and a registered-unoffered tool."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_14_mixed_offered_and_unoffered_round(self):
        for mode in ("auto",):
            sid = f"F2-08a-{mode}"
            seams = {"poi": (_poi_seam, AsyncMock(return_value=poi_result())),
                     "alerts": (_alerts_fetch, fail_loud_spy("alerts"))}
            rounds = [_mixed_round(), _present_places_round("tu-8p")]
            ev = await self._probe(mode=mode, message=f2.DISCOVERY_ONLY_MESSAGE,
                                   rounds=rounds, seams=seams,
                                   discovery_set_id=f2.DISCOVERY_SET_ID,
                                   place_ids=f2.DISCOVERY_PLACE_IDS)
            self._assert_offered_exact(ev, f2.DISCOVERY_TOOL_PROFILE, sid)
            self._assert_policy(ev, mode, sid, model_calls=2)
            self._assert_executed(ev, ("discover_places", "present_places"), sid)
            self._assert_rejected(ev, "transit_snapshot", sid, zero_spies=("alerts",))
            assert ev.provider_execution_count == 2, f"{sid}: only offered ran"
            assert len(ev.tool_starts) == 1, f"{sid}: only rider-visible work emits starts"
            assert len(ev.tool_ends) == 2, f"{sid}: discovery and rejected leaf emit paired ends; " "the internal presenter stays hidden"
            self._assert_discovery_bound(ev, sid)
            assert ev.state_after["trip_state"]["selected_place_id"] is None, sid
            self._assert_no_card(ev, sid)


__all__ = ()
