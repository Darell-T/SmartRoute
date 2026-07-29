"""Production-boundary continuation tests for intelligence scan failures."""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from app.services.trips import incidents


class IncidentScanContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_returns_a_failed_metadata_contract_without_incidents(self):
        async def never_returns(*_args, **_kwargs):
            await asyncio.Event().wait()

        with patch.object(incidents, "TRIP_INCIDENT_SCAN_TIMEOUT_S", 0.001), patch.object(
            incidents, "get_incidents", new=never_returns
        ):
            result = await incidents._scan_route_incidents_with_metadata(["34 St-Herald Sq"])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertEqual(result["scan_metadata"]["snapshot_status"], "unavailable")
        self.assertNotIn("credential", result["scan_metadata"])

    async def test_scanner_exception_continues_with_a_failed_metadata_contract(self):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            incidents, "get_incidents", new=AsyncMock(side_effect=RuntimeError("provider token=super-secret"))
        ):
            result = await incidents._scan_route_incidents_with_metadata(["34 St-Herald Sq"])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertEqual(result["scan_metadata"]["snapshot_status"], "unavailable")
        self.assertNotIn("credential", result["scan_metadata"])
        self.assertNotIn("super-secret", output.getvalue())

    async def test_malformed_scanner_shape_does_not_crash_or_fabricate_incidents(self):
        with patch.object(
            incidents,
            "get_incidents",
            new=AsyncMock(return_value={
                "incidents": {},
                "scan_metadata": {"status": "complete", "snapshot_status": "fresh", "credential": "secret"},
            }),
        ):
            result = await incidents._scan_route_incidents_with_metadata(["34 St-Herald Sq"])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertEqual(result["scan_metadata"]["snapshot_status"], "unavailable")
        self.assertNotIn("credential", result["scan_metadata"])

    async def test_valid_complete_empty_scan_remains_a_clear_contract(self):
        with patch.object(
            incidents,
            "get_incidents",
            new=AsyncMock(return_value={"incidents": [], "scan_metadata": {"status": "complete", "snapshot_status": "fresh"}}),
        ):
            result = await incidents._scan_route_incidents_with_metadata(["34 St-Herald Sq"])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "complete")
        self.assertEqual(result["scan_metadata"]["snapshot_status"], "fresh")
        self.assertEqual(result["scan_metadata"]["merge"]["before_count"], 0)

    async def test_complete_scan_without_a_fresh_snapshot_is_downgraded(self):
        cases = {
            "missing": ({"status": "complete"}, "failed", "unavailable"),
            "unavailable": (
                {"status": "complete", "snapshot_status": "unavailable"},
                "failed",
                "unavailable",
            ),
            "stale": (
                {"status": "complete", "snapshot_status": "stale"},
                "partial",
                "stale",
            ),
            "disabled": (
                {"status": "complete", "snapshot_status": "disabled"},
                "disabled",
                "disabled",
            ),
        }
        for lifecycle, (metadata, expected_status, expected_snapshot) in cases.items():
            with self.subTest(lifecycle=lifecycle), patch.object(
                incidents,
                "get_incidents",
                new=AsyncMock(return_value={"incidents": [], "scan_metadata": metadata}),
            ):
                result = await incidents._scan_route_incidents_with_metadata(["34 St-Herald Sq"])
            self.assertEqual(result["scan_metadata"]["status"], expected_status)
            self.assertEqual(result["scan_metadata"]["snapshot_status"], expected_snapshot)

    async def test_unknown_scanner_status_becomes_failed_without_changing_incident_normalization(self):
        row = {
            "location": "34 St", "nearby_station": "34 St-Herald Sq", "severity": "low",
            "description": "Recorded report", "source": "grok_web",
        }
        with patch.object(
            incidents,
            "get_incidents",
            new=AsyncMock(return_value={"incidents": [row], "scan_metadata": {"status": "unknown"}}),
        ):
            result = await incidents._scan_route_incidents_with_metadata(["34 St-Herald Sq"])

        # The advisor can still receive conservatively normalized evidence,
        # but an unknown provider status is never an all-clear.
        self.assertEqual(len(result["incidents"]), 1)
        self.assertEqual(result["incidents"][0]["source"], "grok_web")
        self.assertEqual(result["scan_metadata"]["status"], "failed")
        self.assertEqual(result["scan_metadata"]["snapshot_status"], "unavailable")
        self.assertEqual(result["scan_metadata"]["merge"]["after_count"], 1)

    def test_merge_diagnostics_drop_url_and_credential_shaped_source_names(self):
        safe = incidents._safe_merge_sources(
            {
                "@NYScanner": 1,
                "https://provider.test/?api_key=literal-secret": 2,
                "Bearer super-token": 3,
                "sk-liveSecret": 4,
            }
        )
        self.assertEqual(safe, {"@NYScanner": 1})


if __name__ == "__main__":
    unittest.main()
