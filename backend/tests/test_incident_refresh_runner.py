"""Focused tests for the cron CLI entrypoint and the Render Blueprint.

The CLI is exercised with injected job boundaries, so zero provider, model,
or Redis calls happen in these tests.
"""

from __future__ import annotations

import asyncio
import io
import os
import pathlib
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

from scripts import run_incident_refresh

_REDIS = "redis://example.invalid:6379/0"


class CloseError(Exception):
    """Representative unexpected transport-close failure."""


def _job(status: str) -> AsyncMock:
    return AsyncMock(return_value={"status": status})


class IncidentRefreshRunnerTests(unittest.TestCase):
    def _run(self, *, job=None, close_error=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        close = AsyncMock()
        if close_error is not None:
            close.side_effect = close_error
        with (
            patch.dict(os.environ, {"REDIS_URL": _REDIS}, clear=False),
            patch.object(
                run_incident_refresh.refresh,
                "run_background_incident_refresh",
                new=job if job is not None else _job("complete"),
            ),
            patch.object(run_incident_refresh, "close_incident_scout_client", new=close),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = run_incident_refresh.main()
        return code, stdout.getvalue(), stderr.getvalue(), close

    def test_missing_shared_redis_fails_fast_without_job_call(self):
        job = AsyncMock()
        close = AsyncMock()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {"REDIS_URL": ""}, clear=False),
            patch.object(asyncio, "run", wraps=asyncio.run) as run_spy,
            patch.object(
                run_incident_refresh.refresh,
                "run_background_incident_refresh",
                new=job,
            ),
            patch.object(run_incident_refresh, "close_incident_scout_client", new=close),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = run_incident_refresh.main()

        assert code == 3
        run_spy.assert_called_once()
        job.assert_not_awaited()
        close.assert_awaited_once()
        message = stderr.getvalue()
        assert "REDIS_URL" in message
        assert "redis://" not in message
        assert "6379" not in message
        assert stdout.getvalue() == ""

    def test_main_uses_one_event_loop_and_closes_transport_on_it(self):
        job_loops = []
        close_loops = []

        def record_job_loop():
            loop = asyncio.get_running_loop()
            job_loops.append((loop, loop.is_running()))
            return {"status": "complete"}

        def record_close_loop():
            loop = asyncio.get_running_loop()
            close_loops.append((loop, loop.is_running()))

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {"REDIS_URL": _REDIS}, clear=False),
            patch.object(asyncio, "run", wraps=asyncio.run) as run_spy,
            patch.object(
                run_incident_refresh.refresh,
                "run_background_incident_refresh",
                new=AsyncMock(side_effect=record_job_loop),
            ),
            patch.object(
                run_incident_refresh,
                "close_incident_scout_client",
                new=AsyncMock(side_effect=record_close_loop),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = run_incident_refresh.main()

        assert code == 0, stderr
        # main() drives the whole lifecycle through exactly one event loop.
        run_spy.assert_called_once()
        assert len(job_loops) == 1
        assert len(close_loops) == 1
        job_loop, job_was_running = job_loops[0]
        close_loop, close_was_running = close_loops[0]
        assert job_loop is not None
        # The job and the transport close ran on the same running loop.
        assert job_loop is close_loop
        assert job_was_running
        assert close_was_running

    def test_cycle_status_maps_to_exit_code(self):
        for status, expected_code in (
            ("complete", 0),
            ("failed", 1),
        ):
            code, stdout, stderr, close = self._run(job=_job(status))
            assert code == expected_code, stderr
            assert f'"status":"{status}"' in stdout
            close.assert_awaited_once()

    def test_invalid_result_returns_nonzero_without_raw_details(self):
        job = AsyncMock(return_value=["not", "a", "mapping"])
        code, stdout, stderr, close = self._run(job=job)
        assert code == 2
        assert "cycle returned an invalid result" in stderr
        assert "not" not in stdout
        assert "Traceback" not in stderr
        close.assert_awaited_once()

    def test_cancelled_cycle_returns_nonzero_and_still_closes(self):
        async def cancel_cycle():
            raise asyncio.CancelledError()

        stdout = io.StringIO()
        stderr = io.StringIO()
        close = AsyncMock()
        with (
            patch.dict(os.environ, {"REDIS_URL": _REDIS}, clear=False),
            patch.object(run_incident_refresh, "run_once", new=cancel_cycle),
            patch.object(run_incident_refresh, "close_incident_scout_client", new=close),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = run_incident_refresh.main()

        assert code == 2
        close.assert_awaited_once()
        assert stdout.getvalue() == ""
        assert "Traceback" not in stderr

    def test_unhandled_exception_returns_nonzero_without_raw_details(self):
        job = AsyncMock(side_effect=RuntimeError("provider secret details"))
        code, stdout, stderr, close = self._run(job=job)
        assert code == 2
        assert "provider secret details" not in stdout
        assert "provider secret details" not in stderr
        assert "Traceback" not in stderr
        close.assert_awaited_once()

    def test_scout_client_close_always_awaited_even_when_close_fails(self):
        code, _stdout, stderr, close = self._run(
            close_error=CloseError("secret close payload")
        )
        assert code == 0
        close.assert_awaited_once()
        assert "CloseError" in stderr
        assert "secret close payload" not in stderr

    def test_stdout_stays_bounded_and_payload_free(self):
        job = AsyncMock(
            return_value={
                "status": "complete",
                "coverage": {"current": 10, "partial": 0, "unavailable": 0},
                "official_sources": {"mta_alerts": "current", "gtfs_rt": "current"},
                "incidents_upserted": 3,
                "official_incidents": 1,
                "unique_incident_ids": 2,
                "model_calls": 10,
                "scout_timeouts": 0,
                "duration_ms": 12.5,
                "error": "never-printed",
            }
        )
        code, stdout, _stderr, _close = self._run(job=job)
        assert code == 0
        assert '"coverage":{"current":10,"partial":0,"unavailable":0}' in stdout
        assert '"official_sources":{"mta_alerts":"current","gtfs_rt":"current"}' in stdout
        assert '"incidents_upserted":3' in stdout
        assert '"incidents":[' not in stdout
        assert '"error"' not in stdout
        assert "never-printed" not in stdout

class RenderBlueprintStaticTests(unittest.TestCase):
    def setUp(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        self.blueprint = (root / "render.yaml").read_text(encoding="utf-8")

    def test_blueprint_declares_the_incident_cron_contract(self):
        assert self.blueprint.count("type: cron") == 1
        assert "type: web" not in self.blueprint
        assert "database:" not in self.blueprint
        assert "region: ohio" in self.blueprint
        assert "branch:" not in self.blueprint
        assert "repo:" not in self.blueprint
        assert "secrets:" not in self.blueprint
        assert 'schedule: "0,30 * * * *"' in self.blueprint
        assert 'startCommand: "python scripts/run_incident_refresh.py"' in self.blueprint
        assert 'buildCommand: "pip install -r requirements.txt"' in self.blueprint
        assert "rootDir: backend" in self.blueprint
        assert "runtime: python" in self.blueprint
        assert "autoDeployTrigger: checksPass" in self.blueprint
        assert "SMARTROUTE_ENV" in self.blueprint
        assert "value: production" in self.blueprint
        assert "REDIS_URL" in self.blueprint
        assert "XAI_API_KEY" in self.blueprint
        assert self.blueprint.count("sync: false") == 2
        assert "envVars:" in self.blueprint
        # A bare "env:" field is not part of the Render Blueprint service
        # spec and must not creep back in.
        assert re.search(r"(?m)^env:\s*$", self.blueprint) is None


if __name__ == "__main__":
    unittest.main()
