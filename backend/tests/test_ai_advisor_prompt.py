import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ADVISOR_MODULE = "evaluation.route_intelligence.advisor"


def _load_ai_advisor():
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = lambda api_key=None: SimpleNamespace(api_key=api_key)
    fake_anthropic.APIStatusError = Exception

    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        if _ADVISOR_MODULE in sys.modules:
            return importlib.reload(sys.modules[_ADVISOR_MODULE])
        return importlib.import_module(_ADVISOR_MODULE)


def _restore_real_advisor() -> None:
    loaded = sys.modules.get(_ADVISOR_MODULE)
    if loaded is not None:
        importlib.reload(loaded)


class AiAdvisorPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.advisor = _load_ai_advisor()
        cls.addClassCleanup(_restore_real_advisor)

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

        assert "[ROUTE:N]" in prompt
        assert "[CANDIDATE_ANALYSIS]" in prompt
        assert "stalled vehicles" in prompt
        assert "incidents" in prompt
        assert "no reported delays" in prompt
        assert "route_candidate_labels" in prompt
        assert "route indexes" in prompt

    def test_smart_route_prompt_overrides_local_prompt(self):
        prompt_value = "custom prompt [ROUTE:N] [CANDIDATE_ANALYSIS]"
        with patch.dict(os.environ, {"SMARTROUTE_SYSTEM_PROMPT": prompt_value}):
            prompt = self.advisor._load_system_prompt(Path("__unused__.txt"))

        assert prompt == prompt_value

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

        assert prompt == "render prompt [ROUTE:N] [CANDIDATE_ANALYSIS]"

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

        assert "[ROUTE:N]" in prompt
        assert "[CANDIDATE_ANALYSIS]" in prompt
        assert prompt != "You are the SmartRoute routing engine"


if __name__ == "__main__":
    unittest.main()
