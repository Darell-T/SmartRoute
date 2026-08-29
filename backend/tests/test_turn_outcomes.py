from __future__ import annotations

import unittest

from app.services.agent import public_surface
from app.services.agent.passenger_output import truthful_failure_text
from app.services.agent.tools._types import ToolOutcome, ToolResult
from app.services.agent.turn import completion as turn_completion
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence


class TruthfulFailureTextTests(unittest.TestCase):
    def test_failed_dependent_route_preserves_place_for_canonical_presentation(
        self,
    ) -> None:
        evidence = TurnEvidence()
        contract = TurnContract(
            (
                OutcomeGoal("place", GoalKind.DESTINATION_SELECTION),
                OutcomeGoal("route", GoalKind.ROUTE, depends_on=("place",)),
            )
        )
        evidence.bind_contract(contract)
        evidence.record_goal_handle("place", "discovery-set")
        evidence.record_goal("place", GoalState.EVIDENCE_READY, attempted=True)

        evidence.record_capability_result(
            "prepare_route_options",
            {
                "goal_key": "route",
                "destination_place_id": "place-id",
            },
            ToolResult(
                ok=True,
                data={
                    "candidates": [],
                    "presentation_allowed": False,
                },
            ),
        )

        assert evidence.state_for("place") == GoalState.EVIDENCE_READY
        assert evidence.state_for("route") == GoalState.ATTEMPTED_BUT_UNAVAILABLE
        decision = turn_completion.evaluate_completion(contract, evidence)
        assert not decision.may_terminate
        assert decision.required_next_actions == ("present:place",)
        offered = public_surface.state_valid_tool_names(evidence)
        assert public_surface.required_presenter_tool(evidence, offered) == "present_places"

    def test_unresolved_transit_result_does_not_make_goal_evidence_ready(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("arrival", GoalKind.ARRIVALS),))
        )
        result = ToolResult(
            ok=True,
            outcome=ToolOutcome.NEEDS_CLARIFICATION,
            data={
                "operation": "arrivals",
                "result": {"source_status": "stop_not_resolved"},
            },
        )

        evidence.record_capability_result(
            "check_transit",
            {"goal_key": "arrival", "operation": "arrivals"},
            result,
        )

        assert evidence.state_for("arrival") == GoalState.ATTEMPTED_BUT_UNAVAILABLE
        assert evidence.handle_for("arrival") is None

    def test_compound_route_and_crowd_failure_names_first_unresolved_evidence(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("msg_route", GoalKind.ROUTE),
                    OutcomeGoal("avoid_crowds", GoalKind.EVENT_OR_CROWD),
                )
            )
        )
        evidence.record_goal(
            "msg_route", GoalState.SATISFIED, attempted=True, presented=True
        )
        evidence.record_goal(
            "avoid_crowds", GoalState.ATTEMPTED_BUT_UNAVAILABLE, attempted=True
        )

        text = truthful_failure_text(evidence)

        assert "couldn't verify the crowd or area conditions" in text
        assert "could not find a verified route" not in text
        assert "msg_route" not in text
        assert "avoid_crowds" not in text

    def test_unresolved_route_keeps_precise_attempted_route_fallback(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("msg_route", GoalKind.ROUTE),
                    OutcomeGoal("avoid_crowds", GoalKind.EVENT_OR_CROWD),
                )
            )
        )
        evidence.record_goal(
            "msg_route", GoalState.ATTEMPTED_BUT_UNAVAILABLE, attempted=True
        )
        evidence.record_goal("avoid_crowds", GoalState.SATISFIED, presented=True)

        text = truthful_failure_text(evidence)

        assert text == "I could not find a verified route for that trip."

    def test_first_unresolved_crowd_goal_wins_over_later_route_failure(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("avoid_crowds", GoalKind.EVENT_OR_CROWD),
                    OutcomeGoal("msg_route", GoalKind.ROUTE),
                )
            )
        )
        evidence.record_goal(
            "avoid_crowds", GoalState.ATTEMPTED_BUT_UNAVAILABLE, attempted=True
        )
        evidence.record_goal(
            "msg_route", GoalState.ATTEMPTED_BUT_UNAVAILABLE, attempted=True
        )

        text = truthful_failure_text(evidence)

        assert "couldn't verify the crowd or area conditions" in text
        assert "could not find a verified route" not in text

    def test_blocked_goal_requests_clarification(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("route_goal", GoalKind.ROUTE),))
        )
        evidence.record_goal("route_goal", GoalState.BLOCKED_WAITING_FOR_RIDER)

        text = truthful_failure_text(evidence)

        assert "still need one detail" in text
        assert "route_goal" not in text

    def test_general_response_keeps_generic_unresolved_fallback(self) -> None:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("response", GoalKind.GENERAL_RESPONSE),))
        )

        text = truthful_failure_text(evidence)

        assert text == "I couldn't complete that request in this turn, so I don't have a " "verified result to share."


if __name__ == "__main__":
    unittest.main()
