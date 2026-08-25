from __future__ import annotations

import unittest

from app.services.agent import session as session_module
from app.services.agent.turn import completion as turn_completion
from app.services.agent.tools._types import ToolResult
from app.services.agent.turn.contract import GoalState, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


class TurnResolutionTests(unittest.TestCase):
    def test_completed_presented_goal_terminates_without_continuation(self) -> None:
        session = session_module.new_session()[1]
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("places", "place_recommendation"),))
        )
        evidence.record_goal(
            "places", GoalState.SATISFIED, attempted=True, presented=True
        )

        decision = turn_completion.apply_completion(
            session=session,
            evidence=evidence,
            tool_outcomes=[("present_places", {}, ToolResult(ok=True))],
            selected_by_model=False,
        )

        self.assertTrue(decision and decision.may_terminate)
        self.assertTrue(evidence.terminal)
        self.assertEqual(evidence.terminal_path, "present_places")
        self.assertEqual(session_module.get_pending_continuations(session), ())

    def test_blocked_goal_creates_only_metadata_continuation(self) -> None:
        session = session_module.new_session()[1]
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", "route"),)))
        evidence.record_goal(
            "route",
            GoalState.BLOCKED_WAITING_FOR_RIDER,
            approved_recovery_options=("ask for destination",),
        )

        decision = turn_completion.apply_completion(
            session=session,
            evidence=evidence,
            tool_outcomes=[("complete_turn", {}, ToolResult(ok=True))],
            selected_by_model=False,
        )

        self.assertTrue(decision and decision.may_terminate)
        continuations = session_module.get_pending_continuations(session)
        self.assertEqual(len(continuations), 1)
        self.assertEqual(continuations[0].unresolved_outcomes, ("route",))
        self.assertEqual(continuations[0].attempt_count, 1)
        self.assertEqual(
            continuations[0].approved_recovery_options,
            ("ask for destination",),
        )
        self.assertNotIn("provider", continuations[0].to_dict())

    def test_repeated_block_drops_continuation_after_attempt_limit(self) -> None:
        session = session_module.new_session()[1]
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", "route"),)))
        evidence.record_goal("route", GoalState.BLOCKED_WAITING_FOR_RIDER)

        for _ in range(session_module.MAX_CONTINUATION_ATTEMPTS + 1):
            evidence.terminal = False
            turn_completion.apply_completion(
                session=session,
                evidence=evidence,
                tool_outcomes=[("complete_turn", {}, ToolResult(ok=True))],
                selected_by_model=False,
            )

        self.assertEqual(session_module.get_pending_continuations(session), ())

    def test_unavailable_evidence_waits_for_terminal_recovery_tool(self) -> None:
        session = session_module.new_session()[1]
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", "route"),)))
        evidence.record_goal(
            "route",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
            approved_recovery_options=("Try different constraints.",),
        )

        decision = turn_completion.apply_completion(
            session=session,
            evidence=evidence,
            tool_outcomes=[("prepare_route_options", {}, ToolResult(ok=True))],
            selected_by_model=False,
        )

        self.assertTrue(decision and decision.may_terminate)
        self.assertFalse(evidence.terminal)
        self.assertEqual(session_module.get_pending_continuations(session), ())

        turn_completion.apply_completion(
            session=session,
            evidence=evidence,
            tool_outcomes=[("complete_turn", {}, ToolResult(ok=True, terminal=True))],
            selected_by_model=False,
        )

        self.assertTrue(evidence.terminal)
        self.assertEqual(evidence.terminal_path, "complete_turn")
        self.assertEqual(len(session_module.get_pending_continuations(session)), 1)

    def test_explicit_deterministic_fallback_provenance_is_preserved(self) -> None:
        session = session_module.new_session()[1]
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", "route"),)))
        evidence.record_goal(
            "route", GoalState.SATISFIED, attempted=True, presented=True
        )
        evidence.selection_source = "deterministic_fallback"

        decision = turn_completion.apply_completion(
            session=session,
            evidence=evidence,
            tool_outcomes=[("present_route", {}, ToolResult(ok=True))],
            selected_by_model=False,
        )

        self.assertTrue(decision and decision.may_terminate)
        self.assertEqual(evidence.selection_source, "deterministic_fallback")

    def test_selected_model_provenance_fills_an_empty_evidence_source(self) -> None:
        session = session_module.new_session()[1]
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", "route"),)))
        evidence.record_goal(
            "route", GoalState.SATISFIED, attempted=True, presented=True
        )

        decision = turn_completion.apply_completion(
            session=session,
            evidence=evidence,
            tool_outcomes=[("present_route", {}, ToolResult(ok=True))],
            selected_by_model=True,
        )

        self.assertTrue(decision and decision.may_terminate)
        self.assertEqual(evidence.selection_source, "model")


if __name__ == "__main__":
    unittest.main()
