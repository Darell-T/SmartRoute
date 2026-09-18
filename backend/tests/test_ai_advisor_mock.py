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
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_ADVISOR_MODULE = "evaluation.route_intelligence.advisor"


def _load_ai_advisor():
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = lambda api_key=None: SimpleNamespace(
        api_key=api_key
    )

    class _FakeAPIStatusError(Exception):
        status_code = 500

    fake_anthropic.APIStatusError = _FakeAPIStatusError

    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        if _ADVISOR_MODULE in sys.modules:
            return importlib.reload(sys.modules[_ADVISOR_MODULE])
        return importlib.import_module(_ADVISOR_MODULE)


def _restore_real_advisor() -> None:
    loaded = sys.modules.get(_ADVISOR_MODULE)
    if loaded is not None:
        importlib.reload(loaded)


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
        cls.addClassCleanup(_restore_real_advisor)

    def test_atlas_uses_haiku_as_only_default_claude_model(self):
        assert self.advisor._MODEL_PRIORITY == ["claude-haiku-4-5-20251001"]

    def test_advisor_identity_reports_the_pinned_production_model(self):
        assert self.advisor.advisor_identity() == {
            "advisor_provider": "anthropic",
            "advisor_model": "claude-haiku-4-5-20251001",
        }

    def test_mock_recommendation_carries_control_blocks(self):
        text = self.advisor.build_mock_recommendation(_payload())

        match = re.search(r"\[ROUTE:(\d+)\]", text)
        assert match is not None, "must include the route tag"
        assert match.group(1) == "0", "fastest route (index 0) recommended"

        analysis_match = re.search(
            r"\[CANDIDATE_ANALYSIS\](.*)\[/CANDIDATE_ANALYSIS\]", text, re.DOTALL
        )
        assert analysis_match is not None, "must include the analysis block"
        analysis = json.loads(analysis_match.group(1))
        assert analysis["selected_route_index"] == 0
        entries = analysis["candidate_analysis"]
        assert len(entries) == 3
        assert entries[0]["is_recommended"]
        assert "recommendation_reason" in entries[0]
        assert not entries[1]["is_recommended"]
        assert "8" in entries[1]["rejection_reason"], "mentions the 8 min delta"

        # Prose mentions the picked line and reads like JARVIS.
        assert "Q" in text

    def test_mock_handles_empty_routes(self):
        text = self.advisor.build_mock_recommendation({"routes": []})
        assert "[ROUTE:0]" in text

    def test_mock_fixture_never_claims_unevidenced_disruption_safety(self):
        text = self.advisor.build_mock_recommendation(_payload()).casefold()
        assert "no disruption" not in text
        assert "no reported delay" not in text
        assert "none blocking this path" not in text

    async def test_stream_yields_mock_when_flag_set(self):
        with patch.dict(
            "os.environ", {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "1"}
        ):
            chunks = [
                chunk async for chunk in self.advisor.stream_recommendation(_payload())
            ]
        text = "".join(chunks)
        assert "[ROUTE:0]" in text
        assert "[CANDIDATE_ANALYSIS]" in text

    async def test_agent_stream_uses_only_the_selected_model(self):
        captured = {}

        async def capture_transport(_payload, *, models, system_prompt, **_kwargs):
            captured["models"] = models
            captured["system_prompt"] = system_prompt
            yield "Take the Q. [ROUTE:0]"

        with patch.object(self.advisor, "_stream_for_models", new=capture_transport):
            chunks = [
                chunk
                async for chunk in self.advisor.stream_agent_recommendation(
                    _payload(),
                    model="claude-sonnet-4-5-20250929",
                    explanation_style="comparative",
                )
            ]

        assert chunks == ["Take the Q. [ROUTE:0]"]
        assert captured["models"] == ("claude-sonnet-4-5-20250929",)
        assert captured["system_prompt"] != self.advisor.SYSTEM_PROMPT
        assert "final NYC transit route-selection stage" in captured["system_prompt"]
        assert "exactly one zero-based [ROUTE:N] tag" in captured["system_prompt"]
        assert "candidate exactly once" in captured["system_prompt"]

    async def test_rest_stream_keeps_its_pinned_model_and_prompt(self):
        captured = {}

        async def capture_transport(_payload, *, models, system_prompt, **_kwargs):
            captured["models"] = models
            captured["system_prompt"] = system_prompt
            yield "[ROUTE:0]"

        with patch.object(self.advisor, "_stream_for_models", new=capture_transport):
            _chunks = [
                chunk async for chunk in self.advisor.stream_recommendation(_payload())
            ]

        assert captured["models"] == tuple(self.advisor._MODEL_PRIORITY)
        assert captured["system_prompt"] == self.advisor.SYSTEM_PROMPT

    def _overload(self, status_code: int = 529) -> Exception:
        error = self.advisor.anthropic.APIStatusError("overloaded")
        error.status_code = status_code
        return error

    async def test_live_request_fields_and_overload_retries_are_frozen(self):
        payload = _payload()
        captured: list[dict] = []

        @asynccontextmanager
        async def failing_stream(**kwargs):
            captured.append(kwargs)
            raise self._overload()
            yield

        with (
            patch.dict(
                "os.environ",
                {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "0"},
            ),
            patch.object(
                self.advisor,
                "client",
                SimpleNamespace(messages=SimpleNamespace(stream=failing_stream)),
            ),
            patch.object(self.advisor.asyncio, "sleep", new=AsyncMock()) as sleeper,
            pytest.raises(
                RuntimeError,
                match=re.escape(
                    "All Claude models are currently overloaded. Please try again."
                ),
            ),
        ):
            async for _chunk in self.advisor.stream_recommendation(payload):
                pass

        assert len(captured) == 3
        assert [call.args[0] for call in sleeper.await_args_list] == [1, 2, 4]
        assert captured[0] == {
            "model": self.advisor._MODEL_PRIORITY[0],
            "max_tokens": 512,
            "system": self.advisor.SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": json.dumps(payload)}],
        }

    async def test_non_overload_status_raises_without_retry_or_fallback(self):
        captured: list[dict] = []

        @asynccontextmanager
        async def failing_stream(**kwargs):
            captured.append(kwargs)
            raise self._overload(500)
            yield

        with (
            patch.dict(
                "os.environ",
                {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "0"},
            ),
            patch.object(
                self.advisor,
                "client",
                SimpleNamespace(messages=SimpleNamespace(stream=failing_stream)),
            ),
            patch.object(self.advisor.asyncio, "sleep", new=AsyncMock()) as sleeper,
            pytest.raises(self.advisor.anthropic.APIStatusError),
        ):
            async for _chunk in self.advisor.stream_recommendation(_payload()):
                pass

        assert len(captured) == 1
        sleeper.assert_not_awaited()

    async def test_one_overload_attempt_enters_the_provider_stream_once(self):
        payload = _payload()
        captured: list[dict] = []

        @asynccontextmanager
        async def failing_stream(**kwargs):
            captured.append(kwargs)
            raise self._overload()
            yield

        with (
            patch.dict(
                "os.environ",
                {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "0"},
            ),
            patch.object(
                self.advisor,
                "client",
                SimpleNamespace(messages=SimpleNamespace(stream=failing_stream)),
            ),
            patch.object(self.advisor.asyncio, "sleep", new=AsyncMock()) as sleeper,
            pytest.raises(
                RuntimeError,
                match=re.escape(
                    "All Claude models are currently overloaded. Please try again."
                ),
            ),
        ):
            async for _chunk in self.advisor.stream_recommendation(
                payload, overload_attempts=1
            ):
                pass

        assert len(captured) == 1
        assert [call.args[0] for call in sleeper.await_args_list] == [1]

    async def test_overload_falls_through_models_then_returns_the_first_success(self):
        captured: list[str] = []

        @asynccontextmanager
        async def stream_for_model(**kwargs):
            captured.append(kwargs["model"])
            if kwargs["model"] == "first-model" or (
                kwargs["model"] == "second-model" and captured.count("second-model") < 2
            ):
                raise self._overload()

            class _Stream:
                @property
                def text_stream(self):
                    async def _chunks():
                        yield "Take the Q. [ROUTE:0]"

                    return _chunks()

            yield _Stream()

        with (
            patch.dict(
                "os.environ",
                {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "0"},
            ),
            patch.object(
                self.advisor,
                "client",
                SimpleNamespace(messages=SimpleNamespace(stream=stream_for_model)),
            ),
            patch.object(self.advisor.asyncio, "sleep", new=AsyncMock()),
        ):
            chunks = [
                chunk
                async for chunk in self.advisor._stream_for_models(
                    _payload(),
                    models=("first-model", "second-model"),
                    system_prompt=self.advisor.SYSTEM_PROMPT,
                )
            ]

        assert chunks == ["Take the Q. [ROUTE:0]"]
        assert captured == [
            "first-model",
            "first-model",
            "first-model",
            "second-model",
            "second-model",
        ]

    async def test_blank_agent_model_keeps_the_existing_value_error(self):
        with pytest.raises(ValueError, match="agent route model is required"):
            async for _chunk in self.advisor.stream_agent_recommendation(
                _payload(),
                model="   ",
                explanation_style="comparative",
            ):
                pass


if __name__ == "__main__":
    unittest.main()
