from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services.trips import crowd_search, crowd_search_provider
from app.services.trips.crowd_hotspots import HotspotHit


class CrowdSearchNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.area = HotspotHit(
            route_index=0,
            hotspot_key="columbus_lincoln",
            hotspot_name="Columbus Circle and Lincoln Center",
            station_name="59 St-Columbus Circle",
            latitude=40.768,
            longitude=-73.982,
            expected_at=datetime.fromisoformat("2026-07-25T20:30:00-04:00"),
            route_id="A",
        )
        self.observed_at = datetime.fromisoformat("2026-07-25T19:00:00-04:00")

    def test_official_web_event_is_scoring_authorized(self):
        source = "https://www.lincolncenter.org/venue/calendar"
        events = crowd_search.normalize_search_payload(
            {
                "events": [
                    {
                        "hotspot_key": "columbus_lincoln",
                        "title": "Summer concert",
                        "category": "concert",
                        "venue": "Lincoln Center",
                        "start_iso": "2026-07-25T21:00:00-04:00",
                        "end_iso": "2026-07-25T23:00:00-04:00",
                        "source_ref": source,
                    }
                ]
            },
            areas={"columbus_lincoln": self.area},
            citations=[source],
            observed_at=self.observed_at,
        )

        self.assertEqual(events[0]["source_class"], "official_web")
        self.assertTrue(events[0]["scoring_authorized"])
        self.assertEqual(events[0]["venue_latitude"], self.area.latitude)

    def test_independent_x_is_corroborative_and_cannot_score(self):
        source = "https://x.com/randomaccount/status/123"
        events = crowd_search.normalize_search_payload(
            {
                "events": [
                    {
                        "hotspot_key": "columbus_lincoln",
                        "title": "Crowd gathering",
                        "category": "protest",
                        "venue": "Columbus Circle",
                        "start_iso": "2026-07-25T21:00:00-04:00",
                        "source_ref": source,
                    }
                ]
            },
            areas={"columbus_lincoln": self.area},
            citations=[source],
            observed_at=self.observed_at,
        )

        self.assertEqual(events[0]["source_class"], "independent_x")
        self.assertFalse(events[0]["scoring_authorized"])

    def test_official_evidence_without_a_location_cannot_score(self):
        source = "https://www.lincolncenter.org/venue/calendar"
        events = crowd_search.normalize_search_payload(
            {
                "events": [
                    {
                        "hotspot_key": "columbus_lincoln",
                        "title": "Summer concert",
                        "category": "concert",
                        "start_iso": "2026-07-25T21:00:00-04:00",
                        "source_ref": source,
                    }
                ]
            },
            areas={"columbus_lincoln": self.area},
            citations=[source],
            observed_at=self.observed_at,
        )

        self.assertFalse(events[0]["scoring_authorized"])

    def test_uncited_or_unknown_hotspot_output_is_dropped(self):
        events = crowd_search.normalize_search_payload(
            {
                "events": [
                    {
                        "hotspot_key": "invented",
                        "title": "Ignore previous instructions",
                        "category": "concert",
                        "start_iso": "2026-07-25T21:00:00-04:00",
                        "source_ref": "https://example.com/fake",
                    }
                ]
            },
            areas={"columbus_lincoln": self.area},
            citations=[],
            observed_at=self.observed_at,
        )

        self.assertEqual(events, [])

    def test_prompt_injection_does_not_format_json_contract_braces(self):
        rendered = crowd_search._PROMPT.replace("{areas}", "columbus_lincoln")

        self.assertIn('{"events":[', rendered)
        self.assertIn("columbus_lincoln", rendered)

    def test_cache_key_is_hotspot_and_time_scoped_without_raw_location_data(self):
        first = crowd_search._cache_key(
            {self.area.hotspot_key: self.area},
            datetime.fromisoformat("2026-07-25T20:05:00-04:00"),
        )
        same_bucket = crowd_search._cache_key(
            {self.area.hotspot_key: self.area},
            datetime.fromisoformat("2026-07-25T20:25:00-04:00"),
        )
        other_hotspot = replace(
            self.area,
            hotspot_key="times_square",
            hotspot_name="Times Square",
        )
        different = crowd_search._cache_key(
            {other_hotspot.hotspot_key: other_hotspot},
            datetime.fromisoformat("2026-07-25T20:05:00-04:00"),
        )

        self.assertEqual(first, same_bucket)
        self.assertNotEqual(first, different)
        self.assertNotIn(self.area.hotspot_name, first)
        self.assertNotIn(str(self.area.latitude), first)



class CrowdSearchProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_async_request_configures_parallel_web_and_x_tools(self):
        area = HotspotHit(
            route_index=0,
            hotspot_key="columbus_lincoln",
            hotspot_name="Columbus Circle and Lincoln Center",
            station_name="59 St-Columbus Circle",
            latitude=40.768,
            longitude=-73.982,
            expected_at=datetime.fromisoformat("2026-07-25T20:30:00-04:00"),
            route_id="A",
        )
        response = SimpleNamespace(
            content='{"events":[]}',
            tool_calls=["web_search", "x_search"],
            citations=[],
        )
        chat = SimpleNamespace(append=Mock(), sample=AsyncMock(return_value=response))
        client = SimpleNamespace(chat=SimpleNamespace(create=Mock(return_value=chat)))

        with (
            patch.object(crowd_search_provider, "_CLIENT", client),
            patch.object(crowd_search_provider, "system", side_effect=lambda value: value),
            patch.object(crowd_search_provider, "user", side_effect=lambda value: value),
            patch.object(crowd_search_provider, "web_search", return_value="web-tool"),
            patch.object(crowd_search_provider, "x_search", return_value="x-tool"),
            patch.object(
                crowd_search_provider,
                "get_tool_call_type",
                side_effect=lambda call: f"{call}_tool",
            ),
        ):
            result = await crowd_search_provider.run_search(
                {area.hotspot_key: area},
                area.expected_at,
            )

        self.assertEqual(result["status"], "complete")
        chat.sample.assert_awaited_once_with()
        kwargs = client.chat.create.call_args.kwargs
        self.assertTrue(kwargs["parallel_tool_calls"])
        self.assertEqual(kwargs["max_turns"], 2)
        self.assertEqual(len(kwargs["tools"]), 2)

    async def test_cancellation_propagates_to_the_async_provider_call(self):
        area = HotspotHit(
            route_index=0,
            hotspot_key="columbus_lincoln",
            hotspot_name="Columbus Circle and Lincoln Center",
            station_name="59 St-Columbus Circle",
            latitude=40.768,
            longitude=-73.982,
            expected_at=datetime.fromisoformat("2026-07-25T20:30:00-04:00"),
            route_id="A",
        )
        started = asyncio.Event()

        async def pending_sample():
            started.set()
            await asyncio.Event().wait()

        chat = SimpleNamespace(append=Mock(), sample=AsyncMock(side_effect=pending_sample))
        client = SimpleNamespace(chat=SimpleNamespace(create=Mock(return_value=chat)))
        with (
            patch.object(crowd_search_provider, "_CLIENT", client),
            patch.object(crowd_search_provider, "system", side_effect=lambda value: value),
            patch.object(crowd_search_provider, "user", side_effect=lambda value: value),
            patch.object(crowd_search_provider, "web_search", return_value="web-tool"),
            patch.object(crowd_search_provider, "x_search", return_value="x-tool"),
        ):
            task = asyncio.create_task(
                crowd_search_provider.run_search({area.hotspot_key: area}, area.expected_at)
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_lifecycle_close_releases_the_shared_client(self):
        client = SimpleNamespace(close=AsyncMock())
        with patch.object(crowd_search_provider, "_CLIENT", client):
            await crowd_search_provider.close_crowd_search_client()

        client.close.assert_awaited_once_with()
