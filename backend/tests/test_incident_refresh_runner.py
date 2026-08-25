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

        self.assertEqual(code, 3)
        run_spy.assert_called_once()
        job.assert_not_awaited()
        close.assert_awaited_once()
        message = stderr.getvalue()
        self.assertIn("REDIS_URL", message)
        self.assertNotIn("redis://", message)
        self.assertNotIn("6379", message)
        self.assertEqual(stdout.getvalue(), "")

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

        self.assertEqual(code, 0, stderr)
        # main() drives the whole lifecycle through exactly one event loop.
        run_spy.assert_called_once()
        self.assertEqual(len(job_loops), 1)
        self.assertEqual(len(close_loops), 1)
        job_loop, job_was_running = job_loops[0]
        close_loop, close_was_running = close_loops[0]
        self.assertIsNotNone(job_loop)
        # The job and the transport close ran on the same running loop.
        self.assertIs(job_loop, close_loop)
        self.assertTrue(job_was_running)
        self.assertTrue(close_was_running)

    def test_complete_partial_and_lock_held_cycles_return_zero(self):
        for status in ("complete", "partial", "lock_held"):
            with self.subTest(status=status):
                code, stdout, stderr, close = self._run(job=_job(status))
                self.assertEqual(code, 0, stderr)
                self.assertIn(f'"status":"{status}"', stdout)
                close.assert_awaited_once()

    def test_failed_cycle_returns_nonzero(self):
        code, stdout, stderr, close = self._run(job=_job("failed"))
        self.assertEqual(code, 1)
        self.assertIn('"status":"failed"', stdout)
        close.assert_awaited_once()

    def test_invalid_result_returns_nonzero_without_raw_details(self):
        job = AsyncMock(return_value=["not", "a", "mapping"])
        code, stdout, stderr, close = self._run(job=job)
        self.assertEqual(code, 2)
        self.assertIn("cycle returned an invalid result", stderr)
        self.assertNotIn("not", stdout)
        self.assertNotIn("Traceback", stderr)
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

        self.assertEqual(code, 2)
        close.assert_awaited_once()
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("Traceback", stderr)

    def test_unhandled_exception_returns_nonzero_without_raw_details(self):
        job = AsyncMock(side_effect=RuntimeError("provider secret details"))
        code, stdout, stderr, close = self._run(job=job)
        self.assertEqual(code, 2)
        self.assertNotIn("provider secret details", stdout)
        self.assertNotIn("provider secret details", stderr)
        self.assertNotIn("Traceback", stderr)
        close.assert_awaited_once()

    def test_scout_client_close_always_awaited_even_when_close_fails(self):
        code, stdout, stderr, close = self._run(close_error=RuntimeError("close boom"))
        self.assertEqual(code, 0)
        close.assert_awaited_once()
        self.assertNotIn("close boom", stderr)

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
        code, stdout, stderr, _close = self._run(job=job)
        self.assertEqual(code, 0)
        self.assertIn('"coverage":{"current":10,"partial":0,"unavailable":0}', stdout)
        self.assertIn('"official_sources":{"mta_alerts":"current","gtfs_rt":"current"}', stdout)
        self.assertIn('"incidents_upserted":3', stdout)
        self.assertNotIn('"incidents":[', stdout)
        self.assertNotIn('"error"', stdout)
        self.assertNotIn("never-printed", stdout)

    def test_run_once_defaults_to_production_boundaries_when_not_injected(self):
        # The module-level seam must point at the real orchestration function
        # so the CLI cannot silently drift from the shared job contract.
        from app.services.incidents import refresh as job_module

        self.assertIs(
            run_incident_refresh.refresh.run_background_incident_refresh,
            job_module.run_background_incident_refresh,
        )


class RenderBlueprintStaticTests(unittest.TestCase):
    def setUp(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        self.blueprint = (root / "render.yaml").read_text(encoding="utf-8")

    def test_blueprint_declares_only_the_incident_cron_service(self):
        self.assertEqual(self.blueprint.count("type: cron"), 1)
        self.assertNotIn("type: web", self.blueprint)
        self.assertNotIn("database:", self.blueprint)
        self.assertIn("region: ohio", self.blueprint)
        self.assertNotIn("branch:", self.blueprint)
        self.assertNotIn("repo:", self.blueprint)
        self.assertNotIn("secrets:", self.blueprint)

    def test_blueprint_exact_schedule_command_and_runtime(self):
        self.assertIn('schedule: "0,30 * * * *"', self.blueprint)
        self.assertIn('startCommand: "python scripts/run_incident_refresh.py"', self.blueprint)
        self.assertIn('buildCommand: "pip install -r requirements.txt"', self.blueprint)
        self.assertIn("rootDir: backend", self.blueprint)
        self.assertIn("runtime: python", self.blueprint)
        self.assertIn("autoDeployTrigger: checksPass", self.blueprint)

    def test_blueprint_declares_required_shared_environment(self):
        self.assertIn("SMARTROUTE_ENV", self.blueprint)
        self.assertIn("value: production", self.blueprint)
        self.assertIn("REDIS_URL", self.blueprint)
        self.assertIn("XAI_API_KEY", self.blueprint)
        self.assertEqual(self.blueprint.count("sync: false"), 2)

    def test_blueprint_uses_official_envvars_field(self):
        self.assertIn("envVars:", self.blueprint)
        # A bare "env:" field is not part of the Render Blueprint service
        # spec and must not creep back in.
        self.assertIsNone(re.search(r"(?m)^env:\s*$", self.blueprint))


if __name__ == "__main__":
    unittest.main()
