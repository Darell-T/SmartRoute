"""Batch B: deterministic exhaustive what-if lifecycle scenarios (Auto+Quick).

Drives the *real* agent loop (``app.services.agent.loop.run_agent_turn``)
with production intent/tool filtering, the real ``TOOL_REGISTRY``
``prepare_route_options`` / ``present_route`` executors, the real candidate
store, and real trip/profile/session state. Only the narrow provider/data
seams of ``tests.conversation.conversation_matrix_harness`` are scripted; Anthropic
inference is deterministic mock text (no model/provider/web/DB/network calls).

Scenario families: B-TEMP-PREVIEW/ACCEPT/REJECT, B-BUS-PREVIEW/ACCEPT/REJECT,
B-REPLACEMENT, B-UNRELATED. Shared invariants live in
``tests.conversation.conversation_what_if_support``. Where current production cannot
satisfy a desired invariant (acceptance tool/identity contract gaps --
stateless intent classification is reported as evidence only -- plus the
reject discard gap and the post-reject executor-eligibility probe), the
failing assertion carries the actual evidence per the batch stop conditions
-- production is not modified here.
"""

from __future__ import annotations

from app.services.agent import trip_state as trip_state_module

from tests.conversation.conversation_matrix_harness import (
    load_agent_loop,
    route_cards,
    run_turn,
    text_round,
)
from tests.conversation.conversation_what_if_support import (
    FIXED_PREVIEW_1,
    FIXED_PREVIEW_2,
    TEMPORAL_DEPARTURE,
    UNRELATED_MESSAGE,
    _WhatIfLifecycleBase,
    capture_temporary_candidate,
)


class TemporalWhatIfTests(_WhatIfLifecycleBase):
    """B-TEMP-PREVIEW / B-TEMP-ACCEPT / B-TEMP-REJECT (Auto + Quick)."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_b_temp_preview_auto(self):
        await self._temporal_preview(mode="auto", scenario_id="B-TEMP-PREVIEW")

    async def test_b_temp_preview_quick(self):
        await self._temporal_preview(mode="quick", scenario_id="B-TEMP-PREVIEW")

    async def _temporal_accept(self, mode, scenario_id):
        session, session_id, seed = await self._temporal_preview(
            mode=mode, scenario_id=scenario_id
        )
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id
        )
        events, trace = await self._accept_turn(
            session=session,
            session_id=session_id,
            mode=mode,
            candidate_id=candidate_id,
        )
        self._assert_accept_gaps(
            scenario_id=scenario_id,
            session_id=session_id,
            mode=mode,
            candidate_id=candidate_id,
        )
        self._assert_accept_commit(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            session=session,
            session_id=session_id,
            seed=seed,
            mode=mode,
            candidate_id=candidate_id,
            set_id=set_id,
            expected_state={
                "planning_mode": "depart_at",
                "requested_departure": TEMPORAL_DEPARTURE,
                "requested_arrival": None,
                "origin": "Home",
                "destination": "Work",
            },
        )

    async def test_b_temp_accept_auto(self):
        await self._temporal_accept("auto", "B-TEMP-ACCEPT")

    async def test_b_temp_accept_quick(self):
        await self._temporal_accept("quick", "B-TEMP-ACCEPT")

    async def _temporal_reject(self, mode, scenario_id):
        session, session_id, seed = await self._temporal_preview(
            mode=mode, scenario_id=scenario_id
        )
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id
        )
        state_before = trip_state_module.get_trip_state(session)
        events, trace = await self._reject_turn(
            session=session,
            session_id=session_id,
            mode=mode,
        )
        self._assert_reject(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            session=session,
            session_id=session_id,
            seed=seed,
            state_before=state_before,
            preview_set_id=set_id,
            preview_candidate_id=candidate_id,
        )

    async def _temporal_reject_eligibility_probe(self, mode, scenario_id):
        session, session_id, seed = await self._temporal_preview(
            mode=mode, scenario_id=scenario_id
        )
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id
        )
        await self._reject_turn(session=session, session_id=session_id, mode=mode)
        await self._executor_eligibility_probe(
            scenario_id=scenario_id,
            session=session,
            session_id=session_id,
            mode=mode,
            seed=seed,
            candidate_id=candidate_id,
        )

    async def test_b_temp_reject_auto(self):
        await self._temporal_reject("auto", "B-TEMP-REJECT")

    async def test_b_temp_reject_quick(self):
        await self._temporal_reject("quick", "B-TEMP-REJECT")

    async def test_b_temp_reject_probe_auto(self):
        await self._temporal_reject_eligibility_probe("auto", "B-TEMP-REJECT")

    async def test_b_temp_reject_probe_quick(self):
        await self._temporal_reject_eligibility_probe("quick", "B-TEMP-REJECT")


class BusWhatIfTests(_WhatIfLifecycleBase):
    """B-BUS-PREVIEW / B-BUS-ACCEPT / B-BUS-REJECT (Auto + Quick)."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_b_bus_preview_auto(self):
        await self._bus_preview(mode="auto", scenario_id="B-BUS-PREVIEW")

    async def test_b_bus_preview_quick(self):
        await self._bus_preview(mode="quick", scenario_id="B-BUS-PREVIEW")

    async def _bus_accept(self, mode, scenario_id):
        session, session_id, seed = await self._bus_preview(
            mode=mode, scenario_id=scenario_id
        )
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id
        )
        prefs_before = trip_state_module.get_trip_state(session)["preferences"]
        events, trace = await self._accept_turn(
            session=session,
            session_id=session_id,
            mode=mode,
            candidate_id=candidate_id,
        )
        self._assert_accept_gaps(
            scenario_id=scenario_id,
            session_id=session_id,
            mode=mode,
            candidate_id=candidate_id,
        )
        self._assert_accept_commit(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            session=session,
            session_id=session_id,
            seed=seed,
            mode=mode,
            candidate_id=candidate_id,
            set_id=set_id,
            expected_state={
                "planning_mode": "leave_now",
                "requested_departure": None,
                "requested_arrival": None,
                "origin": "Home",
                "destination": "Work",
                "preferences": {
                    **prefs_before,
                    "preferred_modes": ["BUS"],
                },
            },
        )
        self.assertEqual(
            session["profile"]["preferences"]["preferred_modes"],
            ["BUS"],
            f"{scenario_id} BUS preference applied to profile exactly once",
        )

    async def test_b_bus_accept_auto(self):
        await self._bus_accept("auto", "B-BUS-ACCEPT")

    async def test_b_bus_accept_quick(self):
        await self._bus_accept("quick", "B-BUS-ACCEPT")

    async def _bus_reject(self, mode, scenario_id):
        session, session_id, seed = await self._bus_preview(
            mode=mode, scenario_id=scenario_id
        )
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id
        )
        state_before = trip_state_module.get_trip_state(session)
        events, trace = await self._reject_turn(
            session=session,
            session_id=session_id,
            mode=mode,
        )
        self._assert_reject(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            session=session,
            session_id=session_id,
            seed=seed,
            state_before=state_before,
            preview_set_id=set_id,
            preview_candidate_id=candidate_id,
        )
        self.assertEqual(
            session["profile"]["preferences"]["preferred_modes"],
            [],
            f"{scenario_id} reject preserves original profile preferences",
        )

    async def _bus_reject_eligibility_probe(self, mode, scenario_id):
        session, session_id, seed = await self._bus_preview(
            mode=mode, scenario_id=scenario_id
        )
        set_id, candidate_id, _record = capture_temporary_candidate(
            session, session_id
        )
        await self._reject_turn(session=session, session_id=session_id, mode=mode)
        await self._executor_eligibility_probe(
            scenario_id=scenario_id,
            session=session,
            session_id=session_id,
            mode=mode,
            seed=seed,
            candidate_id=candidate_id,
        )

    async def test_b_bus_reject_auto(self):
        await self._bus_reject("auto", "B-BUS-REJECT")

    async def test_b_bus_reject_quick(self):
        await self._bus_reject("quick", "B-BUS-REJECT")

    async def test_b_bus_reject_probe_auto(self):
        await self._bus_reject_eligibility_probe("auto", "B-BUS-REJECT")

    async def test_b_bus_reject_probe_quick(self):
        await self._bus_reject_eligibility_probe("quick", "B-BUS-REJECT")


class ReplacementAndUnrelatedTests(_WhatIfLifecycleBase):
    """B-REPLACEMENT and B-UNRELATED (Auto + Quick)."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _replacement(self, mode, scenario_id):
        session, session_id, seed, state_before = self._seed(mode)
        session, session_id, seed = await self._temporal_preview(
            mode=mode,
            scenario_id=f"{scenario_id}-t1",
            candidate_id=FIXED_PREVIEW_1,
            session=session,
            session_id=session_id,
            seed=seed,
            state_before=state_before,
        )
        set1, candidate1, record1 = capture_temporary_candidate(session, session_id)
        session, session_id, seed = await self._bus_preview(
            mode=mode,
            scenario_id=f"{scenario_id}-t2",
            candidate_id=FIXED_PREVIEW_2,
            session=session,
            session_id=session_id,
            seed=seed,
            state_before=state_before,
        )
        set2, candidate2, _record2 = capture_temporary_candidate(session, session_id)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(
            state["temporary_candidate_set_id"],
            set2,
            f"{scenario_id} second preview replaces the temporary identity",
        )
        self.assertEqual(
            state["temporary_selected_candidate_id"],
            candidate2,
            scenario_id,
        )
        self.assertEqual(
            state["active_candidate_set_id"],
            seed.candidate_set_id,
            f"{scenario_id} active base stays the original accepted route",
        )
        self.assertFalse(record1["presented"], f"{scenario_id} stale preview unconsumed")

        probe_events, probe_trace = await self._stale_probe(
            session=session,
            session_id=session_id,
            mode=mode,
            candidate_id=candidate1,
        )
        present_attempts = [
            attempt
            for attempt in probe_trace.capability_attempts
            if attempt["capability"] == "present_route"
        ]
        self.assertTrue(
            present_attempts and present_attempts[0]["ok"] is False,
            f"{scenario_id} stale identity present must fail",
        )
        self.assertEqual(route_cards(probe_events), [], scenario_id)
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(
            state["temporary_candidate_set_id"],
            set2,
            f"{scenario_id} stale probe leaves the latest preview bound",
        )
        self.assertEqual(
            state["active_candidate_set_id"],
            seed.candidate_set_id,
            scenario_id,
        )

        prefs_before = trip_state_module.get_trip_state(session)["preferences"]
        events3, trace3 = await self._accept_turn(
            session=session,
            session_id=session_id,
            mode=mode,
            candidate_id=candidate2,
            turn_id="t4",
        )
        self._assert_accept_gaps(
            scenario_id=scenario_id,
            session_id=session_id,
            mode=mode,
            candidate_id=candidate2,
        )
        self._assert_accept_commit(
            scenario_id=scenario_id,
            events=events3,
            trace=trace3,
            session=session,
            session_id=session_id,
            seed=seed,
            mode=mode,
            candidate_id=candidate2,
            set_id=set2,
            expected_state={
                "planning_mode": "leave_now",
                "requested_departure": None,
                "requested_arrival": None,
                "origin": "Home",
                "destination": "Work",
                "preferences": {
                    **prefs_before,
                    "preferred_modes": ["BUS"],
                },
            },
        )
        self.assertFalse(
            record1["presented"],
            f"{scenario_id} stale preview never consumed after latest commit",
        )

    async def test_b_replacement_auto(self):
        await self._replacement("auto", "B-REPLACEMENT")

    async def test_b_replacement_quick(self):
        await self._replacement("quick", "B-REPLACEMENT")

    async def _unrelated(self, mode, scenario_id):
        session, session_id, seed = await self._temporal_preview(
            mode=mode, scenario_id=scenario_id
        )
        set1, candidate1, _record = capture_temporary_candidate(session, session_id)
        state_before = trip_state_module.get_trip_state(session)
        rounds = [text_round("The standard subway fare is $2.90.")]
        trace = self.loop.TurnTrace()
        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=UNRELATED_MESSAGE,
            rounds=rounds,
            mode=mode,
            trace=trace,
            mocks={},
            turn_id="t2",
        )
        self.assertEqual(trace.tool_calls, [], f"{scenario_id} unrelated runs no tools")
        self.assertEqual(events[0].type, "meta", scenario_id)
        self.assertEqual(events[-1].type, "done", scenario_id)
        self.assertEqual(route_cards(events), [], scenario_id)
        state = trip_state_module.get_trip_state(session)
        for key in (
            "origin",
            "destination",
            "waypoints",
            "planning_mode",
            "requested_departure",
            "requested_arrival",
            "active_candidate_set_id",
            "selected_candidate_id",
            "preferences",
        ):
            self.assertEqual(
                state[key], state_before[key], f"{scenario_id} active [{key}]"
            )
        # Unrelated conversation never implicitly accepts or discards.
        self.assertEqual(state["temporary_candidate_set_id"], set1, scenario_id)
        self.assertEqual(
            state["temporary_selected_candidate_id"], candidate1, scenario_id
        )
        self.assertEqual(session["active_trip"]["card_id"], seed.card_id, scenario_id)
        self.assertEqual(
            [card["card_id"] for card in session["route_cards"]],
            [seed.card_id],
            scenario_id,
        )

    async def test_b_unrelated_auto(self):
        await self._unrelated("auto", "B-UNRELATED")

    async def test_b_unrelated_quick(self):
        await self._unrelated("quick", "B-UNRELATED")
