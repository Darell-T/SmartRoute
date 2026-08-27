from __future__ import annotations

import json
import time
import types
import unittest

from app.services.agent.model import output_projection as model_output_projection
from app.services.agent.model import policy
from app.services.agent.tools import ToolSpec, complete_turn, declare_goals
from app.services.agent.tools._types import ToolContext, ToolOutcome, ToolResult
from app.services.agent.turn import tool_round
from app.services.agent.turn.contract import GoalState
from app.services.agent.turn.evidence import TurnEvidence
from app.services.agent.turn.ledger import TurnToolLedger


def _block(block_id: str, name: str, tool_input: dict) -> object:
    return types.SimpleNamespace(id=block_id, name=name, input=tool_input)


class GoalAwareToolRoundTests(unittest.IsolatedAsyncioTestCase):
    def test_presenter_result_returns_only_a_compact_model_receipt(self) -> None:
        receipt = model_output_projection.project_tool_result_data(
            "present_transit",
            {
                "evidence_set_id": "te_private",
                "goal_key": "q_status",
                "operation": "service_status",
                "passenger_text": "The downtown Q has delays.",
                "lead_in": "I checked the Q.",
                "follow_up": "Want another route?",
                "presentation_outcome": {
                    "status": "presented",
                    "goal_key": "q_status",
                },
            },
        )

        assert receipt == {"presented": True, "goal_key": "q_status", "operation": "service_status", "presentation_outcome": {"status": "presented", "goal_key": "q_status"}}
        assert "passenger_text" not in json.dumps(receipt)

    async def test_identical_clarification_outcome_executes_only_once_per_turn(self) -> None:
        executions = 0

        async def runner(_name, _tool_input, _ctx, *, deadline_monotonic):
            del deadline_monotonic
            nonlocal executions
            executions += 1
            return ToolResult(
                ok=True,
                outcome=ToolOutcome.NEEDS_CLARIFICATION,
                data={"resolution_status": "ambiguous"},
            )

        ledger = TurnToolLedger(runner, 12, 4)
        ctx = ToolContext(session_id="session", turn_id="turn")
        first = await ledger.execute(
            "check_transit",
            {"operation": "arrivals", "stop_query": "34 St"},
            ctx,
            deadline_monotonic=time.monotonic() + 5,
        )
        second = await ledger.execute(
            "check_transit",
            {"operation": "arrivals", "stop_query": "34 St"},
            ctx,
            deadline_monotonic=time.monotonic() + 5,
        )

        assert first is second
        assert executions == 1

    async def test_tool_exception_becomes_a_bounded_failure(self) -> None:
        provider_message = "provider payload must not escape"

        async def explode(_tool_input, _ctx):
            raise RuntimeError(provider_message)

        result = await tool_round.run_one_tool(
            "explode",
            {},
            ToolContext(session_id="session", turn_id="turn"),
            tool_registry={
                "explode": ToolSpec(
                    {"name": "explode"},
                    explode,
                    lambda _input: "Testing",
                    2,
                )
            },
            deadline_monotonic=time.monotonic() + 5,
        )

        assert not result.ok
        assert result.error == "tool failed"

    async def _run(
        self,
        blocks: list[object],
        registry: dict[str, ToolSpec],
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ):
        ctx = ToolContext(
            session={},
            session_id="session",
            turn_id="turn",
            turn_evidence=TurnEvidence(),
        )

        async def runner(name, tool_input, tool_ctx, *, deadline_monotonic):
            return await tool_round.run_one_tool(
                name,
                tool_input,
                tool_ctx,
                tool_registry=registry,
                deadline_monotonic=deadline_monotonic,
            )

        ledger = TurnToolLedger(runner, 12, 4)
        items = [
            item
            async for item in tool_round.execute_tool_round(
                blocks,
                ctx,
                ctx.session,
                [],
                set(),
                policy.policy_for_mode("auto"),
                {},
                time.monotonic() + 5,
                ledger,
                tool_registry=registry,
                allowed_tool_names=(
                    frozenset(registry)
                    if allowed_tool_names is None
                    else allowed_tool_names
                ),
            )
        ]
        return ctx, items

    async def test_declaration_executes_before_sibling_capability(self) -> None:
        observed = []

        async def discover(_tool_input, ctx):
            observed.append(ctx.turn_evidence.turn_contract is not None)
            return ToolResult(
                ok=True,
                data={"discovery_set_id": "ds_1", "places": [{"place_id": "pl_1"}]},
            )

        registry = {
            "declare_goals": ToolSpec(
                declare_goals.DECLARE_GOALS_SCHEMA,
                declare_goals.execute,
                lambda _input: "Thinking",
                2,
            ),
            "discover_places": ToolSpec(
                {"name": "discover_places"},
                discover,
                lambda _input: "Searching",
                2,
            ),
        }
        ctx, items = await self._run(
            [
                _block(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "places",
                                "kind": "place_recommendation",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                _block("discover", "discover_places", {"goal_key": "places"}),
            ],
            registry,
        )

        assert observed == [True]
        assert ctx.turn_evidence.turn_contract is not None
        assert not any(getattr(item, "tool", None) == "declare_goals" for item in items)

    async def test_route_owned_discovery_is_not_rejected_as_unoffered(self) -> None:
        async def discover(_tool_input, _ctx):
            return ToolResult(
                ok=True,
                data={
                    "discovery_set_id": "ds_route",
                    "places": [{"place_id": "pl_1"}],
                },
            )

        registry = {
            "declare_goals": ToolSpec(
                declare_goals.DECLARE_GOALS_SCHEMA,
                declare_goals.execute,
                lambda _input: "Thinking",
                2,
            ),
            "discover_places": ToolSpec(
                {"name": "discover_places"},
                discover,
                lambda _input: "Searching",
                2,
            ),
            "prepare_route_options": ToolSpec(
                {"name": "prepare_route_options"},
                lambda _input, _ctx: ToolResult(ok=True, data={}),
                lambda _input: "Routing",
                2,
            ),
        }
        ctx, items = await self._run(
            [
                _block(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "route",
                                "kind": "route",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                _block("discover", "discover_places", {"goal_key": "route"}),
            ],
            registry,
        )

        outcomes = items[-1]["__tool_outcomes__"]
        assert outcomes[1][2].ok
        assert "tool not offered on this turn" not in (outcomes[1][2].error or "")
        ctx.turn_evidence.record_capability_result(
            "discover_places",
            {"goal_key": "route"},
            outcomes[1][2],
        )
        assert ctx.turn_evidence.state_for("route") == GoalState.PENDING

    async def test_capability_without_declaration_is_rejected(self) -> None:
        called = False

        async def discover(_tool_input, _ctx):
            nonlocal called
            called = True
            return ToolResult(ok=True, data={})

        registry = {
            "declare_goals": ToolSpec(
                declare_goals.DECLARE_GOALS_SCHEMA,
                declare_goals.execute,
                lambda _input: "Thinking",
                2,
            ),
            "discover_places": ToolSpec(
                {"name": "discover_places"},
                discover,
                lambda _input: "Searching",
                2,
            ),
        }
        _ctx, items = await self._run(
            [_block("discover", "discover_places", {"goal_key": "places"})],
            registry,
        )

        assert not called
        final = items[-1]["__tool_outcomes__"][0][2]
        assert not final.ok
        assert "declare_goals" in final.error

    async def test_general_goal_can_complete_in_declaration_round(self) -> None:
        registry = {
            "declare_goals": ToolSpec(
                declare_goals.DECLARE_GOALS_SCHEMA,
                declare_goals.execute,
                lambda _input: "Thinking",
                2,
            ),
            "complete_turn": ToolSpec(
                complete_turn.COMPLETE_TURN_SCHEMA,
                complete_turn.execute,
                lambda _input: "Finishing",
                2,
            ),
        }
        ctx, items = await self._run(
            [
                _block(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "reply",
                                "kind": "general_response",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                _block(
                    "complete",
                    "complete_turn",
                    {
                        "goal_keys": ["reply"],
                        "outcome": "answer",
                        "message": "I can help with routes and service updates.",
                    },
                ),
            ],
            registry,
        )

        assert ctx.turn_evidence.terminal
        result = items[-1]["__tool_outcomes__"][1][2]
        assert result.ok

    async def test_duplicate_terminal_calls_are_rejected_before_execution(self) -> None:
        executions = 0

        async def complete(tool_input, ctx):
            nonlocal executions
            executions += 1
            return await complete_turn.execute(tool_input, ctx)

        registry = {
            "declare_goals": ToolSpec(
                declare_goals.DECLARE_GOALS_SCHEMA,
                declare_goals.execute,
                lambda _input: "Thinking",
                2,
            ),
            "complete_turn": ToolSpec(
                complete_turn.COMPLETE_TURN_SCHEMA,
                complete,
                lambda _input: "Finishing",
                2,
            ),
        }
        _ctx, items = await self._run(
            [
                _block(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "reply",
                                "kind": "general_response",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                _block(
                    "complete-one",
                    "complete_turn",
                    {
                        "goal_keys": ["reply"],
                        "outcome": "answer",
                        "message": "First answer.",
                    },
                ),
                _block(
                    "complete-two",
                    "complete_turn",
                    {
                        "goal_keys": ["reply"],
                        "outcome": "answer",
                        "message": "Conflicting second answer.",
                    },
                ),
            ],
            registry,
        )

        outcomes = items[-1]["__tool_outcomes__"]
        assert executions == 1
        assert outcomes[1][2].ok
        assert not outcomes[2][2].ok
        assert "once per tool round" in (outcomes[2][2].error or "")
        visible_text = [
            item.text
            for item in items
            if getattr(item, "text", None)
        ]
        assert "Conflicting second answer." not in visible_text

    async def test_contextual_activity_copy_is_display_only(self) -> None:
        executed_input = None

        async def discover(tool_input, _ctx):
            nonlocal executed_input
            executed_input = tool_input
            return ToolResult(ok=True, data={"places": []})

        registry = {
            "declare_goals": ToolSpec(
                declare_goals.DECLARE_GOALS_SCHEMA,
                declare_goals.execute,
                lambda _input: "Thinking through your request…",
                2,
            ),
            "discover_places": ToolSpec(
                {"name": "discover_places"},
                discover,
                lambda _input: "Searching verified places near you…",
                2,
            ),
        }
        _ctx, items = await self._run(
            [
                _block(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "places",
                                "kind": "place_recommendation",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                _block(
                    "discover",
                    "discover_places",
                    {
                        "goal_key": "places",
                        "activity_label": "Finding a quiet dinner spot near your theater…",
                    },
                ),
            ],
            registry,
        )

        starts = [item for item in items if getattr(item, "type", None) == "tool_start"]
        assert [item.label for item in starts] == ["Finding a quiet dinner spot near your theater…"]
        assert executed_input is not None
        assert "activity_label" not in executed_input

    async def test_invalid_activity_copy_uses_server_fallback(self) -> None:
        async def discover(_tool_input, _ctx):
            return ToolResult(ok=True, data={"places": []})

        registry = {
            "declare_goals": ToolSpec(
                declare_goals.DECLARE_GOALS_SCHEMA,
                declare_goals.execute,
                lambda _input: "Thinking",
                2,
            ),
            "discover_places": ToolSpec(
                {"name": "discover_places"},
                discover,
                lambda _input: "Searching verified places near you…",
                2,
            ),
        }
        _ctx, items = await self._run(
            [
                _block(
                    "goals",
                    "declare_goals",
                    {
                        "goals": [
                            {
                                "goal_key": "places",
                                "kind": "place_recommendation",
                                "depends_on": [],
                            }
                        ]
                    },
                ),
                _block(
                    "discover",
                    "discover_places",
                    {
                        "goal_key": "places",
                        "activity_label": "Found the best place using candidate_id cd_secret",
                    },
                ),
            ],
            registry,
        )

        starts = [item for item in items if getattr(item, "type", None) == "tool_start"]
        assert [item.label for item in starts] == ["Searching verified places near you…"]

    async def test_unoffered_call_emits_no_activity_start(self) -> None:
        called = False

        async def discover(_tool_input, _ctx):
            nonlocal called
            called = True
            return ToolResult(ok=True, data={})

        registry = {
            "discover_places": ToolSpec(
                {"name": "discover_places"},
                discover,
                lambda _input: "Searching",
                2,
            )
        }
        _ctx, items = await self._run(
            [
                _block(
                    "discover",
                    "discover_places",
                    {
                        "goal_key": "places",
                        "activity_label": "Searching for a date-night restaurant…",
                    },
                )
            ],
            registry,
            allowed_tool_names=frozenset(),
        )

        assert not called
        assert not any(getattr(item, "type", None) == "tool_start" for item in items)

    async def test_same_canonical_result_reuses_one_presentation_in_one_round(self) -> None:
        executions = 0

        async def present(_tool_input, _ctx):
            nonlocal executions
            executions += 1
            return ToolResult(ok=True, data={"presented": True})

        registry = {
            "present_places": ToolSpec(
                {"name": "present_places"},
                present,
                lambda _input: "Showing verified places",
                2,
            )
        }
        _ctx, items = await self._run(
            [
                _block(
                    "first",
                    "present_places",
                    {"discovery_set_id": "ds_same", "lead_in": "One"},
                ),
                _block(
                    "duplicate",
                    "present_places",
                    {"discovery_set_id": "ds_same", "lead_in": "Two"},
                ),
            ],
            registry,
        )

        assert executions == 1
        outcomes = items[-1]["__tool_outcomes__"]
        assert outcomes[0][2].ok
        assert outcomes[1][2].ok
        assert outcomes[0][2] is outcomes[1][2]


if __name__ == "__main__":
    unittest.main()
