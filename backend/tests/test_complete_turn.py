"""complete_turn obligation enforcement."""

from __future__ import annotations

import unittest

from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools import complete_turn
from app.services.agent.tools._types import ToolContext
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence


def _evidence(*goals: tuple[str, GoalKind]) -> TurnEvidence:
    evidence = TurnEvidence()
    evidence.bind_contract(
        TurnContract(tuple(OutcomeGoal(key, kind) for key, kind in goals))
    )
    return evidence


def _general_evidence(goal_key: str = "response") -> TurnEvidence:
    return _evidence((goal_key, GoalKind.GENERAL_RESPONSE))


class CompleteTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unknown_terminal_outcome(self):
        result = await complete_turn.execute(
            {
                "goal_keys": ["response"],
                "outcome": "monitor",
                "message": "I will keep watching that for you.",
            },
            ToolContext(session_id="s", turn_evidence=_general_evidence()),
        )
        assert not result.ok
        assert "outcome must be" in (result.error or "")
        assert result.internal_diagnostic

    async def test_cancelled_discards_only_temporary_scenario(self):
        session = {"trip_state": trip_state_module.empty_trip_state()}
        trip_state_module.update_trip_state(
            session,
            active_candidate_set_id="cs_active",
            selected_candidate_id="cd_active",
            temporary_candidate_set_id="cs_preview",
            temporary_selected_candidate_id="cd_preview",
            temporary_base_candidate_set_id="cs_active",
        )
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("scenario", "route"),)))

        result = await complete_turn.execute(
            {
                "goal_keys": ["scenario"],
                "outcome": "cancelled",
                "message": "Okay, I kept your current trip.",
            },
            ToolContext(session=session, session_id="s", turn_evidence=evidence),
        )

        assert result.ok
        state = trip_state_module.get_trip_state(session)
        assert state["active_candidate_set_id"] == "cs_active"
        assert state["selected_candidate_id"] == "cd_active"
        assert state["temporary_candidate_set_id"] is None
        assert state["temporary_selected_candidate_id"] is None

    async def test_clarification_may_explain_what_happens_after_the_answer(self):
        evidence = _general_evidence("clarify")
        result = await complete_turn.execute(
            {
                "goal_keys": ["clarify"],
                "outcome": "clarification",
                "message": "Which station? Once you answer, I'll check arrivals.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert result.ok
        assert result.data["outcome"] == "clarification"
        assert "".join(event.text for event in result.events if event.type == "token") == "Which station? Once you answer, I'll check arrivals."

    async def test_rejects_internal_runtime_language(self):
        evidence = _general_evidence()
        result = await complete_turn.execute(
            {
                "goal_keys": ["response"],
                "outcome": "answer",
                "message": "I applied avoid_crowds and selected candidate_id cd_secret.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert not result.ok
        assert result.internal_diagnostic
        assert result.events == []

    async def test_partial_transit_recovery_targets_only_unavailable_goal(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("status", "service_status"),
                    OutcomeGoal("arrivals", "arrivals"),
                )
            )
        )
        evidence.record_goal(
            "status", GoalState.SATISFIED, attempted=True, presented=True
        )
        evidence.record_goal(
            "arrivals",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
            approved_recovery_options=("Retry arrivals for a specific station.",),
        )

        invalid = await complete_turn.execute(
            {
                "goal_keys": ["status", "arrivals"],
                "outcome": "answer",
                "message": "The checked service is delayed, but arrivals were unavailable.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert not invalid.ok
        assert "outcome=unavailable" in (invalid.error or "")
        assert invalid.events == []

        mixed_unavailable = await complete_turn.execute(
            {
                "goal_keys": ["status", "arrivals"],
                "outcome": "unavailable",
                "message": "I couldn't confirm the next arrivals from your station.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert not mixed_unavailable.ok
        assert "remove resolved" in (mixed_unavailable.error or "")
        assert "'status'" in (mixed_unavailable.error or "")
        assert "['arrivals']" in (mixed_unavailable.error or "")
        assert mixed_unavailable.events == []

        recovered = await complete_turn.execute(
            {
                "goal_keys": ["arrivals"],
                "outcome": "unavailable",
                "message": "I couldn't confirm the next arrivals from your station.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert recovered.ok
        assert recovered.data["turn_resolution"] == "partial_success_with_recovery"
        assert recovered.events[0].text == "I couldn't confirm the next arrivals from your station."

    async def test_answer_preserves_meaningful_paragraph_breaks(self):
        evidence = _general_evidence()
        result = await complete_turn.execute(
            {
                "goal_keys": ["response"],
                "outcome": "answer",
                "message": "First paragraph.\n\nSecond   paragraph.\nThird line.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert result.ok
        assert result.events[0].text == "First paragraph.\n\nSecond paragraph.\nThird line."

    async def test_refusal_may_end_without_grounding_factual_claims(self):
        evidence = _general_evidence()
        result = await complete_turn.execute(
            {
                "goal_keys": ["response"],
                "outcome": "refusal",
                "message": "I cannot invent live transit data.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert result.ok
        assert result.terminal

    async def test_refusal_cannot_skip_pending_place_presentation(self):
        evidence = _evidence(("places", GoalKind.PLACE_RECOMMENDATION))
        evidence.note_discover_places(
            ok=True,
            discovery_set_id="ds_1",
            place_count=3,
        )
        result = await complete_turn.execute(
            {
                "goal_keys": ["places"],
                "outcome": "refusal",
                "message": "I cannot help with that.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert not result.ok
        assert not evidence.terminal

    async def test_rejects_all_outcomes_while_places_must_be_presented(self):
        evidence = _evidence(("places", GoalKind.PLACE_RECOMMENDATION))
        evidence.note_discover_places(ok=True, discovery_set_id="ds_1", place_count=3)
        for outcome in ("answer", "unavailable"):
            result = await complete_turn.execute(
                {
                    "goal_keys": ["places"],
                    "outcome": outcome,
                    "message": "I found a few places.",
                },
                ToolContext(session_id="s", turn_evidence=evidence),
            )
            assert not result.ok, outcome
        assert not evidence.terminal

    async def test_rejects_without_bound_turn_contract(self):
        result = await complete_turn.execute(
            {
                "outcome": "answer",
                "message": "No delays.",
            },
            ToolContext(session_id="s", turn_evidence=TurnEvidence()),
        )
        assert not result.ok
        assert "bound TurnContract" in (result.error or "")
        assert result.internal_diagnostic
        assert result.events == []

    async def test_provider_grounded_answer_must_use_canonical_presenter(self):
        evidence = _evidence(("status", GoalKind.SERVICE_STATUS))
        evidence.record_goal(
            "status",
            GoalState.SATISFIED,
            attempted=True,
            presented=True,
        )
        result = await complete_turn.execute(
            {
                "goal_keys": ["status"],
                "outcome": "answer",
                "message": "The Q has delays.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert not result.ok
        assert "general_response" in (result.error or "")
        assert result.events == []
        assert not evidence.terminal

    async def test_unavailable_requires_an_attempted_goal(self):
        evidence = _evidence(("places", GoalKind.PLACE_RECOMMENDATION))
        evidence.record_goal(
            "places",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=False,
        )
        result = await complete_turn.execute(
            {
                "goal_keys": ["places"],
                "outcome": "unavailable",
                "message": "I could not find pizza.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert not result.ok
        assert "attempted-but-unavailable" in (result.error or "")
        assert result.events == []

    async def test_clarification_can_close_a_pending_goal(self):
        evidence = _evidence(("places", GoalKind.PLACE_RECOMMENDATION))
        result = await complete_turn.execute(
            {
                "goal_keys": ["places"],
                "outcome": "clarification",
                "message": "Which neighborhood?",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert result.ok
        assert result.terminal

    async def test_clarification_can_close_after_goal_is_unavailable(self):
        evidence = _evidence(("places", GoalKind.PLACE_RECOMMENDATION))
        evidence.record_goal(
            "places",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
        )
        result = await complete_turn.execute(
            {
                "goal_keys": ["places"],
                "outcome": "clarification",
                "message": "Could you narrow the area?",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert result.ok

    async def test_rejects_unavailable_after_goal_succeeds(self):
        evidence = _general_evidence()
        evidence.record_goal(
            "response",
            GoalState.SATISFIED,
            attempted=True,
            presented=True,
        )
        result = await complete_turn.execute(
            {
                "goal_keys": ["response"],
                "outcome": "unavailable",
                "message": "No verified status is available.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert not result.ok
        assert result.events == []

    async def test_route_answer_requires_canonical_route_presentation(self):
        evidence = _evidence(("route", GoalKind.ROUTE))
        result = await complete_turn.execute(
            {
                "goal_keys": ["route"],
                "outcome": "answer",
                "message": "Take the Q to Atlantic Av.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert not result.ok
        assert result.events == []

    async def test_compound_turn_correction_names_the_pending_route_action(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("destination", "destination_selection"),
                    OutcomeGoal("route", "route", ("destination",)),
                )
            )
        )
        evidence.record_goal(
            "destination",
            GoalState.SATISFIED,
            attempted=True,
            presented=True,
        )

        result = await complete_turn.execute(
            {
                "goal_keys": ["destination"],
                "outcome": "clarification",
                "message": "Which one would you like?",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert not result.ok
        assert "prepare_route_options" in (result.error or "")
        assert "present_route" in (result.error or "")
        assert result.events == []

    async def test_route_unavailable_requires_failed_or_empty_preparation(self):
        evidence = _evidence(("route", GoalKind.ROUTE))
        evidence.record_goal(
            "route",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
        )
        result = await complete_turn.execute(
            {
                "goal_keys": ["route"],
                "outcome": "unavailable",
                "message": "I found an available route for that trip.",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )
        assert result.ok
        assert result.events[0].text == "I could not find a verified route for this request."
        assert "available route" not in result.events[0].text

    async def test_grounding_failure_preserves_validated_model_recovery_copy(self):
        evidence = _evidence(("status", GoalKind.SERVICE_STATUS))
        evidence.record_goal(
            "status",
            GoalState.ATTEMPTED_BUT_UNAVAILABLE,
            attempted=True,
        )

        result = await complete_turn.execute(
            {
                "goal_keys": ["status"],
                "outcome": "unavailable",
                "message": "I couldn't check the Q right now. Want me to retry?",
            },
            ToolContext(session_id="s", turn_evidence=evidence),
        )

        assert result.ok
        assert result.events[0].text == "I couldn't check the Q right now. Want me to retry?"


if __name__ == "__main__":
    unittest.main()
