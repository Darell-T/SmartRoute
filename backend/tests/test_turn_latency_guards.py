from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services import cache
from app.services.agent import loop
from app.services.agent.model import policy
from app.services.agent.model import stream as model_stream
from app.services.agent import public_surface
from app.services.agent import events as agent_events
from app.services.agent.tools import ToolResult, ToolSpec
from app.services.agent.tools import declare_goals
from tests.test_agent_loop import (
    _AgentLoopHelpers,
    _load_agent_loop,
    _test_registry,
)


def _goal_registry() -> dict[str, ToolSpec]:
    """Add the real declaration capability to the mechanics fake registry."""

    registry = _test_registry()
    check_transit = registry["check_transit"].executor

    async def check_with_evidence_handle(tool_input, ctx):
        result = await check_transit(tool_input, ctx)
        result.data = {
            **(result.data if isinstance(result.data, dict) else {}),
            "evidence_set_id": "es_test_only",
        }
        return result

    async def present_transit(tool_input, ctx):
        return ToolResult(
            ok=True,
            data={
                "evidence_set_id": tool_input.get("evidence_set_id"),
                "goal_key": tool_input.get("goal_key"),
            },
            summary="Presented verified transit evidence",
            events=[agent_events.TokenEvent(text="Live data says 4 minutes.")],
            terminal=True,
            terminal_path="present_transit",
        )

    registry["check_transit"].executor = check_with_evidence_handle
    registry["present_transit"] = ToolSpec(
        schema={"name": "present_transit"},
        executor=present_transit,
        label_fn=lambda _input: "Presenting verified transit…",
        timeout_s=5.0,
    )
    registry["declare_goals"] = ToolSpec(
        schema={"name": "declare_goals"},
        executor=declare_goals.execute,
        label_fn=lambda _input: "Reviewing your request…",
        timeout_s=5.0,
    )
    return registry


def _declared_route_round() -> dict:
    return {
        "tool_use": [
            {
                "id": "tu_goals",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": "route",
                            "kind": "route",
                            "depends_on": [],
                        }
                    ]
                },
            },
            {
                "id": "tu_1",
                "name": "prepare_route_options",
                "input": {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "goal_key": "route",
                },
            },
        ],
        "stop_reason": "tool_use",
    }


def _declared_arrival_round() -> dict:
    return {
        "tool_use": [
            {
                "id": "tu_goals",
                "name": "declare_goals",
                "input": {
                    "goals": [
                        {
                            "goal_key": "arrivals",
                            "kind": "arrivals",
                            "depends_on": [],
                        }
                    ]
                },
            },
            {
                "id": "tu_1",
                "name": "check_transit",
                "input": {
                    "operation": "arrivals",
                    "route_ids": ["Q"],
                    "stop_query": "Church Ave",
                    "goal_key": "arrivals",
                },
            },
        ],
        "stop_reason": "tool_use",
    }


def _goal_present_route_round() -> dict:
    return {
        "tool_use": [
            {
                "id": "tu_2",
                "name": "present_route",
                "input": {
                    "candidate_id": "cd_test_only",
                    "goal_key": "route",
                    "lead_in": "This option best fits the trip and preferences you gave me.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
            }
        ],
        "stop_reason": "tool_use",
    }


def _goal_present_transit_round(message: str) -> dict:
    return {
        "tool_use": [
            {
                "id": "tu_2",
                "name": "present_transit",
                "input": {
                    "evidence_set_id": "es_test_only",
                    "goal_key": "arrivals",
                },
            }
        ],
        "stop_reason": "tool_use",
    }


class TurnLatencyGuardTests(_AgentLoopHelpers, unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = _load_agent_loop()

    def setUp(self):
        cache._mem.clear()
    async def test_model_progress_prose_is_discarded_before_route_tools(self) -> None:
        rounds = [
            {
                **_declared_route_round(),
                "text": ["I'll compare live routes ", "to Barclays Center."],
            },
            _goal_present_route_round(),
        ]

        events_out, _session = await self._run(
            rounds,
            message="Get me from Union Square to Barclays Center",
            tool_registry=_goal_registry(),
        )

        event_types = [event.type for event in events_out]
        first_tool = event_types.index("tool_start")
        tokens_before_tools = [
            event.text
            for event in events_out[:first_tool]
            if event.type == "token"
        ]
        self.assertEqual(tokens_before_tools, [])
        reasoning = [event.text for event in events_out if event.type == "reasoning"]
        self.assertEqual(reasoning[0], "Thinking through your request…")

    async def test_ungrounded_arrival_prose_is_not_shown_before_the_tool(self) -> None:
        rounds = [
            {
                **_declared_arrival_round(),
                "text": ["The next Q is in 3 minutes."],
            },
            _goal_present_transit_round(
                "Live data says the next downtown Q is in 4 minutes."
            ),
        ]

        events_out, _session = await self._run(
            rounds,
            message="When is the next downtown Q at Church Ave?",
            tool_registry=_goal_registry(),
        )

        rider_text = "".join(
            event.text for event in events_out if event.type == "token"
        )
        self.assertNotIn("3 minutes", rider_text)
        self.assertIn("4 minutes", rider_text)

    async def test_first_visible_token_is_recorded_separately_from_model_production(
        self,
    ) -> None:
        trace = self.loop.TurnTrace()
        events_out, _session = await self._run(
            [
                {
                    **_declared_arrival_round(),
                    "text": ["The next Q is in 3 minutes."],
                },
                _goal_present_transit_round("Live data says 4 minutes."),
            ],
            message="When is the next downtown Q at Church Ave?",
            tool_registry=_goal_registry(),
            trace=trace,
        )
        rider_text = "".join(
            event.text for event in events_out if event.type == "token"
        )
        self.assertIn("4 minutes", rider_text)
        self.assertNotIn("3 minutes", rider_text)
        self.assertIn("conversation_first_visible_token_ms", trace.stage_ms)
        self.assertGreaterEqual(
            trace.stage_ms["conversation_first_visible_token_ms"],
            trace.stage_ms.get("conversation_first_token_ms", 0.0),
        )


class ToolSurfaceAndTimeoutDefaultsTests(unittest.TestCase):
    def test_plain_route_surface_is_initial_state_surface(self) -> None:
        tools = loop._tools_for_state()
        self.assertEqual(
            {schema["name"] for schema in tools},
            set(public_surface.INITIAL_TOOL_NAMES),
        )
        self.assertTrue(all("strict" not in schema for schema in tools))

    def test_auto_retries_once_and_attempts_time_out_sooner(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in (
                "AGENT_AUTO_RETRY_COUNT",
                "AGENT_MODEL_ATTEMPT_TIMEOUT_S",
            ):
                os.environ.pop(key, None)
            automatic = policy.policy_for_mode("auto")
        self.assertEqual(automatic.retry_count, 1)
        self.assertEqual(model_stream.MODEL_ATTEMPT_TIMEOUT_S, 15.0)


if __name__ == "__main__":
    unittest.main()
