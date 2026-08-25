import os
import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _load_ai_advisor():
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = lambda api_key=None: SimpleNamespace(api_key=api_key)
    fake_anthropic.APIStatusError = Exception

    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        if "evaluation.route_intelligence.advisor" in sys.modules:
            return importlib.reload(sys.modules["evaluation.route_intelligence.advisor"])
        return importlib.import_module("evaluation.route_intelligence.advisor")


class AiAdvisorPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.advisor = _load_ai_advisor()

    def test_missing_local_prompt_uses_default_prompt(self):
        missing_prompt = Path("__smart_route_missing_prompt__.txt")

        with patch.dict(
            os.environ,
            {
                "SMARTROUTE_SYSTEM_PROMPT": "",
                "SYSTEM_PROMPT": "",
                "ATLAS_SYSTEM_PROMPT": "",
            },
        ):
            prompt = self.advisor._load_system_prompt(missing_prompt)

        self.assertIn("[ROUTE:N]", prompt)
        self.assertIn("[CANDIDATE_ANALYSIS]", prompt)
        self.assertIn("stalled vehicles", prompt)
        self.assertIn("incidents", prompt)
        self.assertIn("no reported delays", prompt)
        self.assertIn("route_candidate_labels", prompt)
        self.assertIn("route indexes", prompt)

    def test_smart_route_prompt_overrides_local_prompt(self):
        prompt_value = "custom prompt [ROUTE:N] [CANDIDATE_ANALYSIS]"
        with patch.dict(os.environ, {"SMARTROUTE_SYSTEM_PROMPT": prompt_value}):
            prompt = self.advisor._load_system_prompt(Path("__unused__.txt"))

        self.assertEqual(prompt, prompt_value)

    def test_system_prompt_env_name_is_supported(self):
        with patch.dict(
            os.environ,
            {
                "SMARTROUTE_SYSTEM_PROMPT": "",
                "SYSTEM_PROMPT": "render prompt [ROUTE:N] [CANDIDATE_ANALYSIS]",
                "ATLAS_SYSTEM_PROMPT": "",
            },
        ):
            prompt = self.advisor._load_system_prompt(Path("__unused__.txt"))

        self.assertEqual(prompt, "render prompt [ROUTE:N] [CANDIDATE_ANALYSIS]")

    def test_truncated_system_prompt_is_ignored(self):
        with patch.dict(
            os.environ,
            {
                "SMARTROUTE_SYSTEM_PROMPT": "",
                "SYSTEM_PROMPT": "You are the SmartRoute routing engine",
                "ATLAS_SYSTEM_PROMPT": "",
            },
        ):
            prompt = self.advisor._load_system_prompt(Path("__missing__.txt"))

        self.assertIn("[ROUTE:N]", prompt)
        self.assertIn("[CANDIDATE_ANALYSIS]", prompt)
        self.assertNotEqual(prompt, "You are the SmartRoute routing engine")


if __name__ == "__main__":
    unittest.main()
