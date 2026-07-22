import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


os.environ.setdefault("APP_KEY", "test-app-key")
from app import main


async def _idle_loop():
    await asyncio.Event().wait()


class IncidentLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_poller_starts_once_and_stops_without_network(self):
        poller = SimpleNamespace(store=object(), start=Mock(), stop=AsyncMock())
        app = SimpleNamespace(state=SimpleNamespace())
        settings = SimpleNamespace(enabled=True)
        fake_gtfs = SimpleNamespace()
        with patch.object(main, "GTFSStaticData", return_value=fake_gtfs), patch.object(
            main.NY511Settings, "from_env", return_value=settings
        ), patch.object(main, "NY511Poller", return_value=poller) as poller_class, patch.object(
            main, "_init_pool_bg", AsyncMock()
        ), patch.object(main, "_gtfs_refresh_loop", _idle_loop), patch.object(
            main, "_realtime_warm_loop", _idle_loop
        ), patch.object(main, "close_pool"), patch.object(main, "configure_snapshot_store") as configure:
            async with main.lifespan(app):
                self.assertIs(app.state.ny511_poller, poller)
                self.assertIs(app.state.ny511_snapshot_store, poller.store)
                poller_class.assert_called_once_with(settings)
                poller.start.assert_called_once_with()
            poller.stop.assert_awaited_once_with()
            self.assertEqual(configure.call_args_list[-1].args, (None,))

    async def test_unconfigured_poller_is_not_created_or_started(self):
        app = SimpleNamespace(state=SimpleNamespace())
        with patch.object(main, "GTFSStaticData", return_value=SimpleNamespace()), patch.object(
            main.NY511Settings, "from_env", return_value=SimpleNamespace(enabled=False)
        ), patch.object(main, "NY511Poller") as poller_class, patch.object(
            main, "_init_pool_bg", AsyncMock()
        ), patch.object(main, "_gtfs_refresh_loop", _idle_loop), patch.object(
            main, "_realtime_warm_loop", _idle_loop), patch.object(main, "close_pool"):
            async with main.lifespan(app):
                self.assertIsNone(app.state.ny511_poller)
                self.assertIsNone(app.state.ny511_snapshot_store)
            poller_class.assert_not_called()
