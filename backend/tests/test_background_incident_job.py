"""Focused tests for the thirty-minute background incident refresh job.

All tests inject the official collector, the batch scout, and clocks, so they
make zero network, provider, or model calls. Coverage truth is asserted from
the cache-backed incident index, never from incident counts.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.services.incidents import refresh as job
from app.services.incidents import index as incident_index
from app.services.incidents.scout import (
    ScoutBatchResult,
    scout_incident_batch as real_scout_incident_batch,
)
from app.services.incidents.batches import INCIDENT_BATCHES, incident_batch_ids
from app.services.incidents.official import (
    SOURCE_ALERTS,
    SOURCE_GTFS_RT,
    STATUS_CURRENT,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    OfficialIncidentSnapshot,
    collect_official_incidents as real_collect_official_incidents,
)
from app.services import cache

FIXED_NOW = 1_800_000_000.0
SCOUT_AT = "2027-01-15T08:00:05Z"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _snapshot(
    *,
    incidents=(),
    alerts=STATUS_CURRENT,
    gtfs=STATUS_CURRENT,
    attempted_at=_iso(FIXED_NOW),
) -> OfficialIncidentSnapshot:
    return OfficialIncidentSnapshot(
        incidents=tuple(incidents),
        source_status={SOURCE_ALERTS: alerts, SOURCE_GTFS_RT: gtfs},
        attempted_at=attempted_at,
    )


def _scout_result(
    batch_id: str,
    *,
    incidents=(),
    x_status="complete",
    web_status="not_triggered",
    attempted_at=SCOUT_AT,
    model_calls=1,
) -> ScoutBatchResult:
    return ScoutBatchResult(
        batch_id=batch_id,
        incidents=tuple(incidents),
        attempted_at=attempted_at,
        x_status=x_status,
        web_status=web_status,
        model_calls=model_calls,
    )


async def _default_scout(batch) -> ScoutBatchResult:
    return _scout_result(batch.batch_id)


class JobAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    """Isolated in-memory cache so no test touches Redis or the network."""

    def setUp(self):
        self._original_client = cache.redis_client
        cache.redis_client = None
        cache._mem.clear()

    def tearDown(self):
        cache.redis_client = self._original_client
        cache._mem.clear()


class LockTests(unittest.TestCase):
    def setUp(self):
        self._original_client = cache.redis_client
        cache.redis_client = None
        cache._mem.clear()

    def tearDown(self):
        cache.redis_client = self._original_client
        cache._mem.clear()

    def test_atomic_acquisition_and_owner_only_release(self):
        token_a = job.acquire_job_lock()
        self.assertIsInstance(token_a, str)
        self.assertTrue(token_a)
        self.assertIsNone(job.acquire_job_lock())  # overlapping acquisition skipped.
        self.assertFalse(job.release_job_lock("wrong-token"))
        self.assertEqual(cache.cache_get(job.JOB_LOCK_KEY), token_a)
        self.assertTrue(job.release_job_lock(token_a))
        self.assertIsNone(cache.cache_get(job.JOB_LOCK_KEY))
        token_b = job.acquire_job_lock()
        self.assertIsNotNone(token_b)
        self.assertNotEqual(token_a, token_b)
        job.release_job_lock(token_b)

    def test_release_without_token_is_a_safe_noop(self):
        self.assertFalse(job.release_job_lock())

    def test_lock_ttl_stays_below_thirty_minutes(self):
        self.assertLess(job.JOB_LOCK_TTL_S, 30 * 60)


class RunTests(JobAsyncTestCase):
    async def run_job(self, *, collector=None, scout=None, **kwargs):
        return await job.run_background_incident_refresh(
            collect_official=collector or AsyncMock(return_value=_snapshot()),
            scout_batch=scout or _default_scout,
            clock=lambda: FIXED_NOW,
            monotonic=lambda: 0.0,
            **kwargs,
        )

    async def test_overlapping_run_is_skipped_without_touching_boundaries(self):
        token = job.acquire_job_lock()
        collector = AsyncMock()
        scout = AsyncMock()
        metrics = await self.run_job(collector=collector, scout=scout)
        self.assertEqual(metrics["status"], "lock_held")
        collector.assert_not_awaited()
        scout.assert_not_awaited()
        self.assertEqual(cache.cache_get(job.JOB_LOCK_KEY), token)
        self.assertTrue(job.release_job_lock(token))
        stored = job.last_job_metrics()
        self.assertEqual(stored["status"], "lock_held")
        self.assertEqual(stored["official_incidents"], 0)
        self.assertNotIn("incidents", stored)

    async def test_official_collected_once_and_all_ten_batches_scanned(self):
        collector = AsyncMock(return_value=_snapshot())
        scout = AsyncMock(side_effect=_default_scout)
        metrics = await self.run_job(collector=collector, scout=scout)
        collector.assert_awaited_once()
        self.assertEqual(scout.await_count, 10)
        called_ids = [call.args[0].batch_id for call in scout.await_args_list]
        self.assertEqual(sorted(called_ids), sorted(incident_batch_ids()))
        self.assertEqual(len(set(called_ids)), 10)
        self.assertEqual(metrics["batches"], list(incident_batch_ids()))
        self.assertEqual(metrics["status"], "complete")

    async def test_scout_concurrency_never_exceeds_bound(self):
        class Probe:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.guard = asyncio.Lock()

            async def __call__(self, batch):
                async with self.guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.02)
                async with self.guard:
                    self.active -= 1
                return _scout_result(batch.batch_id)

        probe = Probe()
        await self.run_job(scout=probe)
        self.assertLessEqual(probe.max_active, job.SCOUT_CONCURRENCY)
        self.assertEqual(probe.max_active, job.SCOUT_CONCURRENCY)

    async def test_all_current_empty_scans_are_current_without_incident_claims(self):
        metrics = await self.run_job()
        self.assertEqual(metrics["status"], "complete")
        self.assertEqual(metrics["coverage"], {"current": 10, "partial": 0, "unavailable": 0})
        self.assertEqual(metrics["incidents_upserted"], 0)
        self.assertEqual(metrics["model_calls"], 10)
        for batch in INCIDENT_BATCHES:
            coverage = incident_index.get_coverage(batch.batch_id)
            self.assertEqual(coverage["coverage_status"], "current")
            self.assertEqual(coverage["incidents_found"], 0)
            self.assertEqual(coverage["x_status"], "complete")
            self.assertEqual(coverage["web_status"], "not_triggered")
            self.assertEqual(coverage["last_attempted_at"], _iso(FIXED_NOW))
            self.assertEqual(coverage["last_successful_x_scan_at"], SCOUT_AT)
            self.assertEqual(coverage["last_official_refresh_at"], _iso(FIXED_NOW))
        self.assertIsNone(cache.cache_get(job.JOB_LOCK_KEY))

    async def test_truthful_partial_and_unavailable_coverage(self):
        scenarios = [
            # (alerts, gtfs, x_status, web_status, expected coverage)
            (STATUS_UNAVAILABLE, STATUS_UNAVAILABLE, "unavailable", "not_triggered", "unavailable"),
            (STATUS_CURRENT, STATUS_PARTIAL, "complete", "not_triggered", "partial"),
            (STATUS_CURRENT, STATUS_CURRENT, "partial", "not_triggered", "partial"),
            (STATUS_CURRENT, STATUS_CURRENT, "complete", "partial", "partial"),
            (STATUS_CURRENT, STATUS_CURRENT, "complete", "unavailable", "partial"),
            (STATUS_UNAVAILABLE, STATUS_UNAVAILABLE, "complete", "not_triggered", "partial"),
            (STATUS_UNAVAILABLE, STATUS_CURRENT, "unavailable", "not_triggered", "partial"),
        ]
        for index, (alerts, gtfs, x_status, web_status, expected) in enumerate(scenarios):
            with self.subTest(index=index):
                async def scout(batch, x=x_status, web=web_status):
                    return _scout_result(batch.batch_id, x_status=x, web_status=web)

                metrics = await self.run_job(
                    collector=AsyncMock(return_value=_snapshot(alerts=alerts, gtfs=gtfs)),
                    scout=scout,
                )
                self.assertEqual(metrics["status"], "partial")
                self.assertEqual(metrics["coverage"]["current"], 0)
                self.assertEqual(metrics["coverage"][expected], 10)
                coverage = incident_index.get_coverage("upper-manhattan")
                self.assertEqual(coverage["coverage_status"], expected)
                self.assertEqual(coverage["x_status"], x_status)
                self.assertEqual(coverage["web_status"], web_status)
                self.assertEqual(
                    coverage["last_successful_x_scan_at"], SCOUT_AT if x_status == "complete" else None
                )
                official_usable = alerts in (STATUS_CURRENT, STATUS_PARTIAL) or gtfs in (
                    STATUS_CURRENT,
                    STATUS_PARTIAL,
                )
                self.assertEqual(
                    coverage["last_official_refresh_at"],
                    _iso(FIXED_NOW) if official_usable else None,
                )

    async def test_x_timeout_is_unavailable_and_truthful(self):
        async def slow(batch):
            await asyncio.sleep(0.2)
            return _scout_result(batch.batch_id)

        with patch.object(job, "SCOUT_BATCH_TIMEOUT_S", 0.02):
            metrics = await self.run_job(
                collector=AsyncMock(
                    return_value=_snapshot(alerts=STATUS_UNAVAILABLE, gtfs=STATUS_UNAVAILABLE)
                ),
                scout=slow,
            )
        self.assertEqual(metrics["status"], "partial")
        self.assertEqual(metrics["scout_timeouts"], 10)
        self.assertEqual(metrics["model_calls"], 0)
        coverage = incident_index.get_coverage("lower-manhattan")
        self.assertEqual(coverage["coverage_status"], "unavailable")
        self.assertEqual(coverage["x_status"], "unavailable")
        self.assertIsNone(coverage["last_successful_x_scan_at"])
        self.assertIsNone(coverage["last_official_refresh_at"])

    async def test_wrong_returned_batch_id_degrades_to_canonical_unavailable(self):
        async def scout(batch):
            if batch.batch_id == "upper-manhattan":
                return _scout_result("midtown-manhattan")  # wrong canonical id
            return _scout_result(batch.batch_id)

        metrics = await self.run_job(
            collector=AsyncMock(
                return_value=_snapshot(
                    alerts=STATUS_UNAVAILABLE, gtfs=STATUS_UNAVAILABLE
                )
            ),
            scout=scout,
        )
        self.assertEqual(metrics["batches"], list(incident_batch_ids()))
        self.assertEqual(metrics["coverage"], {"current": 0, "partial": 9, "unavailable": 1})
        upper = incident_index.get_coverage("upper-manhattan")
        self.assertEqual(upper["coverage_status"], "unavailable")
        self.assertEqual(upper["x_status"], "unavailable")
        self.assertEqual(upper["incidents_found"], 0)
        # The canonical midtown record stays midtown's own scout result.
        midtown = incident_index.get_coverage("midtown-manhattan")
        self.assertEqual(midtown["coverage_status"], "partial")
        self.assertEqual(midtown["x_status"], "complete")

    async def test_empty_and_noncanonical_returned_ids_write_no_stray_coverage(self):
        async def scout(batch):
            if batch.batch_id == "upper-manhattan":
                return _scout_result("")
            if batch.batch_id == "lower-manhattan":
                return _scout_result("not-a-batch")
            return _scout_result(batch.batch_id)

        metrics = await self.run_job(
            collector=AsyncMock(
                return_value=_snapshot(
                    alerts=STATUS_UNAVAILABLE, gtfs=STATUS_UNAVAILABLE
                )
            ),
            scout=scout,
        )
        self.assertEqual(metrics["batches"], list(incident_batch_ids()))
        self.assertEqual(metrics["coverage"], {"current": 0, "partial": 8, "unavailable": 2})
        for batch in INCIDENT_BATCHES:
            coverage = incident_index.get_coverage(batch.batch_id)
            self.assertIsNotNone(coverage)
            self.assertEqual(coverage["coverage_id"], batch.batch_id)
            if batch.batch_id in ("upper-manhattan", "lower-manhattan"):
                self.assertEqual(coverage["coverage_status"], "unavailable")
            else:
                self.assertEqual(coverage["coverage_status"], "partial")
        # Exactly one coverage record per canonical batch: no stray key exists.
        self.assertIsNone(
            cache.cache_get(f"{incident_index.COVERAGE_PREFIX}not-a-batch")
        )

    async def test_triggered_web_failure_keeps_incidents_and_marks_partial(self):
        incident = {
            "source": "x_search",
            "source_id": "w-1",
            "location_name": "Lexington Avenue",
            "affected_route_ids": ["Q"],
        }

        async def scout(batch):
            return _scout_result(
                batch.batch_id, incidents=(incident,), x_status="complete", web_status="partial"
            )

        metrics = await self.run_job(scout=scout)
        self.assertEqual(metrics["status"], "partial")
        self.assertEqual(metrics["incidents_upserted"], 10)
        found = incident_index.lookup_incidents(route_ids=["Q"])
        self.assertEqual(len(found["incidents"]), 1)
        coverage = incident_index.get_coverage("midtown-manhattan")
        self.assertEqual(coverage["coverage_status"], "partial")
        self.assertEqual(coverage["incidents_found"], 1)
        self.assertEqual(metrics["official_incidents"], 0)

    async def test_incidents_upserted_and_duplicate_identities_dedupe(self):
        official_a = {
            "source": "mta_alerts",
            "source_id": "a1",
            "location_name": "Lexington",
            "affected_route_ids": ["Q"],
        }
        official_b = {
            "source": "mta_gtfs_rt",
            "source_id": "g1",
            "location_name": "Grand Central",
            "affected_stop_ids": ["D1"],
        }
        duplicate = dict(official_a)

        async def scout(batch):
            if batch.batch_id == "upper-manhattan":
                return _scout_result(batch.batch_id, incidents=(duplicate,))
            return _scout_result(batch.batch_id)

        metrics = await self.run_job(
            collector=AsyncMock(return_value=_snapshot(incidents=(official_a, official_b))),
            scout=scout,
        )
        self.assertEqual(metrics["incidents_upserted"], 3)
        self.assertEqual(metrics["unique_incident_ids"], 2)
        self.assertEqual(metrics["official_incidents"], 2)
        found = incident_index.lookup_incidents(route_ids=["Q"])
        self.assertEqual(len(found["incidents"]), 1)
        self.assertEqual(
            found["incidents"][0]["incident_id"], incident_index.incident_id_for(official_a)
        )
        # Per-batch incidents_found counts only that batch scout's incidents;
        # the citywide official snapshot is never added to any batch.
        self.assertEqual(incident_index.get_coverage("upper-manhattan")["incidents_found"], 1)
        self.assertEqual(incident_index.get_coverage("midtown-manhattan")["incidents_found"], 0)

    async def test_collector_failure_is_unavailable_not_abort(self):
        async def broken():
            raise RuntimeError("provider down")

        metrics = await self.run_job(collector=broken)
        self.assertEqual(metrics["status"], "partial")
        self.assertEqual(
            metrics["official_sources"],
            {SOURCE_ALERTS: "unavailable", SOURCE_GTFS_RT: "unavailable"},
        )
        self.assertEqual(metrics["official_incidents"], 0)
        coverage = incident_index.get_coverage("upper-manhattan")
        self.assertEqual(coverage["coverage_status"], "partial")  # X usable, official not.
        self.assertIsNone(coverage["last_official_refresh_at"])
        self.assertIsNone(cache.cache_get(job.JOB_LOCK_KEY))

    async def test_cancellation_releases_lock_and_records_bounded_metrics(self):
        async def slow(batch):
            await asyncio.sleep(60)
            return _scout_result(batch.batch_id)

        task = asyncio.create_task(self.run_job(scout=slow))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(cache.cache_get(job.JOB_LOCK_KEY))
        stored = job.last_job_metrics()
        self.assertEqual(stored["status"], "failed")
        self.assertNotIn("incidents", stored)
        self.assertNotIn("payload", json.dumps(stored))

    async def test_unexpected_failure_marks_failed_and_releases_lock(self):
        with patch.object(
            incident_index, "upsert_incident", side_effect=RuntimeError("cache down")
        ):
            metrics = await self.run_job(
                collector=AsyncMock(
                    return_value=_snapshot(incidents=({"source": "s", "source_id": "x"},))
                )
            )
        self.assertEqual(metrics["status"], "failed")
        self.assertEqual(metrics["error"], "RuntimeError")
        self.assertIsNone(cache.cache_get(job.JOB_LOCK_KEY))
        stored = job.last_job_metrics()
        self.assertEqual(stored["status"], "failed")
        self.assertNotIn("incidents", stored)

    async def test_metrics_stay_bounded_and_payload_free(self):
        incident = {
            "source": "x_search",
            "source_id": "m-1",
            "location_name": "Union Square",
            "description": "signal issue",
            "affected_route_ids": ["Q"],
        }

        async def scout(batch):
            return _scout_result(batch.batch_id, incidents=(incident,))

        metrics = await self.run_job(
            collector=AsyncMock(return_value=_snapshot(incidents=(incident,))),
            scout=scout,
        )
        stored = job.last_job_metrics()
        self.assertEqual(stored["status"], "complete")
        self.assertEqual(stored["coverage"]["current"], 10)
        self.assertEqual(stored["incidents_upserted"], 11)
        self.assertEqual(stored["official_incidents"], 1)
        self.assertEqual(stored["unique_incident_ids"], 1)
        self.assertEqual(stored["model_calls"], 10)
        for batch in INCIDENT_BATCHES:
            self.assertEqual(incident_index.get_coverage(batch.batch_id)["incidents_found"], 1)
        self.assertNotIn("incidents", stored)
        self.assertNotIn("source_records", stored)
        self.assertNotIn("payload", json.dumps(stored))
        self.assertIsInstance(metrics["duration_ms"], (int, float))


class SourcePurityTests(unittest.TestCase):
    def test_no_feature_flags_or_ny511_references(self):
        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app"
            / "services"
            / "incidents"
            / "refresh.py"
        )
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("feature_flags", text)
        self.assertNotIn("ny511", text)
        self.assertNotIn("511", text)

    def test_public_seams_point_at_production_boundaries(self):
        self.assertIs(job.collect_official_incidents, real_collect_official_incidents)
        self.assertIs(job.scout_incident_batch, real_scout_incident_batch)
        self.assertEqual(job.SCOUT_CONCURRENCY, 2)
        self.assertEqual(len(job.INCIDENT_BATCHES), 10)


if __name__ == "__main__":
    unittest.main()
