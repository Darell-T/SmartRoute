"""Batch H: deterministic long mixed-domain conversation state retention.

Drives the *real* agent loop (``app.services.agent.loop.run_agent_turn``)
with production intent/tool filtering, the real registered ``TOOL_REGISTRY``
executors, the real candidate/discovery/trip stores, the real tool ledger,
and the real SSE path across three long transcripts in ONE server-owned
session each: H-01 (Auto, 10 turns), H-02 (Quick, 21 turns), H-03 (Auto,
32 turns). All credited routing operations use the actual canonical
registered tools ``prepare_route_options -> present_route`` (never fake
``plan_trip`` / advisor / ``[ROUTE:N]``); model inference is scripted only
at the inference boundary and provider/network only at true external seams.
Every turn proves its exact offered tool profile, actual/forbidden tools,
and an immutable before/after projection of the server-owned state.

H-03 includes the discovery -> waypoint -> removal lifecycle because Batch
D1's multi-stop presentation gate is GREEN at execution time
(``test_conversation_discovery_waypoint.py`` passes); the metadata
justification lives in ``conversation_long_state_fixtures``.
"""

from __future__ import annotations

from app.services.agent import candidate_store
from app.services.agent import session as session_module
from app.services.agent import trip_state as trip_state_module

from tests.conversation.conversation_discovery_fixtures import discovery_leg_for
from tests.conversation.conversation_long_state_fixtures import (
    ALLOW_Q_MESSAGE,
    ARRIVAL_MESSAGE,
    AVOID_Q_MESSAGE,
    AVOID_STAIRS_MESSAGE,
    DISCOVERY_COFFEE_MESSAGE,
    DISCOVERY_PIZZA_MESSAGE,
    EXPLAIN_MESSAGE,
    EXPLANATION_TOOL_PROFILE,
    PREVIEW_BUS_MESSAGE,
    PREVIEW_LATER_MESSAGE,
    PREVIEW_TEN_MESSAGE,
    RECOVERY_ARRIVAL_MESSAGE,
    RETURN_STATUS_MESSAGE,
    ROUTE_BARCLAYS_MESSAGE,
    ROUTE_MUSEUM_MESSAGE,
    ROUTE_WORK_MESSAGE,
    SELECT_SECOND_MESSAGE,
    SELECT_THIRD_MESSAGE,
    STATUS_MESSAGE,
    TEMPORAL_DEPARTURE,
    TEN_MIN_DEPARTURE,
    WAYPOINT_ADD_MESSAGE,
    WAYPOINT_REMOVE_MESSAGE,
    bus_leg,
    q_leg,
    r_leg,
    waypoint_chain_legs,
)
from tests.conversation.conversation_long_state_support import (
    _LongStateBase,
    coffee_poi_result,
    load_h_agent_loop,
    stored_place,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    complete_turn_round,
    route_cards,
)


class H02QuickTwentyOneTurnTests(_LongStateBase):
    """H-02: Quick, ~21 turns of the full mixed-domain lifecycle."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_h_agent_loop()

    async def test_h02_quick_twenty_one_turn_state_retention(self):
        session_id, session = self._new_session("h02")
        # t1 canonical route (Quick uses the same Agent-led contract).
        _e, _t, _m, _b, _a, set1, _card_a = await self._route_turn(
            scenario_id="H-02-t1", session=session, session_id=session_id,
            mode="quick", message=ROUTE_WORK_MESSAGE, turn_id="t1",
            destination="Work", provider_leg=q_leg("Work"),
            candidate_id="cd_h02_1",
            expected_state={"destination": "Work"})
        # t2-t3 status and arrival preserve the active trip.
        await self._status_turn(
            scenario_id="H-02-t2", session=session, session_id=session_id,
            mode="quick", message=STATUS_MESSAGE, turn_id="t2",
        )
        await self._arrival_turn(
            scenario_id="H-02-t3", session=session, session_id=session_id,
            mode="quick", message=ARRIVAL_MESSAGE, turn_id="t3")
        # t4 discovery-only (no routing, no candidate set); t5 selection.
        _e, _t, _m, _b, _a, ds1 = await self._discovery_turn(
            scenario_id="H-02-t4", session=session, session_id=session_id,
            mode="quick", message=DISCOVERY_PIZZA_MESSAGE, turn_id="t4",
            search_input={"query": "pizza Brooklyn", "max_results": 3})
        _e, _t, _b, _a, pl2 = await self._select_turn(
            scenario_id="H-02-t5", session=session, session_id=session_id,
            mode="quick", message=SELECT_SECOND_MESSAGE, turn_id="t5",
            ordinal=2)
        # t6 route to the selected canonical place (stored identity wins).
        _e, _t, _m, _b, _a, _set2, _card_b = await self._route_selected_turn(
            scenario_id="H-02-t6", session=session, session_id=session_id,
            mode="quick", turn_id="t6", place_id=pl2,
            provider_leg=discovery_leg_for(stored_place(session_id, ds1, 2)),
            candidate_id="cd_h02_2", expected_destination="B Pizza")
        # t7 explanation-only (no replan, no candidate set).
        await self._no_tool_turn(
            scenario_id="H-02-t7", session=session, session_id=session_id,
            mode="quick", message=EXPLAIN_MESSAGE, turn_id="t7",
            profile=EXPLANATION_TOOL_PROFILE,
            text="I picked the Q because it is the fastest option to B Pizza.")
        # t8-t9 what-if preview then accept -- commit exactly once.
        _e, _t, _b, _a, temp_set1 = await self._preview_turn(
            scenario_id="H-02-t8", session=session, session_id=session_id,
            mode="quick", message=PREVIEW_LATER_MESSAGE, turn_id="t8",
            destination="B Pizza",
            prepare_input={"destination": "B Pizza",
                           "departure_time": TEMPORAL_DEPARTURE},
            provider_leg=q_leg("B Pizza"), candidate_id="cd_h02_p1")
        _e, _t, _b, _a, _cs, _card_c = await self._accept_turn(
            scenario_id="H-02-t9", session=session, session_id=session_id,
            mode="quick", turn_id="t9", candidate_id="cd_h02_p1")
        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == temp_set1, "H-02 t9"
        assert state["planning_mode"] == "depart_at", "H-02 t9"
        assert state["requested_departure"] == TEMPORAL_DEPARTURE, "H-02 t9"
        # t10 unrelated simple turn; t11-t12 status and arrival.
        await self._simple_turn(
            scenario_id="H-02-t10", session=session, session_id=session_id,
            mode="quick", turn_id="t10")
        await self._status_turn(
            scenario_id="H-02-t11", session=session, session_id=session_id,
            mode="quick", message=STATUS_MESSAGE, turn_id="t11",
        )
        await self._arrival_turn(
            scenario_id="H-02-t12", session=session, session_id=session_id,
            mode="quick", message=ARRIVAL_MESSAGE, turn_id="t12")
        # t13 constraint change (avoid Q); t14 relaxation (allow Q again).
        _e, _tr, _m, _b, _a, _set3, _card_d = await self._route_turn(
            scenario_id="H-02-t13", session=session, session_id=session_id,
            mode="quick", message=AVOID_Q_MESSAGE, turn_id="t13",
            destination="B Pizza", provider_leg=r_leg("B Pizza"),
            prepare_input={"destination": "B Pizza", "excluded_route_ids": ["Q"]},
            candidate_id="cd_h02_3", expected_excluded_route_ids=["Q"])
        _e, _tr, _m, _b, _a, _set4, _card_e = await self._route_turn(
            scenario_id="H-02-t14", session=session, session_id=session_id,
            mode="quick", message=ALLOW_Q_MESSAGE, turn_id="t14",
            destination="B Pizza", provider_leg=q_leg("B Pizza"),
            prepare_input={"destination": "B Pizza", "allowed_route_ids": ["Q"]},
            candidate_id="cd_h02_4")
        assert _tr.tool_calls[0][1].get("excluded_route_ids") == [], "H-02 t14 relaxed"
        assert ((session.get("slots") or {}).get("constraints", {}).get("excluded_route_ids") or []) == [], "H-02 t14 persisted relaxation"
        # t15 correction/new destination (Barclays) -- new-trip reset.
        _e, _t, _m, _b, _a, _set5, _card_f = await self._route_turn(
            scenario_id="H-02-t15", session=session, session_id=session_id,
            mode="quick", message=ROUTE_BARCLAYS_MESSAGE, turn_id="t15",
            destination="Barclays", provider_leg=q_leg("Barclays"),
            candidate_id="cd_h02_5",
            expected_state={"destination": "Barclays"})
        state = trip_state_module.get_trip_state(session)
        assert state["active_discovery_set_id"] == ds1, "H-02 t15 context"
        assert state["selected_place_id"] == pl2, "H-02 t15 context"
        # t16 return question; t17 recovery arrival (active boarding grounded).
        await self._status_turn(
            scenario_id="H-02-t16", session=session, session_id=session_id,
            mode="quick", message=RETURN_STATUS_MESSAGE, turn_id="t16",
        )
        await self._arrival_turn(
            scenario_id="H-02-t17", session=session, session_id=session_id,
            mode="quick", message=RECOVERY_ARRIVAL_MESSAGE, turn_id="t17")
        # t18-t19 second what-if (bus) preview then accept (BUS preference).
        _e, _t, _b, _a, temp_set2 = await self._preview_turn(
            scenario_id="H-02-t18", session=session, session_id=session_id,
            mode="quick", message=PREVIEW_BUS_MESSAGE, turn_id="t18",
            destination="Barclays",
            prepare_input={"destination": "Barclays",
                           "preferred_modes": ["BUS"]},
            provider_leg=bus_leg("Barclays"), candidate_id="cd_h02_p2")
        _e, _t, _b, _a, _cs2, _card_g = await self._accept_turn(
            scenario_id="H-02-t19", session=session, session_id=session_id,
            mode="quick", turn_id="t19", candidate_id="cd_h02_p2")
        assert session["profile"]["preferences"]["preferred_modes"] == ["BUS"], "H-02 t19 BUS preference applied exactly once"
        # t20 final current-trip question; t21 guarded stale probe.
        await self._status_turn(
            scenario_id="H-02-t20", session=session, session_id=session_id,
            mode="quick", message=RETURN_STATUS_MESSAGE, turn_id="t20",
        )
        await self._stale_probe(
            scenario_id="H-02-t21", session=session, session_id=session_id,
            mode="quick", turn_id="t21", stale_candidate_id="cd_h02_5",
            expected_active_set_id=temp_set2)
        # Final: bus accept is the active selection; stale discovery and
        # candidate references stay stored but never active again.
        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == temp_set2, "H-02 final"
        assert state["selected_candidate_id"] == "cd_h02_p2", "H-02 final"
        assert state["active_discovery_set_id"] == ds1, "H-02 final"
        assert state["selected_place_id"] == pl2, "H-02 final"
        assert candidate_store.load_candidate_set(set1, session_id=session_id)["presented"], "H-02 old sets stay consumed historical records"


class H03AutoThirtyTwoTurnTests(_LongStateBase):
    """H-03: Auto, 32-turn mixed transcript with bounded-history recovery."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_h_agent_loop()

    async def _h03_through_barclays_destination(self, session, session_id):
        # t1 route planning; t2-t4 status, arrival, explanation (no replan).
        _e, _t, _m, _b, _a, set_a, _card_a = await self._route_turn(
            scenario_id="H-03-t1", session=session, session_id=session_id,
            mode="auto", message=ROUTE_WORK_MESSAGE, turn_id="t1",
            destination="Work", provider_leg=q_leg("Work"),
            candidate_id="cd_h03_1")
        await self._status_turn(
            scenario_id="H-03-t2", session=session, session_id=session_id,
            mode="auto", message=STATUS_MESSAGE, turn_id="t2",
        )
        await self._arrival_turn(
            scenario_id="H-03-t3", session=session, session_id=session_id,
            mode="auto", message=ARRIVAL_MESSAGE, turn_id="t3")
        await self._no_tool_turn(
            scenario_id="H-03-t4", session=session, session_id=session_id,
            mode="auto", message=EXPLAIN_MESSAGE, turn_id="t4",
            profile=EXPLANATION_TOOL_PROFILE,
            text="I picked the Q because it is the fastest option to Work.")
        # t5-t7 discovery -> selection -> canonical route to selected place.
        _e, _t, _m, _b, _a, ds1 = await self._discovery_turn(
            scenario_id="H-03-t5", session=session, session_id=session_id,
            mode="auto", message=DISCOVERY_PIZZA_MESSAGE, turn_id="t5",
            search_input={"query": "pizza Brooklyn", "max_results": 3})
        _e, _t, _b, _a, pl2 = await self._select_turn(
            scenario_id="H-03-t6", session=session, session_id=session_id,
            mode="auto", message=SELECT_SECOND_MESSAGE, turn_id="t6",
            ordinal=2)
        _e, _t, _m, _b, _a, _set_b, _card_b = await self._route_selected_turn(
            scenario_id="H-03-t7", session=session, session_id=session_id,
            mode="auto", turn_id="t7", place_id=pl2,
            provider_leg=discovery_leg_for(stored_place(session_id, ds1, 2)),
            candidate_id="cd_h03_2", expected_destination="B Pizza")
        # t8-t9 discovery -> waypoint -> removal lifecycle (Batch D green at
        # execution time) on the accepted B Pizza trip, same active set.
        place3 = stored_place(session_id, ds1, 3)
        _e, _t, _m, _b, _a, _set_wp, _card_wp = await self._route_turn(
            scenario_id="H-03-t8", session=session, session_id=session_id,
            mode="auto", message=WAYPOINT_ADD_MESSAGE, turn_id="t8",
            destination="B Pizza",
            prepare_input={"destination": "B Pizza",
                           "waypoints": [place3["place_id"]]},
            prepare_legs=waypoint_chain_legs(place3, "B Pizza"),
            candidate_id="cd_h03_w1", expected_waypoints=["C Pizza"])
        _e, _t, _m, _b, _a, set_wp2, _card_wp2 = await self._route_turn(
            scenario_id="H-03-t9", session=session, session_id=session_id,
            mode="auto", message=WAYPOINT_REMOVE_MESSAGE, turn_id="t9",
            destination="B Pizza",
            prepare_input={"destination": "B Pizza", "waypoints": []},
            provider_leg=q_leg("B Pizza"), candidate_id="cd_h03_w2",
            expected_waypoints=[])
        # t10-t11 temporary what-if preview then REJECT (active stays).
        _e, _t, _b, _a, _p1 = await self._preview_turn(
            scenario_id="H-03-t10", session=session, session_id=session_id,
            mode="auto", message=PREVIEW_LATER_MESSAGE, turn_id="t10",
            destination="B Pizza",
            prepare_input={"destination": "B Pizza",
                           "departure_time": TEMPORAL_DEPARTURE},
            provider_leg=q_leg("B Pizza"), candidate_id="cd_h03_p1")
        await self._reject_turn(
            scenario_id="H-03-t11", session=session, session_id=session_id,
            mode="auto", turn_id="t11")
        assert trip_state_module.get_trip_state(session)["active_candidate_set_id"] == set_wp2, "H-03 t11 reject preserves the accepted trip"
        # t12-t13 temporary what-if (bus) preview then ACCEPT.
        _e, _t, _b, _a, _temp_bus = await self._preview_turn(
            scenario_id="H-03-t12", session=session, session_id=session_id,
            mode="auto", message=PREVIEW_BUS_MESSAGE, turn_id="t12",
            destination="B Pizza",
            prepare_input={"destination": "B Pizza",
                           "preferred_modes": ["BUS"]},
            provider_leg=bus_leg("B Pizza"), candidate_id="cd_h03_p2")
        _e, _t, _b, _a, _cb, _card_c = await self._accept_turn(
            scenario_id="H-03-t13", session=session, session_id=session_id,
            mode="auto", turn_id="t13", candidate_id="cd_h03_p2")
        # t14 return to current trip several turns later; t15 arrival.
        await self._status_turn(
            scenario_id="H-03-t14", session=session, session_id=session_id,
            mode="auto", message=RETURN_STATUS_MESSAGE, turn_id="t14",
        )
        await self._arrival_turn(
            scenario_id="H-03-t15", session=session, session_id=session_id,
            mode="auto", message=ARRIVAL_MESSAGE, turn_id="t15")
        # t16 constraint change (avoid Q); t17 relaxation (allow Q again).
        _e, _t, _m, _b, _a, _set_d, _card_d = await self._route_turn(
            scenario_id="H-03-t16", session=session, session_id=session_id,
            mode="auto", message=AVOID_Q_MESSAGE, turn_id="t16",
            destination="B Pizza", provider_leg=r_leg("B Pizza"),
            prepare_input={"destination": "B Pizza", "excluded_route_ids": ["Q"]},
            candidate_id="cd_h03_3", expected_excluded_route_ids=["Q"])
        _e, _t, _m, _b, _a, _set_e, _card_e = await self._route_turn(
            scenario_id="H-03-t17", session=session, session_id=session_id,
            mode="auto", message=ALLOW_Q_MESSAGE, turn_id="t17",
            destination="B Pizza", provider_leg=q_leg("B Pizza"),
            prepare_input={"destination": "B Pizza", "allowed_route_ids": ["Q"]},
            candidate_id="cd_h03_4")
        assert ((session.get("slots") or {}).get("constraints", {}).get("excluded_route_ids") or []) == [], "H-03 t17 persisted relaxation"
        # t18 new destination (Barclays) -- clears only obsolete state.
        _e, _t, _m, _b, _a, _set_f, _card_f = await self._route_turn(
            scenario_id="H-03-t18", session=session, session_id=session_id,
            mode="auto", message=ROUTE_BARCLAYS_MESSAGE, turn_id="t18",
            destination="Barclays", provider_leg=q_leg("Barclays"),
            candidate_id="cd_h03_5",
            expected_state={"destination": "Barclays"})
        state = trip_state_module.get_trip_state(session)
        assert state["active_discovery_set_id"] == ds1, "H-03 t18 context"
        assert state["selected_place_id"] == pl2, "H-03 t18 context"
        return set_a

    async def _h03_through_bound_reload(self, session, session_id, set_a):
        # t19-t21 second discovery -> selection -> route (coffee).
        _e, _t, _m, _b, _a, ds2 = await self._discovery_turn(
            scenario_id="H-03-t19", session=session, session_id=session_id,
            mode="auto", message=DISCOVERY_COFFEE_MESSAGE, turn_id="t19",
            search_input={"query": "coffee Barclays", "max_results": 3},
            poi_result_override=coffee_poi_result())
        _e, _t, _b, _a, pl3 = await self._select_turn(
            scenario_id="H-03-t20", session=session, session_id=session_id,
            mode="auto", message=SELECT_THIRD_MESSAGE, turn_id="t20",
            ordinal=3)
        _e, _t, _m, _b, _a, _set_g, _card_g = await self._route_selected_turn(
            scenario_id="H-03-t21", session=session, session_id=session_id,
            mode="auto", turn_id="t21", place_id=pl3,
            provider_leg=discovery_leg_for(stored_place(session_id, ds2, 3)),
            candidate_id="cd_h03_6", expected_destination="C Coffee")
        # t22-t23 what-if preview then accept (10 minutes later).
        _e, _t, _b, _a, _temp_ten = await self._preview_turn(
            scenario_id="H-03-t22", session=session, session_id=session_id,
            mode="auto", message=PREVIEW_TEN_MESSAGE, turn_id="t22",
            destination="C Coffee",
            prepare_input={"destination": "C Coffee",
                           "departure_time": TEN_MIN_DEPARTURE},
            provider_leg=q_leg("C Coffee"), candidate_id="cd_h03_p3")
        _e, _t, _b, _a, _ct, _card_h = await self._accept_turn(
            scenario_id="H-03-t23", session=session, session_id=session_id,
            mode="auto", turn_id="t23", candidate_id="cd_h03_p3")
        assert trip_state_module.get_trip_state(session)["requested_departure"] == TEN_MIN_DEPARTURE, "H-03 t23"
        # t24 unrelated simple turn.
        await self._simple_turn(
            scenario_id="H-03-t24", session=session, session_id=session_id,
            mode="auto", turn_id="t24")
        # t25 new destination -- supersedes the several-turn-old F/G/H trip.
        _e, _t, _m, _b, _a, set_i, _card_i = await self._route_turn(
            scenario_id="H-03-t25", session=session, session_id=session_id,
            mode="auto", message=ROUTE_MUSEUM_MESSAGE, turn_id="t25",
            destination="Museum of Natural History",
            provider_leg=q_leg("Museum of Natural History"),
            candidate_id="cd_h03_7",
            expected_state={"destination": "Museum of Natural History"})
        # t26 explanation grounded in current trip; t27 recovery arrival.
        await self._no_tool_turn(
            scenario_id="H-03-t26", session=session, session_id=session_id,
            mode="auto", message=EXPLAIN_MESSAGE, turn_id="t26",
            profile=EXPLANATION_TOOL_PROFILE,
            text="I picked the Q because it is the fastest option to the Museum.")
        await self._arrival_turn(
            scenario_id="H-03-t27", session=session, session_id=session_id,
            mode="auto", message=RECOVERY_ARRIVAL_MESSAGE, turn_id="t27")
        # t28-t29 temporary what-if (bus) preview then REJECT (active stays).
        _e, _t, _b, _a, _p4 = await self._preview_turn(
            scenario_id="H-03-t28", session=session, session_id=session_id,
            mode="auto", message=PREVIEW_BUS_MESSAGE, turn_id="t28",
            destination="Museum of Natural History",
            prepare_input={"destination": "Museum of Natural History",
                           "preferred_modes": ["BUS"]},
            provider_leg=bus_leg("Museum of Natural History"),
            candidate_id="cd_h03_p4")
        await self._reject_turn(
            scenario_id="H-03-t29", session=session, session_id=session_id,
            mode="auto", turn_id="t29")
        assert trip_state_module.get_trip_state(session)["active_candidate_set_id"] == set_i, "H-03 t29 reject preserves the accepted trip"
        # t30 constraint change -- avoid stairs: the fixture route has unknown
        # accessibility, so the canonical prepare returns a non-presentable
        # no_hard_constraint_match (no card, no present); the accepted trip
        # stays bound while the preference persists.
        _e30, _tr30, mk30, _bf30, _af30 = await self._run(
            session=session, session_id=session_id, mode="auto",
            message=AVOID_STAIRS_MESSAGE, turn_id="t30",
            rounds=[_turn_round("prepare_route_options", "tu-t30-p",
                                {"destination": "Museum of Natural History",
                                 "avoid_stairs": True}),
                    complete_turn_round(
                        "tu-t30-done",
                        "I could not find a route that avoids stairs.",
                        outcome="unavailable",
                    )],
            prepare_leg=q_leg("Museum of Natural History"), mocks={})
        assert [n for n, _i in _tr30.tool_calls] == ["prepare_route_options", "complete_turn"], "H-03 t30 prepares then terminates truthfully"
        assert route_cards(_e30) == [], "H-03 t30 no card"
        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == set_i, "H-03 t30"
        assert state["selected_candidate_id"] == "cd_h03_7", "H-03 t30"
        assert state["preferences"]["avoid_stairs"], "H-03 t30"
        assert state["preferences"]["accessibility_required"], "H-03 t30"
        audit = candidate_store.load_candidate_set(
            mk30["stored_candidate_set_ids"][0], session_id=session_id)
        assert audit["route_status"] == "no_hard_constraint_match", "H-03 t30 audit set"
        assert not audit["presented"], "H-03 t30 unconsumed"
        # t31 final return to current trip; t32 guarded stale probe (the
        # several-turn-old relaxed-Q candidate E cannot resurrect).
        await self._status_turn(
            scenario_id="H-03-t31", session=session, session_id=session_id,
            mode="auto", message=RETURN_STATUS_MESSAGE, turn_id="t31",
        )
        await self._stale_probe(
            scenario_id="H-03-t32", session=session, session_id=session_id,
            mode="auto", turn_id="t32", stale_candidate_id="cd_h03_4",
            expected_active_set_id=set_i)
        # Bounded session/history behavior: save/load trims history and cards
        # but never erases the authoritative trip or resurrects old state.
        session_module.save_session(session_id, session)
        loaded = session_module.load_session(session_id)
        assert loaded is not None, "H-03 reload"
        assert len(loaded["history"]) <= 12, "H-03 history bound"
        assert len(loaded["route_cards"]) <= 8, "H-03 card bound"
        state = trip_state_module.get_trip_state(loaded)
        assert state["active_candidate_set_id"] == set_i, "H-03 reload"
        assert state["selected_candidate_id"] == "cd_h03_7", "H-03 reload"
        assert state["destination"] == "Museum of Natural History", "H-03 reload"
        assert (loaded.get("active_trip") or {}).get("card_id") == _card_i.card_id, "H-03 reload keeps the current card"
        assert state["active_discovery_set_id"] == ds2, "H-03 reload"
        assert state["selected_place_id"] == pl3, "H-03 reload"
        assert candidate_store.load_candidate_set(set_a, session_id=session_id)["presented"], "H-03 old sets stay consumed historical records"

    async def test_h03_auto_thirty_two_turn_state_retention(self):
        session_id, session = self._new_session("h03")
        set_a = await self._h03_through_barclays_destination(session, session_id)
        await self._h03_through_bound_reload(session, session_id, set_a)
