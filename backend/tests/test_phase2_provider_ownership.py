"""Focused ownership tests for the Phase 2 provider seams."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent.tools.transit import check_transit
from app.services.live_feed import snapshot as live_snapshot
from app.services.trips.crowds import event as event_crowd
from app.services.trips.crowds import event_provider


class ProviderOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_live_snapshot_acquires_network_then_uses_pure_builder(self):
        network = object()
        built = {"nearest_stop": None}
        with (
            patch.object(
                live_snapshot.network_snapshot_store,
                "get_or_refresh",
                new=AsyncMock(return_value=network),
            ) as acquire,
            patch.object(
                live_snapshot,
                "_build_live_snapshot",
                new=AsyncMock(return_value=built),
            ) as pure_builder,
        ):
            result = await live_snapshot.build_live_snapshot(
                "gtfs", 40.73, -73.99, {"Q"}
            )

        self.assertIs(result, built)
        acquire.assert_awaited_once_with()
        pure_builder.assert_awaited_once_with(
            "gtfs", network, 40.73, -73.99, {"Q"}
        )

    async def test_agent_event_adapter_wraps_neutral_provider_result(self):
        neutral = event_provider.EventLookupResult(
            ok=True,
            data={"events": []},
            summary="no events",
        )
        with patch.object(
            event_provider,
            "lookup_events",
            new=AsyncMock(return_value=neutral),
        ) as lookup:
            result = await check_transit.execute_event_lookup(
                {"query": "concert"}, object()
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"events": []})
        self.assertEqual(result.summary, "no events")
        lookup.assert_awaited_once()

    async def test_event_crowd_uses_neutral_provider_failure_without_agent_import(self):
        route = [[
            {
                "type": "SUBWAY",
                "route_id": "Q",
                "departure_stop": "Church Av",
                "arrival_stop": "57 St-7 Av",
                "departure_coords": {"latitude": 40.65, "longitude": -73.96},
                "arrival_coords": {"latitude": 40.765, "longitude": -73.98},
                "departure_time_iso": "2026-07-25T20:00:00-04:00",
                "arrival_time_iso": "2026-07-25T20:40:00-04:00",
            }
        ]]
        with patch.object(
            event_provider,
            "lookup_events",
            new=AsyncMock(
                return_value=event_provider.EventLookupResult(
                    ok=False, error="event lookup timed out"
                )
            ),
        ) as lookup:
            status, impacts, failures = await event_crowd.collect_route_event_evidence(
                route,
                type("Context", (), {"now_et": "2026-07-25T19:15:00-04:00"})(),
            )

        self.assertEqual(status, "provider_unavailable")
        self.assertEqual(impacts, [])
        self.assertEqual(failures, ["event lookup timed out"] * lookup.await_count)
        self.assertGreater(lookup.await_count, 0)


if __name__ == "__main__":
    unittest.main()
