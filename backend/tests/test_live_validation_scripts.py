"""Network-isolated tests for the explicitly opt-in live certification CLIs."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

from app.services.incidents.ny511 import NY511Settings
from scripts.live_checks import advisor as advisor_script
from scripts.live_checks import anthropic_agent as anthropic_script
from scripts.live_checks import crowd_search as crowd_search_script
from scripts.live_checks import google_routes as google_routes_script
from scripts.live_checks import ny511 as ny511_script
from scripts.live_checks import ticketmaster as ticketmaster_script


class Live511NYCertificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_skips_without_constructing_or_fetching(self):
        settings = NY511Settings(api_key=None, enabled=False, diagnostic="API key not configured")
        result = await ny511_script.certify(settings)
        self.assertEqual(result, {"status": "skipped", "reason": "NY511_API_KEY is not configured"})

    async def test_certification_fetches_once_then_only_searches_local_snapshot(self):
        settings = NY511Settings(api_key="secret-key", enabled=True, request_timeout_seconds=1.0)
        event = {"ID": "event-1", "Latitude": 40.7128, "Longitude": -74.006, "County": "New York"}
        with patch("app.services.incidents.ny511.NY511Client.fetch_events", new=AsyncMock(return_value=[event])) as fetch:
            result = await ny511_script.certify(settings)
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["route_request_made_upstream_fetch"])
        self.assertEqual(result["nyc_record_count"], 1)
        self.assertNotIn("secret-key", str(result))

    async def test_certification_does_not_retry_a_failed_live_request(self):
        settings = NY511Settings(api_key="secret-key", enabled=True, request_timeout_seconds=1.0)
        with patch(
            "app.services.incidents.ny511.NY511Client.fetch_events",
            new=AsyncMock(side_effect=RuntimeError("key=secret-key")),
        ) as fetch:
            result = await ny511_script.certify(settings)
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("secret-key", str(result))


class LiveTicketmasterCertificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_skips_without_network(self):
        with patch.dict(os.environ, {"TICKETMASTER_API_KEY": ""}, clear=False):
            result = await ticketmaster_script.certify()
        self.assertEqual(result["status"], "skipped")

    async def test_certification_uses_production_lookup_once_and_redacts_failure(self):
        class Result:
            ok = False
            data = None

        with patch.dict(os.environ, {"TICKETMASTER_API_KEY": "ticketmaster-secret"}, clear=False), patch(
            "app.services.trips.crowds.event_provider._lookup_uncached", new=AsyncMock(return_value=Result())
        ) as lookup:
            result = await ticketmaster_script.certify()
        self.assertEqual(lookup.await_count, 1)
        self.assertEqual(lookup.await_args.kwargs["max_pages"], 1)
        self.assertEqual(result, {"status": "failed", "reason": "Ticketmaster request or normalization failed"})
        self.assertNotIn("ticketmaster-secret", str(result))

    async def test_certification_counts_normalized_event_fields(self):
        class Result:
            ok = True
            data = {"events": [
                {
                    "venue_latitude": 40.7,
                    "venue_longitude": -74.0,
                    "venue_key": "msg",
                    "estimated_end_iso": "2026-07-16T23:00:00Z",
                    "start_iso": "2026-07-16T20:00:00Z",
                    "status": "onsale",
                    "start_time_status": "confirmed",
                },
                {"venue_latitude": None, "venue_longitude": None, "start_time_status": "date_only"},
            ]}

        with patch.dict(os.environ, {"TICKETMASTER_API_KEY": "ticketmaster-secret"}, clear=False), patch(
            "app.services.trips.crowds.event_provider._lookup_uncached", new=AsyncMock(return_value=Result())
        ):
            result = await ticketmaster_script.certify()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["normalized_event_count"], 2)
        self.assertEqual(result["events_with_venue_coordinates"], 1)
        self.assertEqual(result["events_with_confirmed_time"], 1)
        self.assertEqual(result["crowd_windows_constructed"], 1)

    async def test_certification_limits_production_fetch_to_one_page(self):
        payload = {
            "_embedded": {"events": [{
                "id": "event-1",
                "name": "Knicks",
                "dates": {
                    "start": {"dateTime": "2026-07-16T20:00:00Z"},
                    "status": {"code": "onsale"},
                },
                "classifications": [{"segment": {"name": "Sports"}}],
                "_embedded": {"venues": [{
                    "name": "Madison Square Garden",
                    "location": {"latitude": "40.7505", "longitude": "-73.9934"},
                }]},
            }]},
            "page": {"totalPages": 99},
        }
        with patch.dict(os.environ, {"TICKETMASTER_API_KEY": "ticketmaster-secret"}, clear=False), patch(
            "app.services.trips.crowds.event_provider.fetch_json", new=AsyncMock(return_value=(payload, None))
        ) as fetch:
            result = await ticketmaster_script.certify()
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(result["status"], "passed")

    async def test_certification_redacts_an_unexpected_provider_failure(self):
        with patch.dict(os.environ, {"TICKETMASTER_API_KEY": "ticketmaster-secret"}, clear=False), patch(
            "app.services.trips.crowds.event_provider._lookup_uncached", new=AsyncMock(side_effect=RuntimeError("url?key=ticketmaster-secret"))
        ):
            result = await ticketmaster_script.certify()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("ticketmaster-secret", str(result))


class LiveCrowdSearchCertificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_skips_without_network(self):
        with patch.dict(os.environ, {"XAI_API_KEY": ""}, clear=False):
            result = await crowd_search_script.certify()
        self.assertEqual(result["status"], "skipped")

    async def test_certification_reports_only_safe_normalized_counts(self):
        from app.services.trips.crowds import search as crowd_search

        safe_result = {
            "status": "complete",
            "events": [
                {
                    "source_class": "official_web",
                    "source_ref": "https://example.invalid/private-source",
                }
            ],
            "completed_sources": ["web_search", "x_search"],
        }
        with (
            patch.dict(os.environ, {"XAI_API_KEY": "xai-secret"}, clear=False),
            patch.object(crowd_search, "_run_search", new=AsyncMock(return_value=safe_result)),
        ):
            result = await crowd_search_script.certify()

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["completed_sources"], ["web_search", "x_search"])
        self.assertEqual(result["source_classes"], ["official_web"])
        self.assertNotIn("xai-secret", str(result))
        self.assertNotIn("private-source", str(result))

    async def test_partial_provider_coverage_fails_certification(self):
        from app.services.trips.crowds import search as crowd_search

        with (
            patch.dict(os.environ, {"XAI_API_KEY": "xai-secret"}, clear=False),
            patch.object(
                crowd_search,
                "_run_search",
                new=AsyncMock(
                    return_value={
                        "status": "partial",
                        "events": [],
                        "completed_sources": ["web_search"],
                    }
                ),
            ),
        ):
            result = await crowd_search_script.certify()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["provider_status"], "partial")


class LiveAdvisorCertificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key_skips_and_reports_safe_identity(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = await advisor_script.certify()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["advisor_provider"], "anthropic")
        self.assertNotIn("ANTHROPIC_API_KEY", str(result))

    async def test_certification_reports_only_selection_and_identity(self):
        raw = '[ROUTE:1][CANDIDATE_ANALYSIS]{"selected_route_index":1,"candidate_analysis":[{"index":0,"is_recommended":false,"rejection_reason":"slower"},{"index":1,"is_recommended":true,"recommendation_reason":"faster"}]}[/CANDIDATE_ANALYSIS]'
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-secret"}, clear=False), patch(
            "evaluation.route_intelligence.advisor.collect_recommendation", new=AsyncMock(return_value=raw)
        ) as collect:
            result = await advisor_script.certify()
        self.assertEqual(collect.await_count, 1)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["selected_route_index"], 1)
        self.assertNotIn("anthropic-secret", str(result))
        self.assertNotIn("recommendation_reason", str(result))

    async def test_failure_reports_only_exception_type(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-secret"}, clear=False), patch(
            "evaluation.route_intelligence.advisor.collect_recommendation", new=AsyncMock(side_effect=RuntimeError("key=anthropic-secret"))
        ):
            result = await advisor_script.certify()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("anthropic-secret", str(result))

    async def test_malformed_model_output_is_not_accepted_as_route_zero_fallback(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-secret"}, clear=False), patch(
            "evaluation.route_intelligence.advisor.collect_recommendation", new=AsyncMock(return_value="The Q is fastest.")
        ):
            result = await advisor_script.certify()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "advisor response did not satisfy the route-selection schema")

    async def test_timeout_reports_only_timeout_type(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-secret"}, clear=False), patch(
            "evaluation.route_intelligence.advisor.collect_recommendation", new=AsyncMock(side_effect=TimeoutError("key=anthropic-secret"))
        ):
            result = await advisor_script.certify()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "TimeoutError")
        self.assertNotIn("anthropic-secret", str(result))


class LiveCertificationCliTests(unittest.TestCase):
    def test_all_clis_require_live_flag_without_starting_async_work(self):
        for module in (
            anthropic_script,
            ny511_script,
            ticketmaster_script,
            advisor_script,
            crowd_search_script,
            google_routes_script,
        ):
            with self.subTest(module=module.__name__), patch.object(sys, "argv", [module.__name__]), patch.object(
                module.asyncio, "run"
            ) as run:
                self.assertEqual(module.main(), 0)
                run.assert_not_called()

    def test_live_cli_missing_key_skips_without_network(self):
        with patch.object(sys, "argv", ["advisor", "--live"]), patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False
        ), patch("evaluation.route_intelligence.advisor.collect_recommendation", new=AsyncMock()) as collect, patch(
            "builtins.print"
        ) as output:
            self.assertEqual(advisor_script.main(), 0)
        collect.assert_not_awaited()
        printed = " ".join(str(call) for call in output.call_args_list)
        self.assertIn("advisor key is not configured", printed)
        self.assertNotIn("ANTHROPIC_API_KEY", printed)

    def test_failed_live_cli_returns_nonzero_without_exposing_details(self):
        with patch.object(sys, "argv", ["ny511", "--live"]), patch.object(
            ny511_script, "certify", new=AsyncMock(return_value={"status": "failed", "reason": "key=secret"})
        ), patch("builtins.print") as output:
            self.assertEqual(ny511_script.main(), 1)
        printed = " ".join(str(call) for call in output.call_args_list)
        self.assertIn("511NY live certification", printed)
        self.assertNotIn("secret", printed)


if __name__ == "__main__":
    unittest.main()
