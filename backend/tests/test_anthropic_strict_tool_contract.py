"""Bounded live Anthropic contract checks for strict custom tool profiles.

Opt-in: set RUN_ANTHROPIC_TOOL_CONTRACT=1 and provide ANTHROPIC_API_KEY.
These tests only assert provider acceptance and a tool-use or grounded
completion shape. They never log prompts, keys, coordinates, or full schemas.
"""

from __future__ import annotations

import os
import unittest

import anthropic

from app.services.agent import intelligence, loop, policy
from app.services.agent.tools import plan_trip

_RUN = os.getenv("RUN_ANTHROPIC_TOOL_CONTRACT", "").strip() == "1"
_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()


def _load_api_key() -> str:
    if _API_KEY:
        return _API_KEY
    # Local .env fallback for operator-run certification only.
    try:
        from pathlib import Path

        env_path = Path(__file__).resolve().parents[2] / ".env"
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


@unittest.skipUnless(_RUN, "set RUN_ANTHROPIC_TOOL_CONTRACT=1 to run live provider checks")
class AnthropicStrictToolContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        key = _load_api_key()
        if not key:
            raise unittest.SkipTest("ANTHROPIC_API_KEY required for live contract tests")
        cls.client = anthropic.Anthropic(api_key=key, max_retries=0)

    def _request(self, *, model: str, tools: list[dict], message: str):
        return self.client.messages.create(
            model=model,
            max_tokens=256,
            tools=tools,
            messages=[{"role": "user", "content": message}],
        )

    def _assert_provider_accepted(self, response) -> None:
        self.assertIsNotNone(response)
        self.assertTrue(response.content)
        types = [getattr(block, "type", None) for block in response.content]
        # Provider accepted the tool profile; either tool_use or text/thinking.
        self.assertTrue(
            any(t in {"tool_use", "text", "thinking", "server_tool_use"} for t in types),
            msg=f"unexpected block types={types}",
        )
        self.assertNotEqual(getattr(response, "stop_reason", None), "error")

    def test_auto_sonnet_accepts_plan_trip_profile(self):
        model = policy.policy_for_mode("auto").model
        tools = [plan_trip.PLAN_TRIP_SCHEMA]
        response = self._request(
            model=model,
            tools=tools,
            message="Plan a trip to Coney Island with less walking.",
        )
        self._assert_provider_accepted(response)

    def test_quick_haiku_accepts_plan_trip_profile(self):
        model = policy.policy_for_mode("quick").model
        tools = [plan_trip.PLAN_TRIP_SCHEMA]
        response = self._request(
            model=model,
            tools=tools,
            message="Plan a trip to Coney Island with less walking.",
        )
        self._assert_provider_accepted(response)

    def test_auto_accepts_web_search_plus_multi_tool_profile(self):
        mode = policy.policy_for_mode("auto")
        parsed = intelligence.ParsedIntent(intent="destination_discovery", avoid_crowds=False)
        tools = loop._tools_for_intent(parsed, mode)
        names = {tool.get("name") for tool in tools}
        self.assertTrue({"prepare_route_options", "present_route"}.issubset(names))
        self.assertNotIn("plan_trip", names)
        self.assertNotIn("poi_search", names)
        self.assertTrue(any(tool.get("name") == "web_search" for tool in tools))
        response = self._request(
            model=mode.model,
            tools=tools,
            message="Find a strong pancake option for Sunday morning and route me there with less walking.",
        )
        self._assert_provider_accepted(response)


if __name__ == "__main__":
    unittest.main()
