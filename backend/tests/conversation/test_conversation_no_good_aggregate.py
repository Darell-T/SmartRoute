"""Batch A aggregate no-good scenarios: A-NG-01..05 in Auto and Quick.

Drives the real agent loop with the real registered ``prepare_route_options``
executor; only narrow provider/data seams are scripted
(``tests/conversation/conversation_matrix_harness.py``) and Anthropic inference is scripted
deterministic mock text. Legacy ``plan_trip`` is never used. Shared
invariants live in ``tests.conversation.conversation_no_good_support``.

Hard-constraint misses preserve the accepted canonical selection as a separate
audit result. Viable candidates remain presentable when optional evidence is
incomplete or operational evidence is adverse.
"""

from __future__ import annotations

from tests.conversation.conversation_matrix_harness import (
    all_materially_degraded_leg,
    insufficient_coverage_leg,
    load_agent_loop,
    policy_model,
    q_only_leg,
)
from tests.conversation.conversation_no_good_support import (
    _NoGoodOptionsBase,
)


class NoGoodOptionsAutoTests(_NoGoodOptionsBase):
    """A-NG-01 / A-NG-03 / A-NG-04 / A-NG-05 in Auto."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_ng01_excluded_q_only_provider_is_no_hard_constraint_match(self):
        (
            _session,
            _session_id,
            _seed,
            _events,
            _trace,
            audit,
            _state,
        ) = await self._run_scenario(
            mode="auto",
            message="Avoid the Q",
            prepare_leg=q_only_leg(),
            expected_status="no_hard_constraint_match",
            expected_prepare_input={
                "excluded_route_ids": ["Q"],
                "max_candidates": self.loop.agent_policy.policy_for_mode(
                    "auto"
                ).max_route_candidates,
            },
            tool_input_extra={"excluded_route_ids": ["Q"]},
        )
        digest = audit["candidates"][0]["digest"]
        self.assertIn("excluded_route", digest["hard_constraint_violations"])
        self.assertEqual(
            _session["slots"]["constraints"]["excluded_route_ids"],
            ["Q"],
        )

    async def test_ng03_insufficient_coverage_auto(self):
        (
            _session,
            _session_id,
            _events,
            _trace,
            record,
        ) = await self._run_presentable_scenario(
            mode="auto",
            message="Change the route",
            prepare_leg=insufficient_coverage_leg(),
            expected_status="insufficient_coverage",
        )
        self.assertEqual(
            record["evidence_coverage"],
            {
                "mta": "unscanned",
                "vehicles": "unscanned",
                "incidents": "unscanned",
                "events": "not_required",
            },
        )

    async def test_ng04_all_materially_degraded_auto(self):
        (
            _session,
            _session_id,
            _events,
            _trace,
            record,
        ) = await self._run_presentable_scenario(
            mode="auto",
            message="Change the route",
            prepare_leg=all_materially_degraded_leg(),
            expected_status="all_materially_degraded",
        )
        impacts = record["candidates"][0]["digest"]["official_service_impacts"]
        self.assertEqual(len(impacts), 1)
        impact = impacts[0]
        self.assertEqual(impact["header"], "R service change")
        self.assertEqual(impact["route_ids"], ["R"])
        self.assertEqual(impact["source"], "unknown")
        self.assertTrue(impact["material_disruption"])

    async def test_ng05_accessibility_invalidates_every_candidate_auto(self):
        (
            _session,
            _session_id,
            _seed,
            _events,
            _trace,
            audit,
            _state,
        ) = await self._run_scenario(
            mode="auto",
            message="Avoid stairs",
            prepare_leg=q_only_leg(),
            expected_status="no_hard_constraint_match",
            tool_input_extra={
                "avoid_stairs": True,
                "accessibility_required": True,
            },
        )
        digest = audit["candidates"][0]["digest"]
        self.assertIn(
            "accessibility_unknown_or_unavailable",
            digest["hard_constraint_violations"],
        )
        self.assertTrue(audit["tool_input"]["accessibility_required"])
        self.assertTrue(audit["tool_input"]["avoid_stairs"])


class NoGoodOptionsQuickTests(_NoGoodOptionsBase):
    """A-NG-02 plus the Quick variants of A-NG-03/04/05."""

    @classmethod
    def setUpClass(cls):
        cls.loop = load_agent_loop()

    async def test_ng02_excluded_q_only_provider_quick(self):
        (
            _session,
            _session_id,
            _seed,
            _events,
            _trace,
            _audit,
            _state,
        ) = await self._run_scenario(
            mode="quick",
            message="Avoid the Q",
            prepare_leg=q_only_leg(),
            expected_status="no_hard_constraint_match",
            expected_prepare_input={
                "excluded_route_ids": ["Q"],
                "max_candidates": self.loop.agent_policy.policy_for_mode(
                    "quick"
                ).max_route_candidates,
                "include_first_leg_arrivals": False,
            },
            tool_input_extra={"excluded_route_ids": ["Q"]},
        )
        _mode, quick_model = policy_model(self.loop, "quick")
        self.assertEqual(self.loop.client.messages.calls[0]["model"], quick_model)
        self.assertIn(
            "response_presentation: quick",
            self.loop.client.messages.calls[0]["messages"][-1]["content"],
        )

    async def test_ng03_insufficient_coverage_quick(self):
        (
            _session,
            _session_id,
            _events,
            _trace,
            _record,
        ) = await self._run_presentable_scenario(
            mode="quick",
            message="Change the route",
            prepare_leg=insufficient_coverage_leg(),
            expected_status="insufficient_coverage",
        )

    async def test_ng04_all_materially_degraded_quick(self):
        (
            _session,
            _session_id,
            _events,
            _trace,
            _record,
        ) = await self._run_presentable_scenario(
            mode="quick",
            message="Change the route",
            prepare_leg=all_materially_degraded_leg(),
            expected_status="all_materially_degraded",
        )

    async def test_ng05_accessibility_invalidates_every_candidate_quick(self):
        (
            _session,
            _session_id,
            _seed,
            _events,
            _trace,
            audit,
            _state,
        ) = await self._run_scenario(
            mode="quick",
            message="Avoid stairs",
            prepare_leg=q_only_leg(),
            expected_status="no_hard_constraint_match",
            tool_input_extra={
                "avoid_stairs": True,
                "accessibility_required": True,
            },
        )
        self.assertIn(
            "accessibility_unknown_or_unavailable",
            audit["candidates"][0]["digest"]["hard_constraint_violations"],
        )
