"""Turn-local web and route-isolation evidence rules."""

from __future__ import annotations

import unittest

from app.services.agent import public_surface
from app.services.agent.tools._types import ToolResult
from app.services.agent.turn.completion import evaluate_completion
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

        assert [item["new_state"] for item in evidence.goal_transitions] == ["in_flight", "satisfied"]

    def test_successful_web_is_one_use_and_claimable(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=3)
        assert evidence.may_offer_web()
        evidence.note_web(ok=True)
        assert evidence.web_used
        assert evidence.web_succeeded
        assert evidence.can_claim_research_used()
        assert not evidence.may_offer_web()

    def test_failed_web_still_consumes_the_one_use(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=2)
        evidence.note_web(ok=False)
        assert evidence.web_used
        assert not evidence.web_succeeded
        assert not evidence.may_offer_web()
        assert not evidence.can_claim_research_used()

    def test_declared_route_never_offers_web_during_discovery(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=3)
        assert not evidence.may_offer_web()

    def test_prepare_route_disables_web(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=3)
        evidence.note_prepare_route_options(ok=True, candidate_count=2)
        assert not evidence.may_offer_web()
        assert evidence.web_disabled

    def test_verify_operation_offers_web_for_current_details(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=1,
            operation="verify",
        )
        assert evidence.may_offer_web()
        assert evidence.discovery_set_id == "ds_1"
        assert evidence.structured_place_search_attempted

    def test_search_operation_may_offer_web_until_prepare(self):
        evidence = TurnEvidence()
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=2,
            operation="search",
        )
        assert evidence.may_offer_web()
        evidence.note_prepare_route_options(ok=True, candidate_count=1)
        assert not evidence.may_offer_web()

    def test_route_requirement_blocks_web_before_preparation(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=2,
            operation="search",
        )
        assert not evidence.may_offer_web()
        assert not evidence.web_disabled

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

        assert not decision.may_terminate
        assert decision.remaining_goal_keys == ("route",)
        assert not evidence.terminal

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

        assert evidence.discovery_set_id == "ds_route"
        assert evidence.verified_place_count == 1
        assert evidence.state_for("route") == GoalState.PENDING
        assert evidence.handle_for("route") is None
        assert evidence.route_candidate_count == 0
        assert "present_route" not in public_surface.state_valid_tool_names(evidence)

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

        assert evidence.state_for("route") == GoalState.EVIDENCE_READY
        assert evidence.handle_for("route") == "cs_route"
        assert "present_route" in public_surface.state_valid_tool_names(evidence)

        evidence.record_capability_result(
            "present_route",
            {"goal_key": "route", "candidate_id": "cd_1"},
            ToolResult(ok=True, data={"selection_source": "model"}),
        )
        assert evidence.state_for("route") == GoalState.SATISFIED
        assert evaluate_completion(contract, evidence).may_terminate

    def test_route_presentation_requires_compound_discovery_evidence(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            _contract(
                ("destination", GoalKind.DESTINATION_SELECTION, ()),
                ("route", GoalKind.ROUTE, ("destination",)),
            )
        )
        evidence.note_prepare_route_options(ok=True, candidate_count=1)

        assert evidence.turn_contract.dependency_blockers("route", evidence) == ("destination",)
        assert not evidence.terminal

    def test_empty_route_preparation_is_not_evidence_ready(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_prepare_route_options(ok=True, candidate_count=0)
        assert evidence.route_candidate_count == 0
        assert evidence.web_disabled
        assert evidence.state_for("route") == GoalState.PENDING

    def test_nonpresentable_route_candidates_are_not_evidence_ready(self):
        evidence = TurnEvidence()
        evidence.bind_contract(_contract(("route", GoalKind.ROUTE, ())))
        evidence.note_prepare_route_options(
            ok=True,
            candidate_count=2,
            presentation_allowed=False,
        )
        assert evidence.route_candidate_count == 0
        assert evidence.web_disabled
        assert evidence.state_for("route") == GoalState.PENDING
        assert not evidence.terminal

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

        assert evidence.state_for("route") == GoalState.ATTEMPTED_BUT_UNAVAILABLE
        assert evidence.route_candidate_count == 0
        assert not evidence.terminal

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

        assert evidence.state_for("destination") == GoalState.SATISFIED
        assert evidence.state_for("route") == GoalState.SATISFIED
        assert evaluate_completion(contract, evidence).may_terminate

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

        assert evidence.state_for("places") == GoalState.EVIDENCE_READY
        assert not evaluate_completion(contract, evidence).may_terminate

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
        assert evidence.discovery_set_id == "ds_1"
        assert evidence.verified_place_count == 2
        assert evidence.structured_place_search_attempted

    def test_later_route_failure_does_not_erase_prepared_candidates(self):
        evidence = TurnEvidence()
        evidence.note_prepare_route_options(ok=True, candidate_count=2)
        evidence.note_prepare_route_options(ok=False, candidate_count=0)
        assert evidence.route_candidate_count == 2
        assert evidence.web_disabled

    def test_arrivals_cannot_satisfy_required_service_status(self):
        evidence = TurnEvidence()
        contract = _contract(("status", GoalKind.SERVICE_STATUS, ()))
        evidence.bind_contract(contract)
        evidence.note_check_transit(ok=True, operation="arrivals")
        assert evidence.transit_evidence
        assert evidence.state_for("status") == GoalState.PENDING
        evidence.note_check_transit(ok=True, operation="service_status")
        evidence.record_goal("status", GoalState.EVIDENCE_READY, attempted=True)
        assert evidence.state_for("status") == GoalState.EVIDENCE_READY

    def test_unavailable_requires_attempting_the_required_transit_operation(self):
        evidence = TurnEvidence()
        contract = _contract(("status", GoalKind.SERVICE_STATUS, ()))
        evidence.bind_contract(contract)
        evidence.record_goal("status", GoalState.ATTEMPTED_BUT_UNAVAILABLE)
        rejected = evaluate_completion(contract, evidence)
        assert not rejected.may_terminate
        assert rejected.required_next_actions == ("attempt:status",)
        evidence.record_goal("status", GoalState.ATTEMPTED_BUT_UNAVAILABLE, attempted=True)
        allowed = evaluate_completion(contract, evidence)
        assert allowed.may_terminate

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

        assert text == "I could not find a verified route for that trip."
        assert evidence.terminal_path == "truthful_failure"


if __name__ == "__main__":
    unittest.main()
