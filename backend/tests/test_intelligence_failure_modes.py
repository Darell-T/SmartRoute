"""Failure and coverage-truth checks for indexed incident evidence.

The rider path performs one immediate index lookup. The retired request-time
provider scan and scan cache must stay absent so broad xAI/Web research cannot
drift back onto the critical path. Offline validation helpers remain separate.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import incident_index
from app.services.trips import incident_index_adapter, incidents
from app.services.trips.incident_context import (
    CandidateStopAssociation,
    CandidateStopContext,
)
from app.utils import cache


def _context() -> list[CandidateStopContext]:
    return [
        CandidateStopContext(
            "D24",
            "Church Av",
            40.650,
            -73.963,
            [CandidateStopAssociation("candidate-0", mode="subway", route_id="Q")],
        )
    ]


class IncidentIndexFailureAndCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_client = cache.redis_client
        cache.redis_client = None
        cache._mem.clear()

    def tearDown(self) -> None:
        cache.redis_client = self._original_client
        cache._mem.clear()

    async def test_index_lookup_failure_degrades_without_any_cold_scan(self):
        with patch.object(
            incidents.incident_index,
            "lookup_incidents",
            side_effect=RuntimeError("cache unavailable"),
        ):
            result = await incidents.scan_route_incidents(_context())

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["warnings"], [])
        metadata = result["scan_metadata"]
        self.assertEqual(metadata["lookup_status"], "failed")
        self.assertEqual(metadata["coverage_status"], "unavailable")
        self.assertEqual(metadata["status"], "unavailable")
        self.assertEqual(metadata["lookup_kind"], "index")
        self.assertEqual(
            metadata["sources"],
            {"attempted": ["incident_index"], "completed": []},
        )
        self.assertFalse(incidents.incident_lookup_succeeded(metadata))
        self.assertFalse(incidents.incident_scan_is_complete(metadata))

    async def test_empty_context_is_unscanned_never_complete(self):
        result = await incidents.scan_route_incidents([])
        metadata = result["scan_metadata"]
        self.assertEqual(result["incidents"], [])
        self.assertEqual(metadata["status"], "unscanned")
        self.assertEqual(metadata["coverage_status"], "unscanned")
        self.assertEqual(metadata["lookup_kind"], "index")
        self.assertFalse(incidents.incident_scan_is_complete(metadata))

    async def test_current_coverage_with_empty_incidents_is_not_an_all_clear_claim(self):
        incident_index.set_coverage(
            {"coverage_id": "central-south-brooklyn", "coverage_status": "current"}
        )
        result = await incidents.scan_route_incidents(_context())
        metadata = result["scan_metadata"]
        self.assertEqual(result["incidents"], [])
        # Current is permitted only from an explicit current coverage record.
        self.assertEqual(metadata["coverage_status"], "current")
        self.assertEqual(metadata["status"], "complete")
        self.assertTrue(incidents.incident_scan_is_complete(metadata))

    async def test_partial_coverage_stays_incomplete_even_with_advisor_incidents(self):
        incident_index.set_coverage(
            {"coverage_id": "lower-manhattan", "coverage_status": "current"}
        )
        incident_index.set_coverage(
            {"coverage_id": "downtown-northwest-brooklyn", "coverage_status": "current"}
        )
        incident_index.set_coverage(
            {"coverage_id": "western-queens", "coverage_status": "unavailable"}
        )
        incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "partial-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "subway_operations",
                "description": "Confirmed indexed incident.",
                "affected_stop_ids": ["W4"],
                "affected_route_ids": ["Q"],
                "affected_batch_ids": ["lower-manhattan"],
            }
        )
        overlap = CandidateStopContext(
            "W4",
            "Overlap",
            40.72,
            -73.96,
            [CandidateStopAssociation("candidate-0", mode="subway", route_id="Q")],
        )

        result = await incidents.scan_route_incidents([overlap])

        self.assertEqual(len(result["incidents"]), 1)
        metadata = result["scan_metadata"]
        self.assertEqual(metadata["coverage_status"], "partial")
        self.assertEqual(metadata["status"], "partial")
        self.assertTrue(incidents.incident_lookup_succeeded(metadata))
        self.assertFalse(incidents.incident_scan_is_complete(metadata))

    def test_metadata_helpers_distinguish_lookup_success_from_coverage_freshness(self):
        current = {
            "status": "complete",
            "lookup_status": "complete",
            "coverage_status": "current",
        }
        partial = {
            "status": "partial",
            "lookup_status": "complete",
            "coverage_status": "partial",
        }
        self.assertTrue(incidents.incident_scan_is_complete(current))
        self.assertTrue(incidents.incident_lookup_succeeded(current))
        self.assertFalse(incidents.incident_scan_is_complete(partial))
        self.assertTrue(incidents.incident_lookup_succeeded(partial))
        self.assertFalse(incidents.incident_lookup_succeeded({"status": "complete"}))
        self.assertFalse(incidents.incident_scan_is_complete(None))
        self.assertFalse(incidents.incident_lookup_succeeded(None))

    def test_normal_trip_incidents_modules_have_no_cold_scan_path(self):
        for module in (incidents, incident_index_adapter):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for forbidden in (
                "incident_monitor",
                "get_incidents",
                "x_search",
                "web_search",
                "incident_scan_cache",
                "_inflight_scans",
                "asyncio",
            ):
                self.assertNotIn(forbidden, source)

    def test_retired_request_time_incident_modules_are_absent(self):
        services_root = Path(__file__).resolve().parents[1] / "app" / "services"
        retired = (
            services_root / "incident_monitor.py",
            services_root / "trips" / "incident_scan_cache.py",
        )
        self.assertEqual([str(path) for path in retired if path.exists()], [])


if __name__ == "__main__":
    unittest.main()
