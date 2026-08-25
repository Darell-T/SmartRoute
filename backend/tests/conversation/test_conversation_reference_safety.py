"""Batch E1 audit: expiry/stale/invented/cross-session discovery references.

E1-CASE1 Expired set: real search -> real ``ds_*`` set -> expiry through the
         real store TTL boundary (deterministic clock, no sleep) -> "The
         second one." fails safely and binds nothing (Auto + Quick).
E1-CASE2 Exact recovery "Okay, search again." must offer and execute only
         the minimum structured discovery search path. If production cannot
         offer it, the test FAILS AT THE OFFER GATE with P1 evidence; a
         control test proves the executor path works when discovery intent
         is recognized (Auto + Quick).
E1-CASE3 Stale selected place: "Take me there." must not resolve or present
         from the stale opaque place; a label-only prepare transcript
         (matching label, no opaque id) proves no text-label fallback
         authority (Auto + Quick; the direct executor probe lives with the
         destination-reference boundary tests).
E1-CASE4 Invented ds_*/pl_* values through real ``present_places`` inputs
         fail safely and bind nothing.
E1-CASE5 Cross-session set/place resolution is rejected with no session B
         mutation and no session A mutation.
E1-CASE6 Superseded sets: bounded natural references resolve only against
         the latest active set; explicit old-set references record the
         public presenter contract; an expired old set binds nothing.
E1-CASE7 Simultaneous separate discovery states stay distinct; profile,
         trip, and candidate fields stay untouched (Auto + Quick).
E1-CASE8 A new explicit route request after prior discovery resets the stale
         selection and still uses the normal destination/provider path.

Real loop, production state-valid public surface, registry/executors, stores,
prompt context, ledger, and events run untouched; only deterministic Anthropic
rounds and the external structured POI seam are scripted. Model-led rounds
declare goals before capabilities; presenters are used only after their
corresponding server-owned evidence exists. Real ids are read back from the
real store; invented ids appear only in the explicit malicious-input cases.
Offered tool profiles are asserted before any scripted tool state is credited.
"""

from __future__ import annotations

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.places import present_places
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    complete_turn_round,
    load_agent_loop,
    make_leg,
    new_session,
)
from tests.conversation.conversation_reference_safety_fixtures import (
    COFFEE_MESSAGE,
    CROSS_SESSION_ERROR_MARKER,
    DISCOVERY_MESSAGE,
    DISCOVERY_REFERENCE_TOOL_PROFILE,
    EXPIRED_ERROR_MARKER,
    INVENTED_PLACE_ID,
    INVENTED_SET_ID,
    PLACE_ID_UNKNOWN_MARKER,
    REFERENCE_MESSAGE,
    TRANSIT_QUESTION_TOOL_PROFILE,
    discovery_leg_for,
)
from tests.conversation.conversation_reference_safety_support import (
    _ReferenceSafetyBase,
    present_one_round,
)


class ExpiredSetSelectionFailsSafelyTests(_ReferenceSafetyBase):
    """E1-CASE1 (Auto + Quick): expired set, "The second one." binds nothing."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript(self, mode: str):
        scenario_id = f"E1C1-{mode}"
        session, session_id, set_id, record = await self._fresh_discovery(
            mode=mode, scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        # Once the owned discovery record is expired it is intentionally not
        # state-valid to offer ``present_places``.  The model-led terminal
        # response records the bounded unavailable result without attempting
        # to resurrect stale evidence.
        rounds = [
            complete_turn_round(
                "tu-ref-done",
                "I couldn't access those search results.",
            )
        ]
        ev = await self._expired_turn(
            mode=mode, session=session, session_id=session_id, record=record,
            message=REFERENCE_MESSAGE, rounds=rounds, turn_id="t2",
            prepare_leg=discovery_leg_for(record["places"][0]))
        self._assert_safe_reference_failure(
            scenario_id=scenario_id, mode=mode, set_id=set_id, ev=ev,
            message=REFERENCE_MESSAGE)

    async def test_e1_case1_expired_selection_auto(self):
        await self._transcript("auto")

    async def test_e1_case1_expired_selection_quick(self):
        await self._transcript("quick")


class RecoveryOfferGateTests(_ReferenceSafetyBase):
    """E1-CASE2 (Auto + Quick): exact recovery must offer the search path."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_e1_case2_recovery_offer_gate_auto(self):
        await self._case2_recovery("auto")

    async def test_e1_case2_recovery_offer_gate_quick(self):
        await self._case2_recovery("quick")

    async def test_e1_case2_control_new_search_creates_fresh_set_auto(self):
        await self._case2_control("auto")

    async def test_e1_case2_control_new_search_creates_fresh_set_quick(self):
        await self._case2_control("quick")


class StaleSelectedPlaceTests(_ReferenceSafetyBase):
    """E1-CASE3 (Auto + Quick): stale selection must never route or present."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_e1_case3_stale_selected_take_me_there_auto(self):
        await self._case3_stale_navigation("auto")

    async def test_e1_case3_stale_selected_take_me_there_quick(self):
        await self._case3_stale_navigation("quick")

    async def test_e1_case3_stale_label_only_loop_auto(self):
        await self._case3_stale_label_only_loop("auto")

    async def test_e1_case3_stale_label_only_loop_quick(self):
        await self._case3_stale_label_only_loop("quick")


class NewRouteAfterPriorDiscoveryTests(_ReferenceSafetyBase):
    """E1-CASE8: a new explicit route request after prior discovery works."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_e1_case8_new_route_after_prior_discovery(self):
        scenario_id = "E1C8-auto"
        session, session_id, set_id, record = await self._fresh_discovery(
            mode="auto", scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        place2 = record["places"][1]
        rounds_ref = [present_one_round("tu-ref", set_id, place2["place_id"])]
        ev_ref = await self._scripted_turn(
            mode="auto", session=session, session_id=session_id,
            message=REFERENCE_MESSAGE, rounds=rounds_ref, turn_id="t2",
            prepare_leg=discovery_leg_for(place2))
        self.assertEqual(ev_ref.state["selected_place_id"], place2["place_id"],
                         f"{scenario_id} prior discovery binds a selected place")
        rounds = [
            _turn_round(
                "prepare_route_options",
                "tu-new",
                {"destination": "Barclays Center"},
            ),
            complete_turn_round(
                "tu-new-done",
                "Here is the route to Barclays Center.",
                outcome="unavailable",
            ),
        ]
        ev = await self._scripted_turn(
            mode="auto", session=session, session_id=session_id,
            message="Take me to Barclays Center", rounds=rounds, turn_id="t3",
            prepare_leg=make_leg(destination="Barclays Center"))
        names = [name for name, _input in ev.trace.tool_calls]
        self.assertEqual(
            names[:2],
            ["declare_goals", "prepare_route_options"],
            f"{scenario_id} normal route preparation runs; executed={names}",
        )
        self.assertEqual(ev.mocks["prepare_single_leg"].await_count, 1,
                         f"{scenario_id} provider path reached for the new "
                         f"explicit destination; "
                         f"actual={ev.mocks['prepare_single_leg'].await_count}")
        self.assertEqual(ev.state["destination"], "Barclays Center",
                         f"{scenario_id} canonical destination committed")
        self._assert_policy(scenario_id, "auto", ev)


class InventedReferenceTests(_ReferenceSafetyBase):
    """E1-CASE4: invented ds_*/pl_* values fail safely and bind nothing."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    def _seed_active(self, session_id: str, session: dict) -> tuple[str, dict]:
        set_id = discovery_store.store_discovery_set(
            session_id=session_id,
            places=[{"name": "A Pizza", "latitude": 40.71, "longitude": -73.98},
                    {"name": "B Pizza", "latitude": 40.72, "longitude": -73.97}],
            query="pizza")
        trip_state_module.bind_discovery_set(session, set_id)
        return set_id, discovery_store.load_discovery_set(set_id, session_id=session_id)

    async def test_e1_case4_invented_place_id_binds_nothing(self):
        session_id, session = self._new_session("auto")
        set_id, _record = self._seed_active(session_id, session)
        ctx = self._tool_ctx(session, session_id)
        result = await present_places.execute(
            {
                "discovery_set_id": set_id,
                "selections": [
                    {"place_id": INVENTED_PLACE_ID, "reason": "preference_match"}
                ],
                "research_used": False,
            },
            ctx,
        )
        self.assertFalse(result.ok, "E1-CASE4 invented place id must fail")
        self.assertIn(PLACE_ID_UNKNOWN_MARKER, result.error or "",
                      f"E1-CASE4 error={result.error!r}")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["selected_place_id"], None, "E1-CASE4 binds nothing")
        self.assertEqual(state["active_discovery_set_id"], set_id,
                         "E1-CASE4 real active set untouched")

    async def test_e1_case4_invented_set_id_binds_nothing(self):
        session_id, session = self._new_session("auto")
        set_id, _record = self._seed_active(session_id, session)
        ctx = self._tool_ctx(session, session_id)
        result = await present_places.execute(
            {
                "discovery_set_id": INVENTED_SET_ID,
                "selections": [
                    {
                        "place_id": _record["places"][0]["place_id"],
                        "reason": "preference_match",
                    }
                ],
                "research_used": False,
            },
            ctx,
        )
        self.assertFalse(result.ok, "E1-CASE4 invented set id must fail")
        self.assertIn(EXPIRED_ERROR_MARKER, result.error or "",
                      f"E1-CASE4 error={result.error!r}")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["selected_place_id"], None, "E1-CASE4 binds nothing")
        self.assertEqual(state["active_discovery_set_id"], set_id,
                         "E1-CASE4 real active set untouched")

    async def test_e1_case4_invented_place_id_loop_auto(self):
        session, session_id, set_id, record = await self._fresh_discovery(
            mode="auto", scenario_id="E1C4", message=DISCOVERY_MESSAGE)
        rounds = [present_one_round("tu-inv", set_id, INVENTED_PLACE_ID)]
        ev = await self._scripted_turn(
            mode="auto", session=session, session_id=session_id,
            message=REFERENCE_MESSAGE, rounds=rounds, turn_id="t2",
            prepare_leg=discovery_leg_for(record["places"][0]))
        names = [name for name, _input in ev.trace.tool_calls]
        end_map = self._tool_ends(ev)
        self.assertEqual(ev.offered, DISCOVERY_REFERENCE_TOOL_PROFILE,
                         f"E1C4 offered={sorted(ev.offered)}; executed={names}; "
                         f"tool_ends={end_map}")
        self.assertEqual(
            names,
            ["declare_goals", "present_places"],
            "E1C4 executor sequence",
        )
        details_attempt = next(
            (
                attempt
                for attempt in ev.trace.capability_attempts
                if attempt["capability"] == "present_places"
            ),
            None,
        )
        self.assertTrue(
            details_attempt is not None and details_attempt["ok"] is False,
            f"E1C4 invented id must fail safely; "
            f"attempts={ev.trace.capability_attempts}",
        )
        self.assertNotIn(
            PLACE_ID_UNKNOWN_MARKER,
            ev.trace.final_text,
        )
        self.assertEqual(ev.state["selected_place_id"], None, "E1C4 binds no place")
        self.assertEqual(ev.state["active_discovery_set_id"], set_id,
                         "E1C4 set stays active")
        self._assert_no_route_surface("E1C4", ev)
        self._assert_no_text_leak("E1C4", ev)
        self._assert_policy("E1C4", "auto", ev)


class CrossSessionReferenceTests(_ReferenceSafetyBase):
    """E1-CASE5: cross-session set/place resolution is rejected cleanly."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _rejected_in_b(self, tool_input_factory) -> None:
        owner_id, other_id = "sess-e1-owner", "sess-e1-other"
        set_id = discovery_store.store_discovery_set(
            session_id=owner_id,
            places=[{"name": "A Pizza", "latitude": 40.71, "longitude": -73.98},
                    {"name": "B Pizza", "latitude": 40.72, "longitude": -73.97}],
            query="pizza")
        record = discovery_store.load_discovery_set(set_id, session_id=owner_id)
        _sid, session_b = new_session()
        ctx_b = self._tool_ctx(session_b, other_id)
        result = await present_places.execute(tool_input_factory(record), ctx_b)
        self.assertFalse(result.ok, "E1-CASE5 cross-session must be rejected")
        self.assertIn(CROSS_SESSION_ERROR_MARKER, result.error or "",
                      f"E1-CASE5 error={result.error!r}")
        state_b = trip_state_module.get_trip_state(session_b)
        self.assertEqual((state_b["active_discovery_set_id"],
                          state_b["selected_place_id"]), (None, None),
                         "E1-CASE5 no session B mutation")
        self.assertIsNotNone(
            discovery_store.load_discovery_set(set_id, session_id=owner_id),
            "E1-CASE5 session A set untouched")

    async def test_e1_case5_cross_session_set_rejected(self):
        await self._rejected_in_b(lambda record: {
            "discovery_set_id": record["discovery_set_id"],
            "selections": [
                {"place_id": record["places"][1]["place_id"],
                 "reason": "preference_match"}
            ],
            "research_used": False,
        })

    async def test_e1_case5_cross_session_place_rejected(self):
        await self._rejected_in_b(lambda record: {
            "discovery_set_id": record["discovery_set_id"],
            "selections": [
                {"place_id": record["places"][0]["place_id"],
                 "reason": "preference_match"}
            ],
            "research_used": False,
        })

    async def test_e1_case5_no_active_set_no_select_offer_auto(self):
        """Without an active set, no cross-session SELECT surface is offered."""
        session_id, session = self._new_session("auto")
        ev = await self._scripted_turn(
            mode="auto", session=session, session_id=session_id,
            message=REFERENCE_MESSAGE,
            rounds=[complete_turn_round(
                "tu-none",
                "I don't have any search results to pick from yet.",
            )],
            turn_id="t1")
        names = [name for name, _input in ev.trace.tool_calls]
        self.assertEqual(
            ev.offered, TRANSIT_QUESTION_TOOL_PROFILE,
            f"E1-CASE5 no active set still offers the model-led initial surface; "
            f"actual={sorted(ev.offered)}; "
            f"executed={names}")
        self.assertNotIn("present_places", ev.offered, "E1-CASE5 no presenter")
        self.assertIn("discover_places", ev.offered, "E1-CASE5 initial search capability remains available")
        self.assertEqual(
            names,
            ["declare_goals", "complete_turn"],
            "E1-CASE5 terminal only",
        )
        self.assertEqual((ev.state["active_discovery_set_id"],
                          ev.state["selected_place_id"]), (None, None),
                         "E1-CASE5 no session mutation without an active set")
        self._assert_no_route_surface(
            "E1-CASE5", ev,
            forbidden=("prepare_route_options", "present_route",
                       "present_places", "discover_places"))
        self._assert_policy("E1-CASE5", "auto", ev)


class SupersededSetTests(_ReferenceSafetyBase):
    """E1-CASE6 (Auto + Quick): latest-set authority and explicit-old contract."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript_default_latest(self, mode: str):
        scenario_id = f"E1C6-{mode}"
        session, session_id, set_a, record_a = await self._fresh_discovery(
            mode=mode, scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        session, session_id, set_b, record_b = await self._search_turn(
            mode=mode, scenario_id=scenario_id, session=session,
            session_id=session_id, message=COFFEE_MESSAGE, turn_id="t2")
        self.assertNotEqual(set_a, set_b,
                            f"{scenario_id} two searches create two real sets")
        self.assertEqual(trip_state_module.get_trip_state(session)["selected_place_id"],
                         None, f"{scenario_id} a new search clears the previous selection")
        rounds = [present_one_round("tu-ref", set_b, record_b["places"][1]["place_id"])]
        ev = await self._scripted_turn(
            mode=mode, session=session, session_id=session_id,
            message=REFERENCE_MESSAGE, rounds=rounds, turn_id="t3",
            prepare_leg=discovery_leg_for(record_b["places"][0]))
        names = [name for name, _input in ev.trace.tool_calls]
        end_map = self._tool_ends(ev)
        self.assertEqual(ev.offered, DISCOVERY_REFERENCE_TOOL_PROFILE,
                         f"{scenario_id} selection offers the model-led initial surface; "
                         f"actual={sorted(ev.offered)}; executed={names}; tool_ends={end_map}")
        self.assertEqual(
            names,
            ["declare_goals", "present_places"],
            f"{scenario_id} sequence",
        )
        details_attempt = next(
            (
                attempt
                for attempt in ev.trace.capability_attempts
                if attempt["capability"] == "present_places"
            ),
            None,
        )
        self.assertTrue(
            details_attempt is not None and details_attempt["ok"] is True,
            f"{scenario_id} latest-set ordinal must resolve; "
            f"attempts={ev.trace.capability_attempts}",
        )
        self.assertEqual(ev.state["active_discovery_set_id"], set_b,
                         f"{scenario_id} default reference targets only the latest active set")
        self.assertEqual(ev.state["selected_place_id"], record_b["places"][1]["place_id"],
                         f"{scenario_id} resolved place is B's stored ordinal-2")
        # Canonical place identity is stable across repeated searches in one
        # session. Prove latest-set authority through the session registry,
        # rather than treating a reused opaque id as evidence of an A-set
        # selection.
        latest_reference = max(
            (
                entry
                for entry in session.get("presented_entity_registry") or []
                if str(entry.get("place_id") or "")
                == str(record_b["places"][1]["place_id"])
            ),
            key=lambda entry: int(entry.get("presentation_sequence") or 0),
        )
        self.assertEqual(
            latest_reference["discovery_set_id"], set_b,
            f"{scenario_id} reference resolves from the latest presented set",
        )
        self.assertEqual(
            latest_reference["place_id"], record_b["places"][1]["place_id"],
            f"{scenario_id} latest registry identity matches B",
        )
        self._assert_no_route_surface(scenario_id, ev)
        self._assert_no_text_leak(scenario_id, ev)
        self._assert_policy(scenario_id, mode, ev)

    async def test_e1_case6_default_reference_targets_latest_auto(self):
        await self._transcript_default_latest("auto")

    async def test_e1_case6_default_reference_targets_latest_quick(self):
        await self._transcript_default_latest("quick")

    async def test_e1_case6_explicit_old_set_records_contract(self):
        """Record the current contract: an explicit old-set reference wins."""
        session_id, session = self._new_session("auto")
        places = [{"name": "A Pizza", "latitude": 40.71, "longitude": -73.98},
                  {"name": "B Pizza", "latitude": 40.72, "longitude": -73.97}]
        set_a = discovery_store.store_discovery_set(
            session_id=session_id, places=places, query="pizza")
        record_a = discovery_store.load_discovery_set(set_a, session_id=session_id)
        set_b = discovery_store.store_discovery_set(
            session_id=session_id, places=places, query="coffee")
        trip_state_module.bind_discovery_set(session, set_b)
        ctx = self._tool_ctx(session, session_id)
        result = await present_places.execute(
            {
                "discovery_set_id": set_a,
                "selections": [
                    {
                        "place_id": record_a["places"][1]["place_id"],
                        "reason": "preference_match",
                    }
                ],
                "research_used": False,
            },
            ctx,
        )
        self.assertTrue(result.ok,
                        f"E1-CASE6 explicit old-set must resolve; error={result.error!r}")
        self.assertEqual(
            (result.data or {}).get("presented"),
            [
                {
                    "place_id": record_a["places"][1]["place_id"],
                    "reason": "preference_match",
                }
            ],
            "E1-CASE6 explicit A resolves A's stored ordinal-2",
        )
        state = trip_state_module.get_trip_state(session)
        # The public presenter validates an explicitly supplied set but does
        # not rebind a newer active context.  Public model turns can only
        # expose the presenter for the active, session-owned set.
        self.assertEqual(state["active_discovery_set_id"], set_b,
                         "E1-CASE6 newer active set remains the route context")
        self.assertEqual(state["selected_place_id"], record_a["places"][1]["place_id"],
                         "E1-CASE6 explicit old set binds its place")

    async def test_e1_case6_explicit_expired_old_set_rejected(self):
        """An expired explicit old-set reference must bind nothing at all."""
        session_id, session = self._new_session("auto")
        set_a = discovery_store.store_discovery_set(
            session_id=session_id,
            places=[{"name": "B Pizza", "latitude": 40.72, "longitude": -73.97}],
            query="pizza")
        record_a = discovery_store.load_discovery_set(set_a, session_id=session_id)
        set_b = discovery_store.store_discovery_set(
            session_id=session_id,
            places=[{"name": "B Pizza", "latitude": 40.72, "longitude": -73.97}],
            query="coffee")
        trip_state_module.bind_discovery_set(session, set_b)
        ctx = self._tool_ctx(session, session_id)
        with self._expired_clock(record_a):
            result = await present_places.execute(
                {
                    "discovery_set_id": set_a,
                    "selections": [
                        {
                            "place_id": record_a["places"][0]["place_id"],
                            "reason": "preference_match",
                        }
                    ],
                    "research_used": False,
                },
                ctx,
            )
        self.assertFalse(result.ok,
                         "E1-CASE6 expired explicit old set must be rejected")
        self.assertIn(EXPIRED_ERROR_MARKER, result.error or "",
                      f"E1-CASE6 error={result.error!r}")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["active_discovery_set_id"], set_b,
                         "E1-CASE6 stale explicit A binds nothing; B stays active")
        self.assertEqual(state["selected_place_id"], None,
                         "E1-CASE6 stale explicit A selects nothing")


class SessionIsolationTests(_ReferenceSafetyBase):
    """E1-CASE7 (Auto + Quick): simultaneous discovery states stay distinct."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _transcript(self, mode: str):
        scenario_id = f"E1C7-{mode}"
        session_a, sid_a, set_a, record_a = await self._fresh_discovery(
            mode=mode, scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        session_b, sid_b, set_b, record_b = await self._fresh_discovery(
            mode=mode, scenario_id=scenario_id, message=DISCOVERY_MESSAGE)
        self.assertNotEqual(set_a, set_b,
                            f"{scenario_id} separate sessions get separate sets")
        rounds = [present_one_round("tu-ref", set_a, record_a["places"][1]["place_id"])]
        ev_a = await self._scripted_turn(
            mode=mode, session=session_a, session_id=sid_a,
            message=REFERENCE_MESSAGE, rounds=rounds, turn_id="t2",
            prepare_leg=discovery_leg_for(record_a["places"][0]))
        ev_b = await self._scripted_turn(
            mode=mode, session=session_b, session_id=sid_b,
            message=REFERENCE_MESSAGE,
            rounds=[present_one_round("tu-ref", set_b, record_b["places"][1]["place_id"])],
            turn_id="t2",
            prepare_leg=discovery_leg_for(record_b["places"][0]))
        self.assertEqual(ev_a.state["active_discovery_set_id"], set_a,
                         f"{scenario_id} A keeps its own set")
        self.assertEqual(ev_a.state["selected_place_id"],
                         record_a["places"][1]["place_id"],
                         f"{scenario_id} A binds its own ordinal-2")
        self.assertEqual(ev_b.state["active_discovery_set_id"], set_b,
                         f"{scenario_id} B keeps its own set")
        self.assertEqual(ev_b.state["selected_place_id"],
                         record_b["places"][1]["place_id"],
                         f"{scenario_id} B binds its own ordinal-2")
        self.assertNotEqual(record_a["places"][1]["place_id"],
                            record_b["places"][1]["place_id"],
                            f"{scenario_id} opaque place ids are per-set")
        self.assertNotIn(set_b, ev_a.state["active_discovery_set_id"] or "",
                         f"{scenario_id} no A leak")
        self.assertNotIn(set_a, ev_b.state["active_discovery_set_id"] or "",
                         f"{scenario_id} no B leak")
        for label, state in (("A", ev_a.state), ("B", ev_b.state)):
            self._assert_pristine_trip_state(f"{scenario_id}-{label}", state)
            self.assertEqual(state["preferences"],
                             trip_state_module.empty_trip_state()["preferences"],
                             f"{scenario_id}-{label} preferences untouched")
        for ev in (ev_a, ev_b):
            self._assert_no_route_surface(scenario_id, ev)
            self._assert_no_text_leak(scenario_id, ev)
            self._assert_policy(scenario_id, mode, ev)

    async def test_e1_case7_sessions_stay_distinct_auto(self):
        await self._transcript("auto")

    async def test_e1_case7_sessions_stay_distinct_quick(self):
        await self._transcript("quick")


__all__ = ()
