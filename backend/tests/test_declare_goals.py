from __future__ import annotations

import unittest

from app.services.agent.tools import assert_strict_tool_schemas_compatible
from app.services.agent.tools import declare_goals
from app.services.agent.tools._types import ToolContext
from app.services.agent.turn.contract import GoalKind, TurnContract
from app.services.agent.turn.evidence import TurnEvidence


class DeclareGoalsTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_is_provider_compatible_and_outcome_focused(self) -> None:
        assert_strict_tool_schemas_compatible((declare_goals.DECLARE_GOALS_SCHEMA,))
        text = str(declare_goals.DECLARE_GOALS_SCHEMA).casefold()
        self.assertNotIn("check_transit", text)
        self.assertNotIn("prepare_route_options", text)

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
        self.assertTrue(result.ok)
        self.assertIsInstance(result.data, TurnContract)
        self.assertEqual(result.events, [])
        self.assertEqual(result.summary, "")
        self.assertEqual(ctx.turn_evidence.turn_contract.goal("route").kind, GoalKind.ROUTE)
        self.assertTrue(
            any(
                goal.kind == GoalKind.ROUTE
                for goal in ctx.turn_evidence.turn_contract.goals
            )
        )

    async def test_executor_rejects_tool_names_and_more_than_six_goals(self) -> None:
        invalid_kind = await declare_goals.execute(
            {"goals": [{"goal_key": "x", "kind": "check_transit", "depends_on": []}]},
            ToolContext(),
        )
        self.assertFalse(invalid_kind.ok)
        too_many = await declare_goals.execute(
            {
                "goals": [
                    {"goal_key": str(index), "kind": "general_response", "depends_on": []}
                    for index in range(7)
                ]
            },
            ToolContext(),
        )
        self.assertFalse(too_many.ok)


if __name__ == "__main__":
    unittest.main()
