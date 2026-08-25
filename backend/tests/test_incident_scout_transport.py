"""Contract tests for the optional xAI transport behind the incident scout."""

from __future__ import annotations

import asyncio
import contextlib
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services.incidents import scout_provider as transport
from app.services.incidents.batches import INCIDENT_BATCHES
from app.services.incidents.scout_normalization import claim_ref_for, per_post_source_id

BATCH = INCIDENT_BATCHES[1]  # midtown-manhattan
NOW = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
X_URL = "https://x.com/nycdesk/status/1234567890"
X_ID = per_post_source_id(X_URL)
WEB_URL = "https://news.example.test/report"


class ResponseExtractionTests(unittest.TestCase):
    def test_response_text_handles_plain_and_sdk_shapes(self):
        self.assertEqual(
            transport.response_text(SimpleNamespace(content='{"incidents": []}')),
            '{"incidents": []}',
        )
        self.assertEqual(
            transport.response_text(
                SimpleNamespace(outputs=[SimpleNamespace(message=SimpleNamespace(content="x"))])
            ),
            "x",
        )
        self.assertEqual(transport.response_text(SimpleNamespace()), "")

    def test_response_citations_are_canonical_sorted_and_bounded(self):
        response = SimpleNamespace(
            citations=["https://x.com/B/status/2?utm=1", "https://X.com/A/status/1"],
            inline_citations=[{"url": "HTTPS://X.COM/A/status/1"}],
        )
        self.assertEqual(
            transport.response_citations(response),
            ("https://x.com/A/status/1", "https://x.com/B/status/2?utm=1"),
        )
        self.assertEqual(transport.response_citations(SimpleNamespace(citations=[])), ())


class PromptRenderingTests(unittest.TestCase):
    def test_x_prompt_has_single_brace_json_and_bounded_batch_context(self):
        prompt = transport.render_x_prompt(BATCH)
        self.assertIn('{"incidents":[{"location"', prompt)
        self.assertIn('Use {"incidents":[]}', prompt)
        self.assertNotIn("{{", prompt)
        self.assertNotIn("}}", prompt)
        self.assertIn("Midtown Manhattan", prompt)
        self.assertIn("midtown", prompt)
        self.assertIn("bounds=40.7300,-74.0200,40.7900,-73.9400", prompt)

    def test_web_prompt_has_single_brace_json_and_no_x_context(self):
        claim = {
            "claim_ref": claim_ref_for(X_ID),
            "location": "Lexington Avenue",
            "description": "FDNY on scene.",
            "severity": "high",
            "impact_scope": "subway_operations",
            "observed_at": NOW.isoformat(),
            "route_ids": ["6"],
            "stop_ids": [],
            "corridor_ids": [],
        }
        prompt = transport.render_web_prompt((claim,))
        self.assertIn('{"corroborations":[{"claim_ref"', prompt)
        self.assertIn('Use {"corroborations":[]}', prompt)
        self.assertNotIn("{{", prompt)
        self.assertNotIn("}}", prompt)
        self.assertIn(claim_ref_for(X_ID), prompt)
        self.assertIn("Lexington Avenue", prompt)
        self.assertNotIn("https://", prompt)
        self.assertNotIn("x:nycdesk", prompt)
        self.assertNotIn("source_id", prompt)

    def test_sanitized_claims_never_include_x_provenance(self):
        claim = {
            "claim_ref": claim_ref_for(X_ID),
            "location": "Lexington Avenue",
            "description": "FDNY on scene.",
            "severity": "high",
            "impact_scope": "subway_operations",
            "observed_at": NOW.isoformat(),
            "route_ids": ["6"],
            "stop_ids": [],
            "corridor_ids": [],
            "source_url": X_URL,
            "source_id": X_ID,
        }
        sanitized = transport.sanitized_claims((claim,))
        self.assertEqual(len(sanitized), 1)
        self.assertNotIn("source_url", sanitized[0])
        self.assertNotIn("source_id", sanitized[0])
        self.assertEqual(sanitized[0]["claim_ref"], claim_ref_for(X_ID))

    def test_has_client_reflects_configuration(self):
        with patch.object(transport, "AsyncClient", None):
            self.assertFalse(transport.has_client())
        with patch.object(transport, "AsyncClient", object()), patch.dict(
            os.environ, {"XAI_API_KEY": ""}
        ):
            self.assertFalse(transport.has_client())
        with patch.object(transport, "AsyncClient", object()), patch.dict(
            os.environ, {"XAI_API_KEY": "configured"}
        ):
            self.assertTrue(transport.has_client())


class TransportConstructionTests(unittest.IsolatedAsyncioTestCase):
    def _patches(self, client):
        return (
            patch.dict(os.environ, {"XAI_API_KEY": "configured"}),
            patch.object(transport, "_client", client),
            patch.object(transport, "_client_loop", asyncio.get_running_loop()),
            patch.object(transport, "system", side_effect=lambda value: value),
            patch.object(transport, "user", side_effect=lambda value: value),
            patch.object(transport, "x_search", return_value="x-tool"),
            patch.object(transport, "web_search", return_value="web-tool"),
            patch.object(
                transport, "get_tool_call_type", side_effect=lambda call: call.call_type
            ),
        )

    async def test_x_transport_single_tool_one_turn_six_hour_window(self):
        response = SimpleNamespace(
            content='{"incidents": []}',
            tool_calls=[SimpleNamespace(call_type="x_search_tool")],
            citations=[X_URL],
            server_side_tool_usage={"x_search": 1},
        )
        chat = SimpleNamespace(append=Mock(), sample=AsyncMock(return_value=response))
        client = SimpleNamespace(chat=SimpleNamespace(create=Mock(return_value=chat)))
        with contextlib.ExitStack() as stack:
            for patch_ctx in self._patches(client):
                stack.enter_context(patch_ctx)
            result = await transport._run_x_search(BATCH, now=NOW)
            x_kwargs = dict(transport.x_search.call_args.kwargs)
        self.assertTrue(result.tool_completed)
        self.assertEqual(result.citations, (X_URL,))
        chat.sample.assert_awaited_once_with()
        client.chat.create.assert_called_once()
        kwargs = client.chat.create.call_args.kwargs
        self.assertEqual(kwargs["tools"], ["x-tool"])
        self.assertEqual(kwargs["max_turns"], 1)
        self.assertEqual(transport._X_OUTPUT_MAX_TOKENS, 1_800)
        self.assertLessEqual(transport._X_OUTPUT_MAX_TOKENS, 2_000)
        self.assertEqual(kwargs["max_tokens"], transport._X_OUTPUT_MAX_TOKENS)
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertEqual(kwargs["response_format"], "json_object")
        self.assertEqual(x_kwargs["to_date"], NOW)
        self.assertEqual(x_kwargs["to_date"] - x_kwargs["from_date"], timedelta(hours=6))
        prompt = chat.append.call_args_list[0].args[0]
        self.assertIn("Midtown Manhattan", prompt)
        self.assertIn("midtown", prompt)
        self.assertNotIn("{{", prompt)

    async def test_web_transport_single_tool_nyc_location_sanitized_prompt(self):
        claim = {
            "claim_ref": claim_ref_for(X_ID),
            "location": "Lexington Avenue",
            "description": "FDNY on scene.",
            "severity": "high",
            "impact_scope": "subway_operations",
            "observed_at": NOW.isoformat(),
            "route_ids": ["6"],
            "stop_ids": [],
            "corridor_ids": [],
        }
        response = SimpleNamespace(
            content='{"corroborations": []}',
            tool_calls=[SimpleNamespace(call_type="web_search_tool")],
            citations=[WEB_URL],
            server_side_tool_usage={"web_search": 1},
        )
        chat = SimpleNamespace(append=Mock(), sample=AsyncMock(return_value=response))
        client = SimpleNamespace(chat=SimpleNamespace(create=Mock(return_value=chat)))
        with contextlib.ExitStack() as stack:
            for patch_ctx in self._patches(client):
                stack.enter_context(patch_ctx)
            result = await transport._run_web_search((claim,), now=NOW)
            web_kwargs = dict(transport.web_search.call_args.kwargs)
        self.assertTrue(result.tool_completed)
        kwargs = client.chat.create.call_args.kwargs
        self.assertEqual(kwargs["tools"], ["web-tool"])
        self.assertEqual(kwargs["max_turns"], 1)
        self.assertEqual(transport._WEB_OUTPUT_MAX_TOKENS, 800)
        self.assertLessEqual(transport._WEB_OUTPUT_MAX_TOKENS, 1_500)
        self.assertEqual(kwargs["max_tokens"], transport._WEB_OUTPUT_MAX_TOKENS)
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertEqual(web_kwargs["user_location_country"], "US")
        self.assertEqual(web_kwargs["user_location_city"], "New York")
        self.assertEqual(web_kwargs["user_location_region"], "NY")
        self.assertEqual(web_kwargs["user_location_timezone"], "America/New_York")
        prompt = chat.append.call_args_list[0].args[0]
        self.assertIn(claim_ref_for(X_ID), prompt)
        self.assertNotIn("https://", prompt)
        self.assertNotIn("x:nycdesk", prompt)
        self.assertNotIn("{{", prompt)

    async def test_transport_without_client_returns_not_completed(self):
        with patch.object(transport, "AsyncClient", None), patch.object(
            transport, "_client", None
        ):
            x_result = await transport._run_x_search(BATCH, now=NOW)
            web_result = await transport._run_web_search((), now=NOW)
        self.assertFalse(x_result.tool_completed)
        self.assertFalse(web_result.tool_completed)
        self.assertEqual(x_result.response_text, "")
        self.assertEqual(web_result.response_text, "")

    async def test_close_releases_the_shared_client(self):
        client = SimpleNamespace(close=AsyncMock())
        with patch.object(transport, "_client", client), patch.object(
            transport, "_client_loop", asyncio.get_running_loop()
        ):
            await transport.close_incident_scout_client()
            self.assertIsNone(transport._client)
            self.assertIsNone(transport._client_loop)
        client.close.assert_awaited_once_with()

    async def test_client_is_created_lazily_on_the_running_event_loop(self):
        created_on = []
        client = SimpleNamespace(close=AsyncMock())

        def create_client(**_kwargs):
            created_on.append(asyncio.get_running_loop())
            return client

        with patch.object(transport, "AsyncClient", side_effect=create_client), patch.object(
            transport, "_client", None
        ), patch.object(transport, "_client_loop", None), patch.dict(
            os.environ, {"XAI_API_KEY": "configured"}
        ):
            self.assertIs(transport._get_client(), client)
            self.assertEqual(created_on, [asyncio.get_running_loop()])
            self.assertIs(transport._client_loop, asyncio.get_running_loop())
            await transport.close_incident_scout_client()

        client.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
