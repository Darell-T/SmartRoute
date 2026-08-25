from __future__ import annotations

import unittest

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

        self.assertEqual(len(kwargs["tools"]), 8)
        self.assertTrue(all("strict" not in tool for tool in kwargs["tools"]))
        self.assertEqual(kwargs["tool_choice"], {"type": "any"})

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
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("top_p", kwargs)
        self.assertNotIn("top_k", kwargs)
        self.assertEqual(kwargs["output_config"], {"effort": "medium"})
        self.assertEqual(kwargs["tools"], [{"name": "discover_places"}])

    def test_sonnet_five_rejects_assistant_prefill_before_provider_call(self):
        with self.assertRaisesRegex(ValueError, "assistant prefill"):
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

        self.assertEqual(kwargs["messages"][-1]["role"], "assistant")
        self.assertEqual(kwargs["tools"][0]["name"], "web_search")

    def test_request_diagnostics_report_shape_only(self):
        kwargs = {
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "private rider text"}],
            "tools": [{"name": "discover_places", "description": "private schema"}],
            "max_tokens": 900,
        }
        diagnostics = model_request.request_diagnostics(kwargs)
        self.assertIn("model=claude-sonnet-5", diagnostics)
        self.assertIn("tools_supplied=1 tool_count=1", diagnostics)
        self.assertNotIn("private rider text", diagnostics)
        self.assertNotIn("private schema", diagnostics)

    def test_structured_error_is_sanitized_without_repr(self):
        exc = _StructuredProviderError(
            status_code=400,
            message=(
                "Invalid field at https://provider.test/private for 'private rider text' "
                "for 40.71280,-74.00600 sk-ant-privatevalue"
            ),
        )
        details = model_request.provider_error_details(exc)
        self.assertEqual(details.status_code, 400)
        self.assertEqual(details.error_type, "invalid_request_error")
        self.assertIn("[url]", details.message)
        self.assertIn("[coordinates]", details.message)
        self.assertIn("[secret]", details.message)
        self.assertNotIn("private rider text", details.message)
        self.assertNotIn("body must not be logged", details.message)

    def test_deterministic_client_errors_are_not_retryable(self):
        for status in (400, 401, 402, 403, 404):
            with self.subTest(status=status):
                self.assertFalse(
                    model_request.should_retry(_StructuredProviderError(status_code=status))
                )

    def test_rate_limits_and_server_errors_are_retryable(self):
        for status in (429, 500, 502, 529):
            with self.subTest(status=status):
                self.assertTrue(
                    model_request.should_retry(_StructuredProviderError(status_code=status))
                )


if __name__ == "__main__":
    unittest.main()
