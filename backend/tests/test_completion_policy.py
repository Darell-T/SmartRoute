from __future__ import annotations

import unittest

from app.services.agent.turn.completion import (
    TurnResolution,
    evaluate_completion,
)
from app.services.agent.turn.contract import GoalKind, GoalState, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


def _contract(*goals: tuple[str, GoalKind, tuple[str, ...]]) -> TurnContract:
    return TurnContract(tuple(OutcomeGoal(key, kind, deps) for key, kind, deps in goals))


class CompletionPolicyTests(unittest.TestCase):
    def test_satisfiable_goal_blocks_termination(self) -> None:
        contract = _contract(("route", GoalKind.ROUTE, ()))
        decision = evaluate_completion(contract, TurnEvidence())

        self.assertFalse(decision.may_terminate)
        self.assertEqual(decision.remaining_goal_keys, ("route",))
        self.assertEqual(decision.required_next_actions, ("execute:route",))

    def test_evidence_ready_must_be_presented(self) -> None:
        contract = _contract(("status", GoalKind.SERVICE_STATUS, ()))
        evidence = TurnEvidence()
        evidence.record_goal("status", GoalState.EVIDENCE_READY, attempted=True)
        pending = evaluate_completion(contract, evidence)
        self.assertFalse(pending.may_terminate)
        self.assertEqual(pending.required_next_actions, ("present:status",))

        evidence.record_goal(
            "status", GoalState.SATISFIED, attempted=True, presented=True
        )
        complete = evaluate_completion(contract, evidence)
        self.assertTrue(complete.may_terminate)
        self.assertEqual(complete.turn_resolution, TurnResolution.COMPLETED)

    def test_unavailable_claim_requires_a_real_attempt(self) -> None:
        contract = _contract(("arrivals", GoalKind.ARRIVALS, ()))
        evidence = {"arrivals": {"state": GoalState.ATTEMPTED_BUT_UNAVAILABLE}}
        rejected = evaluate_completion(contract, evidence)
        self.assertFalse(rejected.may_terminate)
        self.assertEqual(rejected.required_next_actions, ("attempt:arrivals",))

        evidence["arrivals"]["attempted"] = True
        allowed = evaluate_completion(contract, evidence)
        self.assertTrue(allowed.may_terminate)
        self.assertEqual(
            allowed.turn_resolution, TurnResolution.ATTEMPTED_BUT_UNAVAILABLE
        )

    def test_mixed_result_is_partial_only_after_success_is_satisfied(self) -> None:
        contract = _contract(
            ("route", GoalKind.ROUTE, ()),
            ("crowd", GoalKind.EVENT_OR_CROWD, ()),
        )
        evidence = TurnEvidence()
        evidence.record_goal("route", GoalState.SATISFIED, attempted=True, presented=True)
        evidence.record_goal(
            "crowd",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
            approved_recovery_options=("try a wider time window",),
        )
        decision = evaluate_completion(contract, evidence)
        self.assertTrue(decision.may_terminate)
        self.assertEqual(
            decision.turn_resolution, TurnResolution.PARTIAL_SUCCESS_WITH_RECOVERY
        )
        self.assertEqual(decision.recovery_options, ("try a wider time window",))

    def test_rider_cancelled_goal_is_a_terminal_cancellation(self) -> None:
        contract = _contract(("scenario", GoalKind.ROUTE, ()))
        evidence = TurnEvidence()
        evidence.record_goal("scenario", GoalState.CANCELLED_BY_RIDER)

        decision = evaluate_completion(contract, evidence)

        self.assertTrue(decision.may_terminate)
        self.assertEqual(decision.turn_resolution, TurnResolution.CANCELLED)


if __name__ == "__main__":
    unittest.main()
