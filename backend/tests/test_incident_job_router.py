"""Focused tests for the optional secret-authenticated incident refresh route.

The job boundary is always injected, so these tests make zero provider,
model, or Redis calls.
"""

from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from app.routers import incident_refresh
from fastapi import HTTPException


class IncidentJobRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_configured_secret_returns_404(self):
        with patch.dict(os.environ, {"INCIDENT_JOB_CRON_SECRET": ""}, clear=False), pytest.raises(
            HTTPException
        ) as raised:
            incident_refresh._verify_cron_secret("anything")
        assert raised.value.status_code == 404

    def test_wrong_secret_header_returns_403(self):
        with patch.dict(
            os.environ, {"INCIDENT_JOB_CRON_SECRET": "expected-secret"}, clear=False
        ), pytest.raises(HTTPException) as raised:
            incident_refresh._verify_cron_secret("wrong-secret")
        assert raised.value.status_code == 403

    def test_exact_secret_passes(self):
        with patch.dict(
            os.environ, {"INCIDENT_JOB_CRON_SECRET": "expected-secret"}, clear=False
        ):
            assert incident_refresh._verify_cron_secret("expected-secret") is None

    async def test_valid_secret_invokes_the_unconditional_job(self):
        with patch.object(
            incident_refresh.refresh,
            "run_background_incident_refresh",
            new=AsyncMock(return_value={"status": "complete"}),
        ) as run:
            result = await incident_refresh.incident_refresh()
        run.assert_awaited_once()
        assert result == {"status": "complete"}

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
        ) as run, pytest.raises(HTTPException) as raised:
            await incident_refresh.incident_refresh()
        run.assert_awaited_once()
        assert raised.value.status_code == 503
        assert raised.value.detail == "Incident refresh failed"
        assert "RuntimeError" not in raised.value.detail
        assert "coverage" not in raised.value.detail

    def test_router_source_has_no_feature_flag_gating(self):
        source = inspect.getsource(incident_refresh)
        assert "feature_flags" not in source
        assert "background_grok_worker" not in source
        assert "background_incident_index" not in source


if __name__ == "__main__":
    unittest.main()
