"""Batch A nonfatal prepare seams and relaxation follow-up: A-NG-06..09.

Drives the real agent loop with the real registered ``prepare_route_options``
/ ``present_route`` executors; only narrow provider/data seams are scripted
(``tests/conversation/conversation_matrix_harness.py``) and Anthropic inference is scripted
deterministic mock text. Shared invariants live in
``tests.conversation.conversation_no_good_support``.

A-NG-07..09 extend class 1 of Remediation A1 to the nonfatal prepare seam
(``prepare_route_persistence.nonfatal_prepare_result``) for the provider
no-modes / no-route path, in both Auto and Quick. A-NG-06 still fails on the
separate Q-relaxation defect (class 2); relaxation is intentionally not
implemented here.
"""

from __future__ import annotations

import secrets

from app.services.agent import trip_state as trip_state_module

from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    load_agent_loop,
    new_session,
    no_route_found_result,
    no_transit_modes_result,
    policy_model,
    q_only_leg,
    route_cards,
    run_turn,
    seed_accepted_active_trip,
)
from tests.conversation.conversation_no_good_support import (
    FORBIDDEN_TOOL_NAMES,
    _NoGoodOptionsBase,
)


class NonfatalPrepareSeamTests(_NoGoodOptionsBase):
    """A-NG-07..09: nonfatal prepare seams keep the accepted selection bound.

    The real ``prepare_route_options`` executor returns early through
    ``prepare_route_persistence.nonfatal_prepare_result`` when the
    ``prepare_single_leg`` seam reports no transit modes / no route. The
    audit set and status must still be stored and returned, but the accepted
    canonical selection (route facts, active set, selected candidate) stays
    bound -- the same P1 invariant as the aggregate no-good path.
    """

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def _run_nonfatal_scenario(
        self,
        *,
        mode,
        message,
        prepare_leg,
        expected_status,
        expected_prepare_input=None,
    ):
        return await self._run_scenario(
            mode=mode,
            message=message,
            prepare_leg=prepare_leg,
            expected_status=expected_status,
            expected_prepare_input=expected_prepare_input,
            tool_input_extra={"exclude_modes": ["SUBWAY", "BUS"]},
        )

    def _assert_no_modes_attempt(self, session, audit):
        # The attempted hard exclusion stays in the audit record and the
        # conversational slots (normalized to the sorted server form), but
        # never moves the accepted selection.
        self.assertEqual(audit["tool_input"]["exclude_modes"], ["BUS", "SUBWAY"])
        self.assertEqual(audit["candidates"], [])
        self.assertEqual(
            session["slots"]["constraints"]["exclude_modes"],
            ["BUS", "SUBWAY"],
        )

    async def test_ng07_nonfatal_no_transit_modes_auto(self):
        _session, _session_id, _seed, _events, _trace, audit, _state = (
            await self._run_nonfatal_scenario(
                mode="auto",
                message="Avoid buses and subways",
                prepare_leg=no_transit_modes_result(),
                expected_status="no_hard_constraint_match",
                expected_prepare_input={
                    "exclude_modes": ["BUS", "SUBWAY"],
                    "max_candidates": self.loop.agent_policy.policy_for_mode(
                        "auto"
                    ).max_route_candidates,
                },
            )
        )
        self._assert_no_modes_attempt(_session, audit)

    async def test_ng09_nonfatal_no_route_coverage_auto(self):
        _session, _session_id, _seed, _events, _trace, audit, _state = (
            await self._run_nonfatal_scenario(
                mode="auto",
                message="Avoid buses and subways",
                prepare_leg=no_route_found_result(),
                expected_status="insufficient_coverage",
            )
        )
        self.assertEqual(audit["candidates"], [])
        self.assertEqual(
            audit["evidence_coverage"],
            {"routes": "unavailable"},
        )


class RelaxationFollowupTests(_NoGoodOptionsBase):
    """A-NG-06: multi-turn relaxation (preserved failing regression)."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    FIXED_CANDIDATE_ID = "cd_ng06_fixed"

    async def _run_ng06(self, *, mode: str):
        session_id = f"sess-ng06-{secrets.token_hex(4)}"
        _sid, session = new_session()
        seed = seed_accepted_active_trip(session, session_id)
        # Turn 1 mirrors A-NG-01: Q-only provider under an active Q exclusion.
        events1, trace1, _mocks1 = await self._run_no_good_turn(
            session=session,
            session_id=session_id,
            message="Avoid the Q",
            rounds=self._prepare_rounds(seed.destination, tool_id="tu_1"),
            mode=mode,
            prepare_leg=q_only_leg(destination=seed.destination),
        )
        self.assertEqual(
            [name for name, _input in trace1.tool_calls],
            ["declare_goals", "prepare_route_options", "complete_turn"],
        )
        # Turn 2: rider explicitly allows the Q; the spec expects the active
        # exclusion to be cleared, a fresh canonical prepare, then present.
        rounds2 = [
            _turn_round(
                "declare_goals",
                "tu_goals",
                {
                    "goals": [
                        {
                            "goal_key": "route",
                            "kind": "route",
                            "depends_on": [],
                        }
                    ]
                },
            ),
            _turn_round(
                "prepare_route_options",
                "tu_2a",
                {
                    "goal_key": "route",
                    "destination": seed.destination,
                    "allowed_route_ids": ["Q"],
                    "excluded_route_ids": [],
                },
            ),
            _turn_round(
                "present_route",
                "tu_2b",
                {"goal_key": "route", "candidate_id": self.FIXED_CANDIDATE_ID},
            ),
        ]
        trace2 = self.loop.TurnTrace()
        events2, trace2 = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message="Fine, allow the Q.",
            rounds=rounds2,
            mode=mode,
            prepare_leg=q_only_leg(destination=seed.destination),
            fixed_candidate_id=self.FIXED_CANDIDATE_ID,
            trace=trace2,
            turn_id="t2",
        )
        return session, session_id, seed, events1, trace1, events2, trace2

    def _assert_ng06_spec(
        self,
        *,
        session,
        session_id,
        seed,
        events2,
        trace2,
        mode,
    ):
        """The scenario's second-turn route-relaxation contract."""

        prepare_inputs = [
            tool_input
            for name, tool_input in trace2.tool_calls
            if name == "prepare_route_options"
        ]
        self.assertEqual(len(prepare_inputs), 1)
        self.assertNotIn(
            "Q",
            prepare_inputs[0].get("excluded_route_ids") or [],
            "relaxed Q exclusion must be cleared on allow",
        )
        self.assertEqual(
            [name for name, _input in trace2.tool_calls],
            ["declare_goals", "prepare_route_options", "present_route"],
        )
        cards = route_cards(events2)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].role, "recommended")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(state["selected_candidate_id"], self.FIXED_CANDIDATE_ID)
        self.assertNotEqual(state["active_candidate_set_id"], seed.candidate_set_id)
        self.assertNotIn(
            "Q",
            (session.get("slots") or {}).get("constraints", {}).get(
                "excluded_route_ids"
            )
            or [],
        )
        names = [name for name, _input in trace2.tool_calls]
        for forbidden in FORBIDDEN_TOOL_NAMES:
            if forbidden == "present_route":
                continue
            self.assertNotIn(forbidden, names)
        self.assertEqual(events2[0].type, "meta")
        self.assertEqual(events2[-1].type, "done")
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual(trace2.model_call_count, 3)
        self.assertEqual(trace2.initial_mode, expected_mode)
        self.assertEqual(trace2.final_mode, expected_mode)
        self.assertEqual(self.loop.client.messages.calls[0]["model"], expected_model)

    async def test_ng06_auto_relaxation_presents_one_new_card(self):
        session, session_id, seed, _e1, _t1, events2, trace2 = await self._run_ng06(
            mode="auto"
        )
        self._assert_ng06_spec(
            session=session,
            session_id=session_id,
            seed=seed,
            events2=events2,
            trace2=trace2,
            mode="auto",
        )
