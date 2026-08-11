import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("APP_KEY", "test-app-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
from app import main
from app.utils import cache


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.routes_config = patch.dict(
            os.environ,
            {"GOOGLE_ROUTES_API_KEY": "test-google-routes-key"},
            clear=False,
        )
        self.routes_config.start()
        self.addCleanup(self.routes_config.stop)

    async def test_session_store_probe_calls_successful_ping_off_event_loop(self):
        class Client:
            def ping(self):
                return True
        with patch.object(cache, "redis_client", Client()):
            self.assertTrue(await main._session_store_ready())

    async def test_session_store_probe_rejects_raised_ping(self):
        class Client:
            def ping(self):
                raise OSError("offline")
        with patch.object(cache, "redis_client", Client()):
            self.assertFalse(await main._session_store_ready())

    async def test_session_store_probe_bounds_blocking_ping(self):
        class Client:
            def ping(self):
                time.sleep(1)
                return True
        started = time.monotonic()
        with patch.object(cache, "redis_client", Client()):
            self.assertFalse(await main._session_store_ready())
        self.assertLess(time.monotonic() - started, 0.5)

    async def test_liveness_does_not_depend_on_startup_or_redis(self):
        self.assertEqual(await main.health(), {"status": "ok"})

    async def test_readiness_rejects_incomplete_startup(self):
        with patch.object(main.app, "state", SimpleNamespace(startup_complete=False)):
            response = await main.readiness()
        self.assertEqual(response.status_code, 503)

    async def test_readiness_rejects_missing_routes_provider_configuration(self):
        with patch.object(main.app, "state", SimpleNamespace(startup_complete=True)), patch.dict(
            os.environ, {"GOOGLE_ROUTES_API_KEY": ""}, clear=False
        ):
            response = await main.readiness()
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"reason":"routes_provider_config"', response.body)

    async def test_readiness_rejects_missing_durable_chat_store(self):
        with patch.object(main.app, "state", SimpleNamespace(startup_complete=True)), patch.dict(
            os.environ, {"REDIS_URL": ""}, clear=False
        ), patch.object(main.agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", False):
            response = await main.readiness()
        self.assertEqual(response.status_code, 503)

    async def test_readiness_accepts_started_redis_topology(self):
        with patch.object(main.app, "state", SimpleNamespace(startup_complete=True)), patch.dict(
            os.environ,
            {
                "REDIS_URL": "redis://example.invalid:6379/0",
                "SMARTROUTE_ENV": "",
                "APP_ENV": "",
                "ENVIRONMENT": "",
            },
            clear=False,
        ), patch.object(main, "_session_store_ready", AsyncMock(return_value=True)):
            response = await main.readiness()
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["chat_sessions"], "durable")
        self.assertEqual(response["runtime_mode"], "unknown")

    async def test_readiness_rejects_unreachable_configured_redis(self):
        with patch.object(main.app, "state", SimpleNamespace(startup_complete=True)), patch.dict(
            os.environ,
            {
                "REDIS_URL": "redis://example.invalid:6379/0",
                "SMARTROUTE_ENV": "",
                "APP_ENV": "",
                "ENVIRONMENT": "",
            },
            clear=False,
        ), patch.object(main, "_session_store_ready", AsyncMock(return_value=False)):
            response = await main.readiness()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.body,
            b'{"status":"not_ready","reason":"redis_session_store_unreachable","runtime_mode":"unknown"}',
        )

    async def test_local_memory_readiness_is_explicit_test_only_state(self):
        with patch.object(main.app, "state", SimpleNamespace(startup_complete=True)), patch.dict(
            os.environ,
            {
                "REDIS_URL": "",
                "SMARTROUTE_ENV": "test",
                "APP_ENV": "",
                "ENVIRONMENT": "",
            },
            clear=False,
        ), patch.object(main.agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True):
            response = await main.readiness()
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["chat_sessions"], "local")

    async def test_memory_session_flag_cannot_make_production_ready(self):
        with patch.object(main.app, "state", SimpleNamespace(startup_complete=True)), patch.dict(
            os.environ,
            {
                "REDIS_URL": "",
                "SMARTROUTE_ENV": "production",
                "APP_ENV": "",
                "ENVIRONMENT": "",
            },
            clear=False,
        ), patch.object(main.agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True):
            response = await main.readiness()
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"reason":"redis_session_store"', response.body)
