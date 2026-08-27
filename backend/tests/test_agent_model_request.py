from __future__ import annotations

import unittest

import pytest
from app.services.agent.model import policy
from app.services.agent.model import request as model_request
from app.services.agent.tools import TOOLS


def _auto_policy(model: str = "claude-sonnet-5") -> policy.AgentModePolicy:
    return policy.AgentModePolicy(
        mode="auto",
        model=model,
        max_route_candidates=5,
        max_presented_places=5,
        retry_count=2,
        max_output_tokens=900,
        output_effort="medium",
        max_rounds=5,
        explanation_style="comparative",
        optional_enrichment=True,
        web_research_timeout_s=6.0,
    )


class _StructuredProviderError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        error_type: str = "invalid_request_error",
        message: str = "temperature is not supported",
        request_id: str = "req_test_123",
    ):
        super().__init__("body must not be logged")
        self.status_code = status_code
        self.request_id = request_id
        self.body = {"type": "error", "error": {"type": error_type, "message": message}}


class AgentModelRequestTests(unittest.TestCase):
    def test_public_request_does_not_enable_provider_grammar_compilation(self):
        kwargs = model_request.build_stream_kwargs(
            messages=[{"role": "user", "content": "Find pizza"}],
            system_blocks=[{"type": "text", "text": "system"}],
            mode_policy=_auto_policy(),
            tools=TOOLS,
        )

        assert len(kwargs["tools"]) == 8
        assert all("strict" not in tool for tool in kwargs["tools"])
        assert kwargs["tool_choice"] == {"type": "any"}

    def test_sonnet_five_omits_incompatible_request_fields(self):
        kwargs = model_request.build_stream_kwargs(
            messages=[{"role": "user", "content": "hello"}],
            system_blocks=[{"type": "text", "text": "system"}],
            mode_policy=_auto_policy(),
            tools=[{"name": "discover_places"}],
            request_options={
                "thinking": {"type": "enabled", "budget_tokens": 2048},
                "temperature": 0.2,
                "top_p": 0.9,
                "top_k": 10,
            },
        )
        assert "thinking" not in kwargs
        assert "temperature" not in kwargs
        assert "top_p" not in kwargs
        assert "top_k" not in kwargs
        assert kwargs["output_config"] == {"effort": "medium"}
        assert kwargs["tools"] == [{"name": "discover_places"}]

    def test_sonnet_five_rejects_assistant_prefill_before_provider_call(self):
        with pytest.raises(ValueError, match="assistant prefill"):
            model_request.build_stream_kwargs(
                messages=[{"role": "assistant", "content": "prefix"}],
                system_blocks=[],
                mode_policy=_auto_policy(),
                tools=[],
            )

    def test_sonnet_five_allows_explicit_server_tool_pause_continuation(self):
        kwargs = model_request.build_stream_kwargs(
            messages=[{"role": "assistant", "content": [{"type": "server_tool_use"}]}],
            system_blocks=[],
            mode_policy=_auto_policy(),
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            allow_server_tool_continuation=True,
        )

        assert kwargs["messages"][-1]["role"] == "assistant"
        assert kwargs["tools"][0]["name"] == "web_search"

    def test_request_diagnostics_report_shape_only(self):
        kwargs = {
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "private rider text"}],
            "tools": [{"name": "discover_places", "description": "private schema"}],
            "max_tokens": 900,
        }
        diagnostics = model_request.request_diagnostics(kwargs)
        assert "model=claude-sonnet-5" in diagnostics
        assert "tools_supplied=1 tool_count=1" in diagnostics
        assert "private rider text" not in diagnostics
        assert "private schema" not in diagnostics

    def test_structured_error_is_sanitized_without_repr(self):
        exc = _StructuredProviderError(
            status_code=400,
            message=(
                "Invalid field at https://provider.test/private for 'private rider text' "
                "for 40.71280,-74.00600 sk-ant-privatevalue"
            ),
        )
        details = model_request.provider_error_details(exc)
        assert details.status_code == 400
        assert details.error_type == "invalid_request_error"
        assert "[url]" in details.message
        assert "[coordinates]" in details.message
        assert "[secret]" in details.message
        assert "private rider text" not in details.message
        assert "body must not be logged" not in details.message

    def test_deterministic_client_errors_are_not_retryable(self):
        assert not model_request.should_retry(_StructuredProviderError(status_code=400))

    def test_rate_limits_and_server_errors_are_retryable(self):
        assert model_request.should_retry(_StructuredProviderError(status_code=429))
        assert model_request.should_retry(_StructuredProviderError(status_code=500))


if __name__ == "__main__":
    unittest.main()
