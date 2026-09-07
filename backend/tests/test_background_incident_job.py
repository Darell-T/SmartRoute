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
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from app.services import cache
from app.services.incidents import index as incident_index
from app.services.incidents import refresh as job
from app.services.incidents.batches import INCIDENT_BATCHES, incident_batch_ids
from app.services.incidents.official import (
    SOURCE_ALERTS,
    SOURCE_GTFS_RT,
    STATUS_CURRENT,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    OfficialIncidentSnapshot,
)
from app.services.incidents.official import (
    collect_official_incidents as real_collect_official_incidents,
)
from app.services.incidents.scout import (
    ScoutBatchResult,
)
from app.services.incidents.scout import (
    scout_incident_batch as real_scout_incident_batch,
)

FIXED_NOW = 1_800_000_000.0
SCOUT_AT = "2027-01-15T08:00:05Z"
PROVIDER_DOWN_MESSAGE = "provider down"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


FIXED_NOW_ISO = _iso(FIXED_NOW)


def _snapshot(
    *,
    incidents=(),
    alerts=STATUS_CURRENT,
    gtfs=STATUS_CURRENT,
    attempted_at=FIXED_NOW_ISO,
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
        assert isinstance(token_a, str)
        assert token_a
        assert job.acquire_job_lock() is None  # overlapping acquisition skipped.
        assert not job.release_job_lock("wrong-token")
        assert cache.cache_get(job.JOB_LOCK_KEY) == token_a
        assert job.release_job_lock(token_a)
        assert cache.cache_get(job.JOB_LOCK_KEY) is None
        token_b = job.acquire_job_lock()
        assert token_b is not None
        assert token_a != token_b
        job.release_job_lock(token_b)

    def test_release_without_token_is_a_safe_noop(self):
        assert not job.release_job_lock()

    def test_lock_ttl_stays_below_thirty_minutes(self):
        assert job.JOB_LOCK_TTL_S < 30 * 60


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
        assert metrics["status"] == "lock_held"
        collector.assert_not_awaited()
        scout.assert_not_awaited()
        assert cache.cache_get(job.JOB_LOCK_KEY) == token
        assert job.release_job_lock(token)
        stored = job.last_job_metrics()
        assert stored["status"] == "lock_held"
        assert stored["official_incidents"] == 0
        assert "incidents" not in stored

    async def test_official_collected_once_and_all_ten_batches_scanned(self):
        collector = AsyncMock(return_value=_snapshot())
        scout = AsyncMock(side_effect=_default_scout)
        metrics = await self.run_job(collector=collector, scout=scout)
        collector.assert_awaited_once()
        assert scout.await_count == 10
        called_ids = [call.args[0].batch_id for call in scout.await_args_list]
        assert sorted(called_ids) == sorted(incident_batch_ids())
        assert len(set(called_ids)) == 10
        assert metrics["batches"] == list(incident_batch_ids())
        assert metrics["status"] == "complete"

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
        assert probe.max_active <= job.SCOUT_CONCURRENCY
        assert probe.max_active == job.SCOUT_CONCURRENCY

    async def test_all_current_empty_scans_are_current_without_incident_claims(self):
        metrics = await self.run_job()
        assert metrics["status"] == "complete"
        assert metrics["coverage"] == {"current": 10, "partial": 0, "unavailable": 0}
        assert metrics["incidents_upserted"] == 0
        assert metrics["model_calls"] == 10
        for batch in INCIDENT_BATCHES:
            coverage = incident_index.get_coverage(batch.batch_id)
            assert coverage["coverage_status"] == "current"
            assert coverage["incidents_found"] == 0
            assert coverage["x_status"] == "complete"
            assert coverage["web_status"] == "not_triggered"
            assert coverage["last_attempted_at"] == _iso(FIXED_NOW)
            assert coverage["last_successful_x_scan_at"] == SCOUT_AT
            assert coverage["last_official_refresh_at"] == _iso(FIXED_NOW)
        assert cache.cache_get(job.JOB_LOCK_KEY) is None

    async def test_truthful_partial_and_unavailable_coverage(self):
        scenarios = [
            # (alerts, gtfs, x_status, web_status, expected coverage)
            (
                STATUS_UNAVAILABLE,
                STATUS_UNAVAILABLE,
                "unavailable",
                "not_triggered",
                "unavailable",
            ),
            (STATUS_CURRENT, STATUS_PARTIAL, "complete", "not_triggered", "partial"),
            (STATUS_CURRENT, STATUS_CURRENT, "partial", "not_triggered", "partial"),
            (STATUS_CURRENT, STATUS_CURRENT, "complete", "partial", "partial"),
            (STATUS_CURRENT, STATUS_CURRENT, "complete", "unavailable", "partial"),
            (
                STATUS_UNAVAILABLE,
                STATUS_UNAVAILABLE,
                "complete",
                "not_triggered",
                "partial",
            ),
            (
                STATUS_UNAVAILABLE,
                STATUS_CURRENT,
                "unavailable",
                "not_triggered",
                "partial",
            ),
        ]
        for index, (alerts, gtfs, x_status, web_status, expected) in enumerate(
            scenarios
        ):
            with self.subTest(index=index):

                async def scout(batch, x=x_status, web=web_status):
                    return _scout_result(batch.batch_id, x_status=x, web_status=web)

                metrics = await self.run_job(
                    collector=AsyncMock(
                        return_value=_snapshot(alerts=alerts, gtfs=gtfs)
                    ),
                    scout=scout,
                )
                assert metrics["status"] == "partial"
                assert metrics["coverage"]["current"] == 0
                assert metrics["coverage"][expected] == 10
                coverage = incident_index.get_coverage("upper-manhattan")
                assert coverage["coverage_status"] == expected
                assert coverage["x_status"] == x_status
                assert coverage["web_status"] == web_status
                assert coverage["last_successful_x_scan_at"] == (
                    SCOUT_AT if x_status == "complete" else None
                )
                official_usable = alerts in (
                    STATUS_CURRENT,
                    STATUS_PARTIAL,
                ) or gtfs in (
                    STATUS_CURRENT,
                    STATUS_PARTIAL,
                )
                assert coverage["last_official_refresh_at"] == (
                    _iso(FIXED_NOW) if official_usable else None
                )

    async def test_x_timeout_is_unavailable_and_truthful(self):
        async def slow(batch):
            await asyncio.sleep(0.2)
            return _scout_result(batch.batch_id)

        with patch.object(job, "SCOUT_BATCH_TIMEOUT_S", 0.02):
            metrics = await self.run_job(
                collector=AsyncMock(
                    return_value=_snapshot(
                        alerts=STATUS_UNAVAILABLE, gtfs=STATUS_UNAVAILABLE
                    )
                ),
                scout=slow,
            )
        assert metrics["status"] == "partial"
        assert metrics["scout_timeouts"] == 10
        assert metrics["model_calls"] == 0
        coverage = incident_index.get_coverage("lower-manhattan")
        assert coverage["coverage_status"] == "unavailable"
        assert coverage["x_status"] == "unavailable"
        assert coverage["last_successful_x_scan_at"] is None
        assert coverage["last_official_refresh_at"] is None

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
        assert metrics["batches"] == list(incident_batch_ids())
        assert metrics["coverage"] == {"current": 0, "partial": 9, "unavailable": 1}
        upper = incident_index.get_coverage("upper-manhattan")
        assert upper["coverage_status"] == "unavailable"
        assert upper["x_status"] == "unavailable"
        assert upper["incidents_found"] == 0
        # The canonical midtown record stays midtown's own scout result.
        midtown = incident_index.get_coverage("midtown-manhattan")
        assert midtown["coverage_status"] == "partial"
        assert midtown["x_status"] == "complete"

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
        assert metrics["batches"] == list(incident_batch_ids())
        assert metrics["coverage"] == {"current": 0, "partial": 8, "unavailable": 2}
        for batch in INCIDENT_BATCHES:
            coverage = incident_index.get_coverage(batch.batch_id)
            assert coverage is not None
            assert coverage["coverage_id"] == batch.batch_id
            if batch.batch_id in ("upper-manhattan", "lower-manhattan"):
                assert coverage["coverage_status"] == "unavailable"
            else:
                assert coverage["coverage_status"] == "partial"
        # Exactly one coverage record per canonical batch: no stray key exists.
        assert cache.cache_get(f"{incident_index.COVERAGE_PREFIX}not-a-batch") is None

    async def test_triggered_web_failure_keeps_incidents_and_marks_partial(self):
        incident = {
            "source": "x_search",
            "source_id": "w-1",
            "location_name": "Lexington Avenue",
            "affected_route_ids": ["Q"],
        }

        async def scout(batch):
            return _scout_result(
                batch.batch_id,
                incidents=(incident,),
                x_status="complete",
                web_status="partial",
            )

        metrics = await self.run_job(scout=scout)
        assert metrics["status"] == "partial"
        assert metrics["incidents_upserted"] == 10
        found = incident_index.lookup_incidents(route_ids=["Q"])
        assert len(found["incidents"]) == 1
        coverage = incident_index.get_coverage("midtown-manhattan")
        assert coverage["coverage_status"] == "partial"
        assert coverage["incidents_found"] == 1
        assert metrics["official_incidents"] == 0

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
            collector=AsyncMock(
                return_value=_snapshot(incidents=(official_a, official_b))
            ),
            scout=scout,
        )
        assert metrics["incidents_upserted"] == 3
        assert metrics["unique_incident_ids"] == 2
        assert metrics["official_incidents"] == 2
        found = incident_index.lookup_incidents(route_ids=["Q"])
        assert len(found["incidents"]) == 1
        assert found["incidents"][0]["incident_id"] == incident_index.incident_id_for(
            official_a
        )
        # Per-batch incidents_found counts only that batch scout's incidents;
        # the citywide official snapshot is never added to any batch.
        assert incident_index.get_coverage("upper-manhattan")["incidents_found"] == 1
        assert incident_index.get_coverage("midtown-manhattan")["incidents_found"] == 0

    async def test_collector_failure_is_unavailable_not_abort(self):
        async def broken():
            raise RuntimeError(PROVIDER_DOWN_MESSAGE)

        metrics = await self.run_job(collector=broken)
        assert metrics["status"] == "partial"
        assert metrics["official_sources"] == {
            SOURCE_ALERTS: "unavailable",
            SOURCE_GTFS_RT: "unavailable",
        }
        assert metrics["official_incidents"] == 0
        coverage = incident_index.get_coverage("upper-manhattan")
        assert coverage["coverage_status"] == "partial"  # X usable, official not.
        assert coverage["last_official_refresh_at"] is None
        assert cache.cache_get(job.JOB_LOCK_KEY) is None

    async def test_cancellation_releases_lock_and_records_bounded_metrics(self):
        async def slow(batch):
            await asyncio.sleep(60)
            return _scout_result(batch.batch_id)

        task = asyncio.create_task(self.run_job(scout=slow))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cache.cache_get(job.JOB_LOCK_KEY) is None
        stored = job.last_job_metrics()
        assert stored["status"] == "failed"
        assert "incidents" not in stored
        assert "payload" not in json.dumps(stored)

    async def test_unexpected_failure_marks_failed_and_releases_lock(self):
        with patch.object(
            incident_index, "upsert_incident", side_effect=RuntimeError("cache down")
        ):
            metrics = await self.run_job(
                collector=AsyncMock(
                    return_value=_snapshot(
                        incidents=({"source": "s", "source_id": "x"},)
                    )
                )
            )
        assert metrics["status"] == "failed"
        assert metrics["error"] == "RuntimeError"
        assert cache.cache_get(job.JOB_LOCK_KEY) is None
        stored = job.last_job_metrics()
        assert stored["status"] == "failed"
        assert "incidents" not in stored

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
        assert stored["status"] == "complete"
        assert stored["coverage"]["current"] == 10
        assert stored["incidents_upserted"] == 11
        assert stored["official_incidents"] == 1
        assert stored["unique_incident_ids"] == 1
        assert stored["model_calls"] == 10
        for batch in INCIDENT_BATCHES:
            assert incident_index.get_coverage(batch.batch_id)["incidents_found"] == 1
        assert "incidents" not in stored
        assert "source_records" not in stored
        assert "payload" not in json.dumps(stored)
        assert isinstance(metrics["duration_ms"], (int, float))


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
        assert "feature_flags" not in text
        assert "ny511" not in text
        assert "511" not in text

    def test_public_seams_point_at_production_boundaries(self):
        assert job.collect_official_incidents is real_collect_official_incidents
        assert job.scout_incident_batch is real_scout_incident_batch
        assert job.SCOUT_CONCURRENCY == 2
        assert len(job.INCIDENT_BATCHES) == 10


if __name__ == "__main__":
    unittest.main()
