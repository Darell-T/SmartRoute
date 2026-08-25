"""Focused tests for safe, provider-independent turn telemetry."""

from __future__ import annotations

import unittest

from app.services.agent.model import policy as agent_policy
from app.services.agent.turn import finalization as turn_telemetry


class TurnTelemetryTests(unittest.TestCase):
    def test_safe_usage_extraction_ignores_missing_and_non_numeric_fields(self):
        usage = type(
            "Usage",
            (),
            {
                "input_tokens": 11,
                "output_tokens": "bad",
                "cache_read_input_tokens": 3,
                "thinking_tokens": None,
            },
        )()
        safe = turn_telemetry.extract_safe_usage(usage)
        self.assertEqual(safe, {"input_tokens": 11, "cache_read_input_tokens": 3})
        self.assertEqual(turn_telemetry.extract_safe_usage(None), {})
        self.assertEqual(
            turn_telemetry.extract_safe_usage({"input_tokens": 4}),
            {"input_tokens": 4},
        )

    def test_record_model_call_keeps_only_allowlisted_fields(self):
        telemetry: dict = {}
        turn_telemetry.record_model_call(
            telemetry,
            role="conversation",
            provider="anthropic",
            model="claude-sonnet-5",
            duration_ms=12.4,
            outcome="complete",
            first_token_ms=3.2,
            usage={"input_tokens": 9, "output_tokens": 2, "secret": "nope"},
        )
        call = telemetry["model_calls"][0]
        self.assertEqual(call["model"], "claude-sonnet-5")
        self.assertEqual(call["first_token_ms"], 3)
        self.assertEqual(call["input_tokens"], 9)
        self.assertEqual(call["output_tokens"], 2)
        self.assertNotIn("secret", call)

    def test_model_call_telemetry_uses_ordered_records(self):
        for mode in ("auto", "quick"):
            with self.subTest(mode=mode):
                policy = agent_policy.policy_for_mode(mode)
                telemetry = {"mode": mode}
                turn_telemetry.record_model_call(
                    telemetry,
                    role="conversation",
                    provider="anthropic",
                    model=policy.model,
                    duration_ms=12,
                    outcome="complete",
                )
                turn_telemetry.record_model_call(
                    telemetry,
                    role="route_selection",
                    provider="anthropic",
                    model=policy.model,
                    duration_ms=8,
                    outcome="complete",
                )
                self.assertEqual(
                    [call["call_index"] for call in telemetry["model_calls"]],
                    [1, 2],
                )
                self.assertNotIn("call_count", telemetry["model_calls"][0])
                self.assertEqual(
                    [call["model"] for call in telemetry["model_calls"]],
                    [policy.model, policy.model],
                )


if __name__ == "__main__":
    unittest.main()
