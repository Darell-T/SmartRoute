from __future__ import annotations

import os
from unittest.mock import patch

from tests.agent_loop_reliability_support import AgentLoopRoundCapTestCase
from tests.test_agent_loop import (
    _test_registry,
)


class AgentLoopRoundCapReliabilityTests(AgentLoopRoundCapTestCase):
    async def test_round_cap_uses_deterministic_fallback_without_wrapup_call(self):
        # Every real round asks for another tool call -- the model never
        # naturally stops, so the cap must kick in after 2 rounds.
        rounds = [
            {"tool_use": [{"id": "tu_1", "name": "ok_tool", "input": {}}], "stop_reason": "tool_use"},
            {"tool_use": [{"id": "tu_2", "name": "ok_tool", "input": {}}], "stop_reason": "tool_use"},
            {"text": ["here is what I know so far"], "stop_reason": "end_turn"},
        ]
        with patch.dict(os.environ, {"AGENT_AUTO_MAX_ROUNDS": "2"}):
            events_out, _session = await self._run(rounds, tool_registry=_test_registry())

        self.assertEqual(len(self.loop.client.messages.calls), 2)
        self.assertEqual(
            [call["tool_choice"] for call in self.loop.client.messages.calls],
            [{"type": "any"}, {"type": "any"}],
        )
        self.assertIn(
            "verified result",
            "".join(
                event.text for event in events_out if event.type == "token"
            ),
        )

        done = events_out[-1]
        self.assertEqual(done.type, "done")
        self.assertEqual(done.stop_reason, "max_rounds")

    async def test_round_cap_preserves_each_mode_place_result_limit(self):
        rounds = [
            {
                "tool_use": [{"id": "tu_1", "name": "ok_tool", "input": {}}],
                "stop_reason": "tool_use",
            },
            {
                "tool_use": [{"id": "tu_2", "name": "ok_tool", "input": {}}],
                "stop_reason": "tool_use",
            },
        ]

        for mode, expected_limit in (("auto", 5), ("quick", 3)):
            with (
                self.subTest(mode=mode),
                patch.dict(
                    os.environ,
                    {
                        "AGENT_AUTO_MAX_ROUNDS": "2",
                        "AGENT_QUICK_MAX_ROUNDS": "2",
                    },
                ),
                patch.object(
                    self.loop.turn_stream.turn_completion,
                    "fallback_text",
                    return_value="Verified places.",
                ) as fallback,
            ):
                events, _session = await self._run(
                    rounds, response_presentation=mode, tool_registry=_test_registry()
                )

            self.assertEqual(fallback.call_args.kwargs["limit"], expected_limit)
            self.assertEqual(events[-1].stop_reason, "max_rounds")
