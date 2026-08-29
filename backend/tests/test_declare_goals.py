from __future__ import annotations

import unittest

from app.services.agent.tools import (
    assert_strict_tool_schemas_compatible,
    declare_goals,
)
from app.services.agent.tools._types import ToolContext
from app.services.agent.turn.contract import GoalKind, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


class DeclareGoalsTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_is_provider_compatible_and_outcome_focused(self) -> None:
        assert_strict_tool_schemas_compatible((declare_goals.DECLARE_GOALS_SCHEMA,))
        text = str(declare_goals.DECLARE_GOALS_SCHEMA).casefold()
        assert "check_transit" not in text
        assert "prepare_route_options" not in text

    async def test_executor_returns_typed_contract_without_rider_prose(self) -> None:
        ctx = ToolContext(turn_evidence=TurnEvidence())
        result = await declare_goals.execute(
            {
                "goals": [
                    {"goal_key": "destination", "kind": "destination_selection", "depends_on": []},
                    {"goal_key": "route", "kind": "route", "depends_on": ["destination"]},
                ]
            },
            ctx,
        )
        assert result.ok
        assert isinstance(result.data, TurnContract)
        assert result.events == []
        assert result.summary == ""
        assert ctx.turn_evidence.turn_contract.goal("route").kind == GoalKind.ROUTE
        assert any(goal.kind == GoalKind.ROUTE for goal in ctx.turn_evidence.turn_contract.goals)

    async def test_executor_rejects_tool_names_and_more_than_six_goals(self) -> None:
        invalid_kind = await declare_goals.execute(
            {"goals": [{"goal_key": "x", "kind": "check_transit", "depends_on": []}]},
            ToolContext(),
        )
        assert not invalid_kind.ok
        too_many = await declare_goals.execute(
            {
                "goals": [
                    {"goal_key": str(index), "kind": "general_response", "depends_on": []}
                    for index in range(7)
                ]
            },
            ToolContext(),
        )
        assert not too_many.ok


if __name__ == "__main__":
    unittest.main()
