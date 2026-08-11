from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.services.trips import crowd_evidence
from app.services.trips.crowd_hotspots import HotspotHit


def _route() -> list[dict]:
    return [
        {
            "type": "SUBWAY",
            "route_id": "Q",
            "departure_stop": "Church Av",
            "arrival_stop": "57 St-7 Av",
            "departure_coords": {"latitude": 40.65, "longitude": -73.96},
            "arrival_coords": {"latitude": 40.765, "longitude": -73.98},
            "departure_time_iso": "2026-07-25T20:00:00-04:00",
            "arrival_time_iso": "2026-07-25T20:40:00-04:00",
        }
    ]


def _hit() -> HotspotHit:
    return HotspotHit(
        route_index=0,
        hotspot_key="columbus_lincoln",
        hotspot_name="Columbus Circle and Lincoln Center",
        station_name="57 St-7 Av",
        latitude=40.765,
        longitude=-73.98,
        expected_at=datetime.fromisoformat("2026-07-25T20:40:00-04:00"),
        route_id="Q",
    )


class _Ctx:
    now_et = "2026-07-25T19:30:00-04:00"


class CrowdEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_cross_provider_duplicates_collapse_by_venue_and_time(self):
        impacts = crowd_evidence._deduplicate_impacts(
            [
                {
                    "event_id": "ticketmaster:1",
                    "route_index": 0,
                    "title": "Summer Concert",
                    "venue": "Lincoln Center",
                    "window_start_iso": "2026-07-25T21:00:00-04:00",
                    "confidence": 0.9,
                    "risk_score": 7.0,
                },
                {
                    "event_id": "grok:2",
                    "route_index": 0,
                    "title": "Free concert tonight",
                    "venue": "Lincoln Center!",
                    "window_start_iso": "2026-07-25T21:00:00-04:00",
                    "confidence": 0.75,
                    "risk_score": 5.0,
                },
            ]
        )

        self.assertEqual(len(impacts), 1)
        self.assertEqual(impacts[0]["event_id"], "ticketmaster:1")

    async def test_partial_grok_coverage_never_becomes_an_all_clear(self):
        with (
            patch.object(
                crowd_evidence.event_crowd,
                "collect_route_event_evidence",
                new=AsyncMock(return_value=("no_relevant_events", [], [])),
            ),
            patch.object(
                crowd_evidence.crowd_search,
                "search_hotspots",
                new=AsyncMock(
                    return_value={
                        "status": "partial",
                        "events": [],
                        "completed_sources": ["web_search"],
                    }
                ),
            ),
        ):
            status, impacts, failures, metadata = await crowd_evidence.collect(
                [_route()],
                _Ctx(),
                hotspot_hits=[_hit()],
                explicit_crowd_request=True,
                allow_live_search=True,
            )

        self.assertEqual(status, "partial")
        self.assertEqual(impacts, [])
        self.assertIn("grok_partial", failures)
        self.assertEqual(metadata["completed_sources"], ["web_search"])

    async def test_grok_timeout_keeps_ticketmaster_result_and_marks_partial(self):
        async def slow_search(*_args, **_kwargs):
            await asyncio.sleep(0.1)
            return {"status": "complete", "events": []}

        with (
            patch.object(
                crowd_evidence.event_crowd,
                "collect_route_event_evidence",
                new=AsyncMock(return_value=("no_relevant_events", [], [])),
            ),
            patch.object(
                crowd_evidence.crowd_search,
                "search_hotspots",
                new=slow_search,
            ),
            patch.object(crowd_evidence, "_LIVE_SEARCH_DEADLINE_S", 0.01),
        ):
            status, impacts, failures, _metadata = await crowd_evidence.collect(
                [_route()],
                _Ctx(),
                hotspot_hits=[_hit()],
                explicit_crowd_request=True,
                allow_live_search=True,
            )

        self.assertEqual(status, "partial")
        self.assertEqual(impacts, [])
        self.assertIn("grok_partial", failures)

    async def test_one_deadline_also_bounds_ticketmaster(self):
        async def slow_ticketmaster(*_args, **_kwargs):
            await asyncio.sleep(0.1)
            return "no_relevant_events", [], []

        with (
            patch.object(
                crowd_evidence.event_crowd,
                "collect_route_event_evidence",
                new=slow_ticketmaster,
            ),
            patch.object(
                crowd_evidence.crowd_search,
                "search_hotspots",
                new=AsyncMock(
                    return_value={
                        "status": "complete",
                        "events": [],
                        "completed_sources": ["web_search", "x_search"],
                    }
                ),
            ),
            patch.object(crowd_evidence, "_LIVE_SEARCH_DEADLINE_S", 0.01),
        ):
            status, impacts, failures, _metadata = await crowd_evidence.collect(
                [_route()],
                _Ctx(),
                hotspot_hits=[_hit()],
                explicit_crowd_request=True,
                allow_live_search=True,
            )

        self.assertEqual(status, "partial")
        self.assertEqual(impacts, [])
        self.assertIn("TimeoutError", failures)

    async def test_quick_incidental_hotspot_does_not_require_live_grok(self):
        search = AsyncMock(
            return_value={
                "status": "not_required",
                "events": [],
                "completed_sources": [],
            }
        )
        with (
            patch.object(
                crowd_evidence.event_crowd,
                "collect_route_event_evidence",
                new=AsyncMock(return_value=("no_relevant_events", [], [])),
            ),
            patch.object(crowd_evidence.crowd_search, "search_hotspots", new=search),
        ):
            status, _impacts, failures, _metadata = await crowd_evidence.collect(
                [_route()],
                _Ctx(),
                hotspot_hits=[_hit()],
                explicit_crowd_request=False,
                allow_live_search=False,
            )

        self.assertEqual(status, "no_relevant_events")
        self.assertEqual(failures, [])
        self.assertFalse(search.await_args.kwargs["allow_live_search"])

    async def test_caller_cancellation_cancels_and_drains_both_provider_tasks(self):
        ticketmaster_started = asyncio.Event()
        ticketmaster_cleaned_up = asyncio.Event()
        grok_started = asyncio.Event()
        grok_cleaned_up = asyncio.Event()

        async def blocking_ticketmaster(*_args, **_kwargs):
            ticketmaster_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                ticketmaster_cleaned_up.set()

        async def blocking_grok(*_args, **_kwargs):
            grok_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                grok_cleaned_up.set()

        with (
            patch.object(
                crowd_evidence.event_crowd,
                "collect_route_event_evidence",
                new=blocking_ticketmaster,
            ),
            patch.object(
                crowd_evidence.crowd_search,
                "search_hotspots",
                new=blocking_grok,
            ),
            # The shared deadline must not fire while the test cancels.
            patch.object(crowd_evidence, "_LIVE_SEARCH_DEADLINE_S", 60.0),
        ):
            collect_task = asyncio.create_task(
                crowd_evidence.collect(
                    [_route()],
                    _Ctx(),
                    hotspot_hits=[_hit()],
                    explicit_crowd_request=True,
                    allow_live_search=True,
                )
            )
            await ticketmaster_started.wait()
            await grok_started.wait()
            collect_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await collect_task

        self.assertTrue(collect_task.cancelled())
        self.assertTrue(ticketmaster_cleaned_up.is_set())
        self.assertTrue(grok_cleaned_up.is_set())
