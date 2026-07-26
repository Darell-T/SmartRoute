from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime
from threading import Event
from unittest.mock import patch

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

    def test_web_and_x_provider_searches_start_concurrently(self):
        both_started = Event()
        started: list[str] = []

        def fake_source_search(**kwargs):
            started.append(kwargs["source_name"])
            if len(started) == 2:
                both_started.set()
            self.assertTrue(both_started.wait(0.5))
            return {
                "status": "complete",
                "events": [],
                "completed_sources": [kwargs["source_name"]],
            }

        with (
            patch.object(crowd_search_provider, "_CLIENT", object()),
            patch.object(crowd_search_provider, "system", object()),
            patch.object(crowd_search_provider, "user", object()),
            patch.object(crowd_search_provider, "get_tool_call_type", object()),
            patch.object(crowd_search_provider, "web_search", return_value="web"),
            patch.object(crowd_search_provider, "x_search", return_value="x"),
            patch.object(
                crowd_search_provider,
                "_run_source_search",
                side_effect=fake_source_search,
            ),
        ):
            result = crowd_search_provider.run_search(
                {self.area.hotspot_key: self.area},
                self.area.expected_at,
            )

        self.assertEqual(sorted(started), ["web_search", "x_search"])
        self.assertEqual(result["status"], "complete")
