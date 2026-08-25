"""Focused route-presentation framing and canonical-fact guards."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from app.services.agent.tools.route import prepare_route_options
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence
from tests.conversation.conversation_matrix_harness import clear_caches
from tests.single_agent_route_test_support import _ctx, _prepared_leg
from app.services.trips.selection_decision import evaluate_candidate_decision

def _supported_reason_codes(record: dict, entry: dict) -> set[str]:
    return evaluate_candidate_decision(record, entry)["supported_reason_codes"]


class PresentRouteFramingTestMixin:
    def setUp(self) -> None:
        clear_caches()

    async def _prepared_context(self, prepared=None, prepare_input=None):
        ctx = _ctx("sess-route-framing")
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("route", GoalKind.ROUTE),))
        )
        ctx.turn_evidence = evidence
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=prepared or _prepared_leg()),
        ):
            route_input = {
                "destination": "Barclays Center",
                "destination_source": "current_turn",
                "goal_key": "route",
            }
            route_input.update(prepare_input or {})
            prepared = await prepare_route_options.execute(
                route_input,
                ctx,
            )
        self.assertTrue(prepared.ok, prepared.error)
        self.assertNotIn(
            "supported_reason_codes",
            prepared.data["candidates"][0],
        )
        candidate_id = prepared.data["candidates"][0]["candidate_id"]
        evidence.record_goal_handle("route", prepared.data["candidate_set_id"])
        evidence.record_goal("route", GoalState.EVIDENCE_READY, attempted=True)
        return ctx, candidate_id, prepared.data["candidate_set_id"]
