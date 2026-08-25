"""Focused tests for the optional secret-authenticated incident refresh route.

The job boundary is always injected, so these tests make zero provider,
model, or Redis calls.
"""

from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routers import incident_refresh


class IncidentJobRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_configured_secret_returns_404(self):
        with patch.dict(os.environ, {"INCIDENT_JOB_CRON_SECRET": ""}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                incident_refresh._verify_cron_secret("anything")
        self.assertEqual(raised.exception.status_code, 404)

    def test_wrong_secret_header_returns_403(self):
        with patch.dict(
            os.environ, {"INCIDENT_JOB_CRON_SECRET": "expected-secret"}, clear=False
        ):
            with self.assertRaises(HTTPException) as raised:
                incident_refresh._verify_cron_secret("wrong-secret")
        self.assertEqual(raised.exception.status_code, 403)

    def test_exact_secret_passes(self):
        with patch.dict(
            os.environ, {"INCIDENT_JOB_CRON_SECRET": "expected-secret"}, clear=False
        ):
            self.assertIsNone(incident_refresh._verify_cron_secret("expected-secret"))

    async def test_valid_secret_invokes_the_unconditional_job(self):
        with patch.object(
            incident_refresh.refresh,
            "run_background_incident_refresh",
            new=AsyncMock(return_value={"status": "complete"}),
        ) as run:
            result = await incident_refresh.incident_refresh()
        run.assert_awaited_once()
        self.assertEqual(result, {"status": "complete"})

    async def test_partial_and_lock_held_return_their_bounded_result(self):
        for status in ("partial", "lock_held"):
            with self.subTest(status=status):
                with patch.object(
                    incident_refresh.refresh,
                    "run_background_incident_refresh",
                    new=AsyncMock(return_value={"status": status}),
                ) as run:
                    result = await incident_refresh.incident_refresh()
                run.assert_awaited_once()
                self.assertEqual(result, {"status": status})

    async def test_failed_result_becomes_503_without_metrics_or_details(self):
        with patch.object(
            incident_refresh.refresh,
            "run_background_incident_refresh",
            new=AsyncMock(
                return_value={
                    "status": "failed",
                    "error": "RuntimeError",
                    "coverage": {"current": 0, "partial": 0, "unavailable": 10},
                }
            ),
        ) as run:
            with self.assertRaises(HTTPException) as raised:
                await incident_refresh.incident_refresh()
        run.assert_awaited_once()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Incident refresh failed")
        self.assertNotIn("RuntimeError", raised.exception.detail)
        self.assertNotIn("coverage", raised.exception.detail)

    def test_router_source_has_no_feature_flag_gating(self):
        source = inspect.getsource(incident_refresh)
        self.assertNotIn("feature_flags", source)
        self.assertNotIn("background_grok_worker", source)
        self.assertNotIn("background_incident_index", source)


if __name__ == "__main__":
    unittest.main()
