import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


os.environ.setdefault("APP_KEY", "test-app-key")
from app import main


async def _idle_loop():
    await asyncio.Event().wait()


class LiveRuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_owns_and_closes_shared_runtime_clients(self):
        gtfs = Mock()
        gtfs._pattern_index = SimpleNamespace(patterns=[], stops=[])
        gtfs.load_scheduled_arrivals.return_value = True
        app = SimpleNamespace(state=SimpleNamespace())

        with patch.object(main, "GTFSStaticData", return_value=gtfs), patch.object(
            main, "start_bus_client", AsyncMock()
        ) as start_bus, patch.object(
            main, "close_bus_client", AsyncMock()
        ) as close_bus, patch.object(
            main, "close_incident_scout_client", AsyncMock()
        ) as close_scout, patch.object(
            main, "close_crowd_search_client", AsyncMock()
        ) as close_crowd, patch.object(
            main.network_snapshot_store, "close", AsyncMock()
        ) as close_snapshot, patch.object(
            main, "_init_pool_bg", AsyncMock()
        ), patch.object(main, "_gtfs_refresh_loop", _idle_loop), patch.object(
            main, "_realtime_warm_loop", _idle_loop
        ), patch.object(main, "close_pool"):
            async with main.lifespan(app):
                self.assertIs(app.state.gtfs, gtfs)
                self.assertTrue(app.state.startup_complete)
                self.assertFalse(hasattr(app.state, "ny511_poller"))
                start_bus.assert_awaited_once_with()

        self.assertFalse(app.state.startup_complete)
        close_snapshot.assert_awaited_once_with()
        close_bus.assert_awaited_once_with()
        close_scout.assert_awaited_once_with()
        close_crowd.assert_awaited_once_with()
        # The dormant request-time incident_monitor client is no longer
        # imported or closed by the application lifecycle.
        self.assertFalse(hasattr(main, "close_incident_client"))
        self.assertTrue(callable(main.close_incident_scout_client))
