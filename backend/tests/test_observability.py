"""Deterministic telemetry.dev integration coverage; no network or model calls."""

from __future__ import annotations

import os
import re
import secrets
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx
import pytest
import telemetry_dev
from app import observability
from app.services.agent import session as session_module
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests._fake_anthropic import FakeAsyncAnthropic, reload_agent_loop_module


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self._old_key = os.environ.pop("TELEMETRY_DEV_API_KEY", None)
        observability.shutdown()

    def tearDown(self):
        observability.shutdown()
        if self._old_key is not None:
            os.environ["TELEMETRY_DEV_API_KEY"] = self._old_key

    def _enable(self):
        observability.initialize(span_exporter=self.exporter)

    def test_missing_key_is_a_noop_and_fake_clients_are_untouched(self):
        observability.initialize()
        ctx = SimpleNamespace(session_id="session-secret", telemetry={})
        span = observability.start_turn(ctx, turn_id="turn-1", mode="auto")
        assert span.traceparent() is None
        assert "trace_id" not in ctx.telemetry

        fake = FakeAsyncAnthropic()
        assert observability.wrap_anthropic(fake) is fake
        assert tuple(self.exporter.get_finished_spans()) == ()

    def test_service_and_environment_come_from_the_supported_env_names(self):
        with patch.dict(
            os.environ,
            {
                "TELEMETRY_DEV_ENVIRONMENT": "verification",
                "OTEL_SERVICE_NAME": "smartroute-test",
            },
        ):
            self._enable()
            with telemetry_dev.start_span("smartroute.telemetry.verify"):
                pass
            observability.shutdown()

        span = self.exporter.get_finished_spans()[0]
        assert span.resource.attributes["service.name"] == "smartroute-test"
        assert span.resource.attributes["deployment.environment.name"] == "verification"

    async def test_real_client_wrapper_emits_nested_safe_trace(self):
        self._enable()

        async def response(_request):
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "hello"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            )

        http = anthropic.DefaultAsyncHttpxClient(
            transport=httpx.MockTransport(response)
        )
        client = anthropic.AsyncAnthropic(api_key="test", http_client=http)
        wrapped = observability.wrap_anthropic(client)
        assert wrapped is client
        ctx = SimpleNamespace(
            session_id="session-secret",
            turn_id="turn-1",
            telemetry={},
        )
        try:
            with telemetry_dev.start_span(
                "smartroute.telemetry.verify",
                type="agent",
                input={"secret": "never capture"},
                output={"secret": "never capture"},
            ):
                turn = observability.start_turn(ctx, turn_id="turn-1", mode="auto")
                with observability.activate(turn):
                    result = await wrapped.messages.create(
                        model="claude-sonnet-5",
                        max_tokens=10,
                        messages=[{"role": "user", "content": "secret"}],
                    )
                    tool = observability.start_tool(ctx, "check_transit")
                    with observability.activate(tool):
                        observability.finish_tool(tool, ok=True)
                    observability.finish_turn(
                        turn,
                        {
                            "turn_resolution": "completed",
                            "selection_source": "model",
                            "goal_states": {"route": {"kind": "route"}},
                            "route_candidate_diagnostics": {
                                "final_structurally_unique_candidate_count": 2
                            },
                        },
                    )
        finally:
            await client.close()
            observability.shutdown()

        assert result.id == "msg_1"
        spans = self.exporter.get_finished_spans()
        names = {span.name for span in spans}
        assert names == {
            "smartroute.telemetry.verify",
            "smartroute.agent.turn",
            "chat claude-sonnet-5",
            "smartroute.agent.tool",
        }
        verify = next(
            span for span in spans if span.name == "smartroute.telemetry.verify"
        )
        turn = next(span for span in spans if span.name == "smartroute.agent.turn")
        generation = next(span for span in spans if span.name == "chat claude-sonnet-5")
        tool = next(span for span in spans if span.name == "smartroute.agent.tool")
        assert turn.parent.span_id == verify.context.span_id
        assert generation.parent.span_id == turn.context.span_id
        assert tool.parent.span_id == turn.context.span_id
        assert re.search(r"^[0-9a-f]{32}$", ctx.telemetry["trace_id"])
        assert turn.attributes["smartroute.candidate_family_count"] == 2
        assert tool.attributes["smartroute.capability"] == "check_transit"
        assert "session-secret" not in str(turn.attributes)
        assert "secret" not in str(verify.attributes)
        forbidden_content = {
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
        }
        for span in spans:
            assert forbidden_content.isdisjoint(span.attributes)

    async def test_real_client_wrapper_preserves_native_async_stream(self):
        self._enable()
        body = (
            "\n\n".join(
                (
                    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_stream","type":"message","role":"assistant","content":[],"model":"claude-sonnet-5","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":3,"output_tokens":1}}}',
                    'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
                    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}',
                    'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}',
                    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":2}}',
                    'event: message_stop\ndata: {"type":"message_stop"}',
                )
            )
            + "\n\n"
        )

        async def response(_request):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body.encode("utf-8"),
            )

        http = anthropic.DefaultAsyncHttpxClient(
            transport=httpx.MockTransport(response)
        )
        client = anthropic.AsyncAnthropic(
            api_key="test", http_client=http, max_retries=0
        )
        wrapped = observability.wrap_anthropic(client)
        try:
            async with wrapped.messages.stream(
                model="claude-sonnet-5",
                max_tokens=10,
                messages=[{"role": "user", "content": "secret"}],
            ) as stream:
                text = "".join([chunk async for chunk in stream.text_stream])
                final = await stream.get_final_message()
        finally:
            await client.close()
            observability.shutdown()

        assert text == "hello"
        assert final.id == "msg_stream"
        generation = next(
            span
            for span in self.exporter.get_finished_spans()
            if span.name == "chat claude-sonnet-5"
        )
        assert generation.attributes["gen_ai.usage.output_tokens"] == 2
        assert "secret" not in str(generation.attributes)

    async def test_real_client_wrapper_preserves_provider_exceptions(self):
        self._enable()

        async def response(request):
            message = "provider unavailable"
            raise httpx.ConnectError(message, request=request)

        http = anthropic.DefaultAsyncHttpxClient(
            transport=httpx.MockTransport(response)
        )
        client = anthropic.AsyncAnthropic(
            api_key="test", http_client=http, max_retries=0
        )
        wrapped = observability.wrap_anthropic(client)
        try:
            with pytest.raises(anthropic.APIConnectionError):
                await wrapped.messages.create(
                    model="claude-sonnet-5",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "secret"}],
                )
        finally:
            await client.close()
            observability.shutdown()

        generation = next(
            span
            for span in self.exporter.get_finished_spans()
            if span.name == "chat claude-sonnet-5"
        )
        assert generation.attributes["error.type"] == "APIConnectionError"
        assert "secret" not in str(generation.attributes)

    async def test_enabled_telemetry_does_not_change_a_fake_agent_turn(self):
        self._enable()
        expected = "I can help with an NYC trip or transit question."
        loop = reload_agent_loop_module(
            rounds=[
                {
                    "tool_use": [
                        {
                            "id": "goals",
                            "name": "declare_goals",
                            "input": {
                                "goals": [
                                    {
                                        "goal_key": "response",
                                        "kind": "general_response",
                                        "depends_on": [],
                                    }
                                ]
                            },
                        },
                        {
                            "id": "done",
                            "name": "complete_turn",
                            "input": {
                                "goal_keys": ["response"],
                                "outcome": "answer",
                                "message": expected,
                            },
                        },
                    ],
                    "stop_reason": "tool_use",
                }
            ],
            env={"SMARTROUTE_ENV": "test", "AGENT_MOCK_MODE": "0"},
        )
        session_id, session = session_module.new_session()
        trace = loop.TurnTrace()
        with (
            patch.object(loop.budget, "agent_enabled", return_value=True),
            patch.object(loop.budget, "check_session_rate_limit", return_value=True),
            patch.object(loop.budget, "daily_spend_exceeded", return_value=False),
        ):
            events = [
                event
                async for event in loop.run_agent_turn(
                    session=session,
                    session_id=session_id,
                    turn_id=secrets.token_hex(8),
                    message="What can you do?",
                    now_et="2026-08-24T12:00:00-04:00",
                    response_presentation="auto",
                    trace=trace,
                )
            ]

        assert (
            "".join(event.text for event in events if event.type == "token") == expected
        )
        assert events[-1].type == "done"
        assert re.search(r"^[0-9a-f]{32}$", trace.telemetry["trace_id"])
        spans = self.exporter.get_finished_spans()
        assert [span.name for span in spans].count("smartroute.agent.tool") == 2
        assert [span.name for span in spans].count("smartroute.agent.turn") == 1
        assert not any(span.name.startswith("chat ") for span in spans)


if __name__ == "__main__":
    unittest.main()
