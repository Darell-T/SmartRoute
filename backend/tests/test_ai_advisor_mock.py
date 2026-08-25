"""Mock advisor mode (JARVIS_MOCK_ADVISOR=1).

Lets the route-planning UI be exercised end-to-end without Anthropic
credits: real Google routes and stop enrichment flow through; only the
recommendation prose + candidate analysis are generated locally. The mock
emits the same [ROUTE:N] / [CANDIDATE_ANALYSIS] control blocks Claude
would, so the parsing path in trips.py is exercised unchanged.
"""

import importlib
import json
import re
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def _load_ai_advisor():
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = lambda api_key=None: SimpleNamespace(api_key=api_key)

    class _FakeAPIStatusError(Exception):
        status_code = 500

    fake_anthropic.APIStatusError = _FakeAPIStatusError

    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        if "evaluation.route_intelligence.advisor" in sys.modules:
            return importlib.reload(sys.modules["evaluation.route_intelligence.advisor"])
        return importlib.import_module("evaluation.route_intelligence.advisor")


def _payload():
    def transit(line, minutes):
        return {
            "type": "SUBWAY",
            "route_id": line,
            "train_line": line,
            "departure_stop": "A St",
            "arrival_stop": "B St",
            "minutes_until_arrival": minutes,
        }

    return {
        "routes": [
            [transit("Q", 20)],
            [transit("B", 28)],
            [transit("D", 21)],
        ],
        "service_alerts": [],
        "incidents": [],
        "stalled_trains": [],
        "stalled_buses": [],
    }


class MockAdvisorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.advisor = _load_ai_advisor()

    def test_atlas_uses_haiku_as_only_default_claude_model(self):
        self.assertEqual(
            self.advisor._MODEL_PRIORITY,
            ["claude-haiku-4-5-20251001"],
        )

    def test_advisor_identity_reports_the_pinned_production_model(self):
        self.assertEqual(
            self.advisor.advisor_identity(),
            {
                "advisor_provider": "anthropic",
                "advisor_model": "claude-haiku-4-5-20251001",
            },
        )

    def test_mock_recommendation_carries_control_blocks(self):
        text = self.advisor.build_mock_recommendation(_payload())

        match = re.search(r"\[ROUTE:(\d+)\]", text)
        self.assertIsNotNone(match, "must include the route tag")
        self.assertEqual(match.group(1), "0", "fastest route (index 0) recommended")

        analysis_match = re.search(
            r"\[CANDIDATE_ANALYSIS\](.*)\[/CANDIDATE_ANALYSIS\]", text, re.DOTALL
        )
        self.assertIsNotNone(analysis_match, "must include the analysis block")
        analysis = json.loads(analysis_match.group(1))
        self.assertEqual(analysis["selected_route_index"], 0)
        entries = analysis["candidate_analysis"]
        self.assertEqual(len(entries), 3)
        self.assertTrue(entries[0]["is_recommended"])
        self.assertIn("recommendation_reason", entries[0])
        self.assertFalse(entries[1]["is_recommended"])
        self.assertIn("8", entries[1]["rejection_reason"], "mentions the 8 min delta")

        # Prose mentions the picked line and reads like JARVIS.
        self.assertIn("Q", text)

    def test_mock_handles_empty_routes(self):
        text = self.advisor.build_mock_recommendation({"routes": []})
        self.assertIn("[ROUTE:0]", text)

    def test_mock_fixture_never_claims_unevidenced_disruption_safety(self):
        text = self.advisor.build_mock_recommendation(_payload()).casefold()
        self.assertNotIn("no disruption", text)
        self.assertNotIn("no reported delay", text)
        self.assertNotIn("none blocking this path", text)

    async def test_stream_yields_mock_when_flag_set(self):
        with patch.dict("os.environ", {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "1"}):
            chunks = []
            async for chunk in self.advisor.stream_recommendation(_payload()):
                chunks.append(chunk)
        text = "".join(chunks)
        self.assertIn("[ROUTE:0]", text)
        self.assertIn("[CANDIDATE_ANALYSIS]", text)

    async def test_agent_stream_uses_only_the_selected_model(self):
        captured = {}

        async def capture_transport(_payload, *, models, system_prompt):
            captured["models"] = models
            captured["system_prompt"] = system_prompt
            yield "Take the Q. [ROUTE:0]"

        with patch.object(self.advisor, "_stream_for_models", new=capture_transport):
            chunks = []
            async for chunk in self.advisor.stream_agent_recommendation(
                _payload(),
                model="claude-sonnet-4-5-20250929",
                explanation_style="comparative",
            ):
                chunks.append(chunk)

        self.assertEqual(captured["models"], ("claude-sonnet-4-5-20250929",))
        self.assertNotEqual(captured["system_prompt"], self.advisor.SYSTEM_PROMPT)
        self.assertIn("final NYC transit route-selection stage", captured["system_prompt"])
        self.assertIn("exactly one zero-based [ROUTE:N] tag", captured["system_prompt"])
        self.assertIn("candidate exactly once", captured["system_prompt"])

    async def test_rest_stream_keeps_its_pinned_model_and_prompt(self):
        captured = {}

        async def capture_transport(_payload, *, models, system_prompt):
            captured["models"] = models
            captured["system_prompt"] = system_prompt
            yield "[ROUTE:0]"

        with patch.object(self.advisor, "_stream_for_models", new=capture_transport):
            _chunks = [chunk async for chunk in self.advisor.stream_recommendation(_payload())]

        self.assertEqual(captured["models"], tuple(self.advisor._MODEL_PRIORITY))
        self.assertEqual(captured["system_prompt"], self.advisor.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
