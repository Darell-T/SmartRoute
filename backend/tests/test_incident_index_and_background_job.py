"""Tests for the unflagged prepare_route_options / present_route path."""
from __future__ import annotations
import unittest
from unittest.mock import AsyncMock



class IncidentIndexTests(unittest.TestCase):
    def test_lookup_and_coverage(self):
        from app.services.incidents import index as incident_index

        incident_id = incident_index.upsert_incident(
            {
                "state": "confirmed",
                "location_name": "Atlantic Av",
                "affected_route_ids": ["Q", "B"],
                "affected_stop_ids": ["D24"],
                "description": "Entrance restriction",
                "advisor_eligible": True,
                "source_coverage": ["x_search"],
                "corroboration_state": "confirmed",
            }
        )
        self.assertTrue(incident_id.startswith("inc_"))
        found = incident_index.lookup_incidents(route_ids=["Q"])
        self.assertGreaterEqual(len(found["incidents"]), 1)
        incident_index.set_coverage(
            {
                "coverage_id": "downtown-northwest-brooklyn",
                "coverage_status": "current",
                "incidents_found": 1,
            }
        )
        coverage = incident_index.get_coverage("downtown-northwest-brooklyn")
        self.assertEqual(coverage["coverage_status"], "current")


class BackgroundJobTests(unittest.IsolatedAsyncioTestCase):
    """Service-job surface with every official/scout boundary injected.

    The service job has no feature-flag gate: the replacement run test injects
    all provider boundaries so it makes zero provider/model calls, and every
    test releases the job lock in finally so no lock can leak.
    """

    def setUp(self):
        from app.services import cache

        self._original_client = cache.redis_client
        cache.redis_client = None
        cache._mem.clear()

    def tearDown(self):
        from app.services import cache

        cache.redis_client = self._original_client
        cache._mem.clear()

    async def test_job_runs_end_to_end_with_injected_boundaries(self):
        from app.services.incidents import refresh
        from app.services.incidents.scout import ScoutBatchResult
        from app.services.incidents.batches import INCIDENT_BATCHES
        from app.services.incidents.official import (
            SOURCE_ALERTS,
            SOURCE_GTFS_RT,
            STATUS_CURRENT,
            OfficialIncidentSnapshot,
        )
        from app.services import cache

        collector = AsyncMock(
            return_value=OfficialIncidentSnapshot(
                incidents=(),
                source_status={SOURCE_ALERTS: STATUS_CURRENT, SOURCE_GTFS_RT: STATUS_CURRENT},
                attempted_at="2026-08-08T12:00:00Z",
            )
        )

        async def scout(batch):
            return ScoutBatchResult(
                batch_id=batch.batch_id,
                incidents=(),
                attempted_at="2026-08-08T12:00:05Z",
                x_status="complete",
                web_status="not_triggered",
                model_calls=0,
            )

        metrics = await refresh.run_background_incident_refresh(
            collect_official=collector,
            scout_batch=scout,
            clock=lambda: 1_800_000_000.0,
            monotonic=lambda: 0.0,
        )
        self.assertEqual(metrics["status"], "complete")
        self.assertEqual(
            metrics["coverage"],
            {"current": len(INCIDENT_BATCHES), "partial": 0, "unavailable": 0},
        )
        self.assertEqual(metrics["incidents_upserted"], 0)
        self.assertEqual(metrics["official_incidents"], 0)
        self.assertEqual(metrics["model_calls"], 0)
        self.assertNotIn("incidents", metrics)
        collector.assert_awaited_once()  # one injected official boundary call only.
        self.assertIsNone(cache.cache_get(refresh.JOB_LOCK_KEY))

    async def test_job_lock_prevents_overlap_and_releases_in_finally(self):
        from app.services.incidents import refresh
        from app.services import cache

        token = refresh.acquire_job_lock()
        self.assertIsNotNone(token)
        try:
            self.assertFalse(refresh.acquire_job_lock())
        finally:
            self.assertTrue(refresh.release_job_lock(token))
        self.assertIsNone(cache.cache_get(refresh.JOB_LOCK_KEY))
