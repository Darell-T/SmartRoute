"""Failure, cache, and singleflight coverage for route incident scans."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.services.trips import incident_scan_cache, incidents
from app.services.trips.incident_context import (
    CandidateStopAssociation,
    CandidateStopContext,
)


def _context() -> list[CandidateStopContext]:
    return [CandidateStopContext(
        "D24",
        "Church Av",
        40.650,
        -73.963,
        [CandidateStopAssociation("candidate-0", mode="subway", route_id="Q")],
    )]


def _complete() -> dict:
    return {
        "incidents": [],
        "scan_metadata": {
            "status": "complete",
            "sources": {"attempted": ["x_search", "web_search"], "completed": ["x_search", "web_search"]},
        },
    }


class IncidentScanFailureAndCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        incidents._inflight_scans.clear()
        incident_scan_cache._local_fallback.clear()

    async def asyncTearDown(self) -> None:
        for task in list(incidents._inflight_scans.values()):
            task.cancel()
        await asyncio.gather(*incidents._inflight_scans.values(), return_exceptions=True)
        incidents._inflight_scans.clear()
        incident_scan_cache._local_fallback.clear()

    async def test_timeout_returns_failed_contract_without_fabricated_evidence(self):
        async def never_returns(_context):
            await asyncio.Event().wait()

        with patch.object(incidents, "TRIP_INCIDENT_SCAN_TIMEOUT_S", 0.001), patch.object(
            incidents, "get_incidents", new=never_returns
        ):
            result = await incidents._scan_route_incidents_with_metadata(_context())

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertEqual(result["scan_metadata"]["sources"]["errors"], ["timeout"])

    async def test_single_source_rows_are_warnings_not_advisor_evidence(self):
        row = {
            "location": "Church Avenue",
            "nearby_station": "Church Av",
            "severity": "high",
            "description": "Access restriction reported.",
            "impact_scope": "station_access",
            "affected_candidate_route_ids": ["candidate-0"],
            "evidence": [{
                "source_type": "x_search",
                "source_url": "https://x.com/one-source/status/1",
                "source_origin": "@one-source",
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }],
            "corroborated": False,
            "advisor_eligible": False,
        }
        with patch.object(incidents, "get_incidents", new=AsyncMock(return_value={
            "incidents": [row],
            "scan_metadata": {"status": "complete", "sources": {"completed": ["x_search", "web_search"]}},
        })):
            result = await incidents._scan_route_incidents_with_metadata(_context())

        self.assertEqual(result["incidents"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["scan_metadata"]["warning_count"], 1)

    async def test_concurrent_callers_share_one_scan_and_one_inflight_task(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def scanner(_context):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return _complete()

        with patch.object(incidents, "load_cached_scan", return_value=None), patch.object(
            incidents, "cache_scan_contract"
        ), patch.object(incidents, "get_incidents", new=scanner):
            first = asyncio.create_task(incidents.scan_route_incidents(_context()))
            second = asyncio.create_task(incidents.scan_route_incidents(_context()))
            await entered.wait()
            self.assertEqual(calls, 1)
            self.assertEqual(len(incidents._inflight_scans), 1)
            release.set()
            one, two = await asyncio.gather(first, second)

        self.assertEqual(one["scan_metadata"]["status"], "complete")
        self.assertEqual(two["scan_metadata"]["status"], "complete")
        self.assertEqual(calls, 1)
        self.assertEqual(incidents._inflight_scans, {})

    async def test_cancelled_waiter_does_not_cancel_shared_scan(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def scanner(_context):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return _complete()

        with patch.object(incidents, "load_cached_scan", return_value=None), patch.object(
            incidents, "cache_scan_contract"
        ), patch.object(incidents, "get_incidents", new=scanner):
            cancelled = asyncio.create_task(incidents.scan_route_incidents(_context()))
            await entered.wait()
            survivor = asyncio.create_task(incidents.scan_route_incidents(_context()))
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            release.set()
            result = await survivor

        self.assertEqual(calls, 1)
        self.assertEqual(result["scan_metadata"]["status"], "complete")

    async def test_failed_scan_is_cached_as_short_backoff_not_an_all_clear(self):
        stored: dict[str, tuple[str, int]] = {}

        def set_cache(key: str, value: str, ttl: int) -> None:
            stored[key] = (value, ttl)

        with patch.object(incidents, "load_cached_scan", return_value=None), patch.object(
            incident_scan_cache.cache, "redis_client", None
        ), patch.object(
            incident_scan_cache, "_local_set", side_effect=set_cache
        ), patch.object(
            incidents, "get_incidents", new=AsyncMock(side_effect=RuntimeError("provider failed"))
        ):
            result = await incidents.scan_route_incidents(_context())

        self.assertEqual(result["scan_metadata"]["status"], "failed")
        serialized, ttl = next(iter(stored.values()))
        self.assertEqual(ttl, incident_scan_cache.FAILURE_BACKOFF_TTL_S)
        self.assertEqual(json.loads(serialized)["incidents"], [])

    async def test_local_cache_fallback_has_a_fixed_capacity(self):
        result = _complete()
        result["scan_metadata"]["scanned_at"] = datetime.now(timezone.utc).isoformat()
        with patch.object(incident_scan_cache.cache, "redis_client", None):
            for index in range(incident_scan_cache.LOCAL_FALLBACK_MAX_ENTRIES + 2):
                incident_scan_cache.cache_scan_contract(f"incident-{index}", result)

        self.assertEqual(
            len(incident_scan_cache._local_fallback),
            incident_scan_cache.LOCAL_FALLBACK_MAX_ENTRIES,
        )
        self.assertNotIn("incident-0", incident_scan_cache._local_fallback)

    async def test_cached_evidence_rederives_the_citation_identity(self):
        now = datetime.now(timezone.utc).isoformat()
        normalized = incident_scan_cache.normalize_advisor_incident({
            "location": "Church Avenue",
            "nearby_station": "Church Av",
            "severity": "high",
            "description": "Access restriction reported.",
            "impact_scope": "station_access",
            "affected_candidate_route_ids": ["candidate-0"],
            "advisor_eligible": True,
            "evidence": [
                {
                    "source_type": "x_search",
                    "source_url": "https://x.com/citydesk/status/1",
                    "source_origin": "@citydesk",
                    "source_identity": "web:spoofed.example",
                    "observed_at": now,
                },
                {
                    "source_type": "web_search",
                    "source_url": "https://news.example.test/report",
                    "source_origin": "Independent News",
                    "source_identity": "x:spoofed",
                    "observed_at": now,
                },
            ],
        })

        self.assertEqual(
            [entry["source_identity"] for entry in normalized["evidence"]],
            ["x:citydesk", "web:news.example.test"],
        )
        self.assertTrue(normalized["advisor_eligible"])


if __name__ == "__main__":
    unittest.main()
