"""Turn-local web and route-isolation evidence rules."""

from __future__ import annotations

import unittest

from app.services.agent import public_surface
from app.services.agent.turn.completion import evaluate_completion
from app.services.agent.tools._types import ToolResult
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence


def _contract(
    *goals: tuple[str, GoalKind, tuple[str, ...]],
) -> TurnContract:
    return TurnContract(
        tuple(OutcomeGoal(key, kind, dependencies) for key, kind, dependencies in goals)
    )


class TurnEvidenceWebTests(unittest.TestCase):
    def test_trace_records_monotonic_goal_transitions(self):
        contract = TurnContract(
            (OutcomeGoal("places", GoalKind.PLACE_RECOMMENDATION),)
        )
        evidence = TurnEvidence()
        evidence.bind_contract(contract)

        evidence.record_goal("places", GoalState.IN_FLIGHT, attempted=True)
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_test",
            place_count=2,
        )
        evidence.record_goal(
            "places",
            GoalState.SATISFIED,
            attempted=True,
            presented=True,
        )
        evidence.mark_terminal("present_places")

        self.assertEqual(
            [item["new_state"] for item in evidence.goal_transitions],
            ["in_flight", "satisfied"],
        )

    def test_successful_web_is_one_use_and_claimable(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=3)
        self.assertTrue(evidence.may_offer_web())
        evidence.note_web(ok=True)
        self.assertTrue(evidence.web_used)
        self.assertTrue(evidence.web_succeeded)
        self.assertTrue(evidence.can_claim_research_used())
        self.assertFalse(evidence.may_offer_web())

    def test_failed_web_still_consumes_the_one_use(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=2)
        evidence.note_web(ok=False)
        self.assertTrue(evidence.web_used)
        self.assertFalse(evidence.web_succeeded)
        self.assertFalse(evidence.may_offer_web())
        self.assertFalse(evidence.can_claim_research_used())

    def test_declared_route_never_offers_web_during_discovery(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=3)
        self.assertFalse(evidence.may_offer_web())

    def test_prepare_route_disables_web(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=3)
        evidence.note_prepare_route_options(ok=True, candidate_count=2)
        self.assertFalse(evidence.may_offer_web())
        self.assertTrue(evidence.web_disabled)

    def test_verify_operation_offers_web_for_current_details(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=1,
            operation="verify",
        )
        self.assertTrue(evidence.may_offer_web())
        self.assertEqual(evidence.discovery_set_id, "ds_1")
        self.assertTrue(evidence.structured_place_search_attempted)

    def test_search_operation_may_offer_web_until_prepare(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=2,
            operation="search",
        )
        self.assertTrue(evidence.may_offer_web())
        evidence.note_prepare_route_options(ok=True, candidate_count=1)
        self.assertFalse(evidence.may_offer_web())

    def test_route_requirement_blocks_web_before_preparation(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=2,
            operation="search",
        )
        self.assertFalse(evidence.may_offer_web())
        self.assertFalse(evidence.web_disabled)

    def test_route_goal_remains_unresolved_after_discovery(self):
        evidence = TurnEvidence()
        contract = _contract(("route", GoalKind.ROUTE, ()))
        evidence.bind_contract(contract)
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=2,
        )

        decision = evaluate_completion(contract, evidence)

        self.assertFalse(decision.may_terminate)
        self.assertEqual(decision.remaining_goal_keys, ("route",))
        self.assertFalse(evidence.terminal)

    def test_route_owned_discovery_keeps_route_pending_without_route_evidence(self):
        evidence = TurnEvidence()
        contract = _contract(("route", GoalKind.ROUTE, ()))
        evidence.bind_contract(contract)

        evidence.record_capability_result(
            "discover_places",
            {"goal_key": "route"},
            ToolResult(
                ok=True,
                data={
                    "discovery_set_id": "ds_route",
                    "places": [{"place_id": "pl_1"}],
                },
            ),
        )

        self.assertEqual(evidence.discovery_set_id, "ds_route")
        self.assertEqual(evidence.verified_place_count, 1)
        self.assertEqual(evidence.state_for("route"), GoalState.PENDING)
        self.assertIsNone(evidence.handle_for("route"))
        self.assertEqual(evidence.route_candidate_count, 0)
        self.assertNotIn(
            "present_route",
            public_surface.state_valid_tool_names(evidence),
        )

    def test_route_owned_discovery_then_route_preparation_owns_completion(self):
        evidence = TurnEvidence()
        contract = _contract(("route", GoalKind.ROUTE, ()))
        evidence.bind_contract(contract)

        evidence.record_capability_result(
            "discover_places",
            {"goal_key": "route"},
            ToolResult(
                ok=True,
                data={
                    "discovery_set_id": "ds_route",
                    "places": [{"place_id": "pl_1"}],
                },
            ),
        )
        evidence.record_capability_result(
            "prepare_route_options",
            {"goal_key": "route"},
            ToolResult(
                ok=True,
                data={
                    "candidate_set_id": "cs_route",
                    "candidates": [{"candidate_id": "cd_1"}],
                },
            ),
        )

        self.assertEqual(evidence.state_for("route"), GoalState.EVIDENCE_READY)
        self.assertEqual(evidence.handle_for("route"), "cs_route")
        self.assertIn(
            "present_route",
            public_surface.state_valid_tool_names(evidence),
        )

        evidence.record_capability_result(
            "present_route",
            {"goal_key": "route", "candidate_id": "cd_1"},
            ToolResult(ok=True, data={"selection_source": "model"}),
        )
        self.assertEqual(evidence.state_for("route"), GoalState.SATISFIED)
        self.assertTrue(evaluate_completion(contract, evidence).may_terminate)

    def test_route_presentation_requires_compound_discovery_evidence(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            _contract(
                ("destination", GoalKind.DESTINATION_SELECTION, ()),
                ("route", GoalKind.ROUTE, ("destination",)),
            )
        )
        evidence.note_prepare_route_options(ok=True, candidate_count=1)

        self.assertEqual(
            evidence.turn_contract.dependency_blockers("route", evidence),
            ("destination",),
        )
        self.assertFalse(evidence.terminal)

    def test_empty_route_preparation_is_not_evidence_ready(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_prepare_route_options(ok=True, candidate_count=0)
        self.assertEqual(evidence.route_candidate_count, 0)
        self.assertTrue(evidence.web_disabled)
        self.assertEqual(evidence.state_for("route"), GoalState.PENDING)

    def test_nonpresentable_route_candidates_are_not_evidence_ready(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_prepare_route_options(
            ok=True,
            candidate_count=2,
            presentation_allowed=False,
        )
        self.assertEqual(evidence.route_candidate_count, 0)
        self.assertTrue(evidence.web_disabled)
        self.assertEqual(evidence.state_for("route"), GoalState.PENDING)
        self.assertFalse(evidence.terminal)

    def test_nonpresentable_route_outcome_marks_goal_unavailable(self):
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", "route"),)))

        evidence.record_capability_result(
            "prepare_route_options",
            {"goal_key": "route"},
            ToolResult(
                ok=True,
                data={
                    "candidate_set_id": "cs_1",
                    "candidates": [{"candidate_id": "candidate_1"}],
                    "presentation_allowed": False,
                },
            ),
        )

        self.assertEqual(
            evidence.state_for("route"),
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
        )
        self.assertEqual(evidence.route_candidate_count, 0)
        self.assertFalse(evidence.terminal)

    def test_presented_route_satisfies_consumed_destination_selection(self):
        evidence = TurnEvidence()
        contract = TurnContract(
            (
                OutcomeGoal("destination", GoalKind.DESTINATION_SELECTION),
                OutcomeGoal("route", GoalKind.ROUTE, ("destination",)),
            )
        )
        evidence.bind_contract(contract)
        evidence.record_goal_handle("destination", "ds_branch_pool")
        evidence.record_goal(
            "destination", GoalState.EVIDENCE_READY, attempted=True
        )
        evidence.record_goal_handle("route", "cs_branch_routes")
        evidence.record_goal("route", GoalState.EVIDENCE_READY, attempted=True)

        evidence.record_capability_result(
            "present_route",
            {"goal_key": "route", "candidate_id": "cd_selected_branch"},
            ToolResult(
                ok=True,
                data={"selection_source": "model"},
            ),
        )

        self.assertEqual(evidence.state_for("destination"), GoalState.SATISFIED)
        self.assertEqual(evidence.state_for("route"), GoalState.SATISFIED)
        self.assertTrue(evaluate_completion(contract, evidence).may_terminate)

    def test_presented_route_does_not_satisfy_unrelated_place_goal(self):
        evidence = TurnEvidence()
        contract = TurnContract(
            (
                OutcomeGoal("places", GoalKind.PLACE_RECOMMENDATION),
                OutcomeGoal("route", GoalKind.ROUTE),
            )
        )
        evidence.bind_contract(contract)
        evidence.record_goal_handle("places", "ds_places")
        evidence.record_goal("places", GoalState.EVIDENCE_READY, attempted=True)
        evidence.record_goal_handle("route", "cs_route")
        evidence.record_goal("route", GoalState.EVIDENCE_READY, attempted=True)

        evidence.record_capability_result(
            "present_route",
            {"goal_key": "route", "candidate_id": "cd_route"},
            ToolResult(ok=True, data={"selection_source": "model"}),
        )

        self.assertEqual(evidence.state_for("places"), GoalState.EVIDENCE_READY)
        self.assertFalse(evaluate_completion(contract, evidence).may_terminate)

    def test_later_discovery_failure_does_not_erase_verified_results(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=2,
        )
        evidence.note_discover_places(
            ok=False,
            discovery_set_id=None,
            place_count=0,
        )
        self.assertEqual(evidence.discovery_set_id, "ds_1")
        self.assertEqual(evidence.verified_place_count, 2)
        self.assertTrue(evidence.structured_place_search_attempted)

    def test_later_route_failure_does_not_erase_prepared_candidates(self):
        evidence = TurnEvidence()
        evidence.note_prepare_route_options(ok=True, candidate_count=2)
        evidence.note_prepare_route_options(ok=False, candidate_count=0)
        self.assertEqual(evidence.route_candidate_count, 2)
        self.assertTrue(evidence.web_disabled)

    def test_arrivals_cannot_satisfy_required_service_status(self):
        evidence = TurnEvidence()
        contract = _contract(("status", GoalKind.SERVICE_STATUS, ()))
        evidence.bind_contract(contract)
        evidence.note_check_transit(ok=True, operation="arrivals")
        self.assertTrue(evidence.transit_evidence)
        self.assertEqual(evidence.state_for("status"), GoalState.PENDING)
        evidence.note_check_transit(ok=True, operation="service_status")
        evidence.record_goal("status", GoalState.EVIDENCE_READY, attempted=True)
        self.assertEqual(evidence.state_for("status"), GoalState.EVIDENCE_READY)

    def test_unavailable_requires_attempting_the_required_transit_operation(self):
        evidence = TurnEvidence()
        contract = _contract(("status", GoalKind.SERVICE_STATUS, ()))
        evidence.bind_contract(contract)
        evidence.record_goal("status", GoalState.ATTEMPTED_BUT_UNAVAILABLE)
        rejected = evaluate_completion(contract, evidence)
        self.assertFalse(rejected.may_terminate)
        self.assertEqual(rejected.required_next_actions, ("attempt:status",))
        evidence.record_goal("status", GoalState.ATTEMPTED_BUT_UNAVAILABLE, attempted=True)
        allowed = evaluate_completion(contract, evidence)
        self.assertTrue(allowed.may_terminate)

    def test_route_failure_wrapup_is_route_specific_after_prepare_failure(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=2,
        )
        evidence.note_prepare_route_options(ok=False, candidate_count=0)
        evidence.record_goal(
            "route",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
        )

        from app.services.agent.passenger_output import truthful_failure_text

        text = truthful_failure_text(evidence)

        self.assertEqual(text, "I could not find a verified route for that trip.")
        self.assertEqual(evidence.terminal_path, "truthful_failure")


if __name__ == "__main__":
    unittest.main()
