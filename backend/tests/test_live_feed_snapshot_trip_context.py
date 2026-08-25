"""Live Map snapshots use the shared realtime snapshot, never static I/O."""

from __future__ import annotations

import time
import unittest
from types import MappingProxyType
from unittest.mock import patch

from app.services.live_feed import snapshot as rider_snapshot
from app.services.live_feed.network_snapshot import NetworkSnapshot


TRIP_STOPS = [{
    "stop_id": "Q01N",
    "stop_sequence": 1,
    "stop_name": "Canal St",
    "lat": 40.73,
    "lng": -73.99,
    "parent_station": "Q01",
}]


def _network_snapshot(generation: int = 1, **overrides) -> NetworkSnapshot:
    values = {
        "generation": generation,
        "updated_at": int(time.time()),
        "trip_updates": (),
        "arrival_lookup": MappingProxyType({}),
        "vehicles": (),
        "vehicle_debug": MappingProxyType({}),
        "alerts": (),
        "service_alerts": (),
        "feed_count": 1,
    }
    values.update(overrides)
    return NetworkSnapshot(**values)


class StaticLookupGuardGTFS:
    def get_all_parent_stops(self):
        return [{
            "stop_id": "Q01",
            "stop_name": "Canal St",
            "stop_lat": 40.73,
            "stop_lon": -73.99,
        }]

    def get_route_ids_for_parent_stop(self, _stop_id):
        return ["Q"]

    def get_child_stop_ids(self, _stop_id):
        return ["Q01N", "Q01S"]

    def get_stop_locations(self, _stop_ids):
        return {
            "Q01": {
                "stop_name": "Canal St",
                "lat": 40.73,
                "lng": -73.99,
                "parent_station": "",
            },
            "Q01N": {
                "stop_name": "Canal St",
                "lat": 40.73,
                "lng": -73.99,
                "parent_station": "Q01",
            },
        }

    def get_trip_stop_context(self, trip_ids):
        raise AssertionError(f"rider snapshot attempted static trip lookup: {trip_ids}")


def _without_timing(payload):
    """Snapshot payload with the timing-only debug fields removed."""
    debug = dict(payload["debug"])
    debug.pop("arrivals_ms", None)
    debug.pop("build_ms", None)
    out = dict(payload)
    out["debug"] = debug
    return out


class SnapshotTripContextMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def _build(self, gtfs, network):
        with patch.object(
            rider_snapshot.mta_realtime,
            "cached_nearby_bus_update",
            return_value=None,
        ):
            return await rider_snapshot._build_live_snapshot(gtfs, network, 40.73, -73.99)

    async def test_trip_stop_context_uses_shared_realtime_snapshot(self):
        now = int(time.time())
        gtfs = StaticLookupGuardGTFS()
        network = _network_snapshot(
            7,
            trip_updates=(
                {
                    "trip_id": "trip-1",
                    "stop_id": "Q01N",
                    "stop_sequence": 1,
                    "arrival_time": now + 240,
                    "route_id": "Q",
                },
                {
                    "trip_id": "trip-2",
                    "stop_id": "Q01N",
                    "stop_sequence": 1,
                    "arrival_time": now + 300,
                    "route_id": "Q",
                },
            ),
            vehicles=(
                MappingProxyType({
                    "id": "vehicle-1",
                    "trip_id": "trip-1",
                    "route_id": "Q",
                    "lat": 40.73,
                    "lng": -73.99,
                    "stop_id": "Q01N",
                    "position_source": "vehicle_position",
                }),
            ),
            vehicle_debug=MappingProxyType({"vehicle_entities": 1}),
        )
        result = await self._build(gtfs, network)

        # Contract unchanged: terminal/segment enrichment still applies.
        vehicle = result["vehicles"][0]
        self.assertEqual(vehicle["route_name"], "Q train")
        self.assertEqual(vehicle["terminal_stop_id"], "Q01N")
        self.assertEqual(vehicle["terminal_stop_name"], "Canal St")

        arrival = next(
            item for item in result["arrivals"] if item["trip_id"] == "trip-2"
        )
        self.assertEqual(arrival["trip_id"], "trip-2")
        self.assertEqual(arrival["parent_stop_id"], "Q01")
        self.assertEqual(arrival["terminal_stop_id"], "Q01N")
        self.assertEqual(arrival["terminal_stop_name"], "Canal St")
        self.assertEqual(result["updated_at"], network.updated_at)

    async def test_repeated_builds_from_one_snapshot_are_equivalent(self):
        now = int(time.time())
        network = _network_snapshot(
            3,
            trip_updates=(
                {
                    "trip_id": "trip-1",
                    "stop_id": "Q01N",
                    "stop_sequence": 1,
                    "arrival_time": now + 240,
                    "route_id": "Q",
                },
            ),
            vehicles=(
                MappingProxyType({
                    "id": "vehicle-1",
                    "trip_id": "trip-1",
                    "route_id": "Q",
                    "lat": 40.73,
                    "lng": -73.99,
                    "stop_id": "Q01N",
                    "position_source": "vehicle_position",
                }),
            ),
            vehicle_debug=MappingProxyType({"vehicle_entities": 1}),
        )

        first = await self._build(StaticLookupGuardGTFS(), network)
        second = await self._build(StaticLookupGuardGTFS(), network)

        self.assertEqual(_without_timing(first), _without_timing(second))


if __name__ == "__main__":
    unittest.main()
