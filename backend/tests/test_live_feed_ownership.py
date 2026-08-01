import asyncio
import gc
import json
import time
import unittest
import weakref
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect

from app.routers import live_feed_socket
from app.services.live_feed import network_snapshot as network_snapshot_module
from app.services.live_feed import snapshot as rider_snapshot
from app.services.live_feed.network_snapshot import NetworkSnapshot, NetworkSnapshotStore


def network_snapshot(generation: int, **overrides) -> NetworkSnapshot:
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


class NetworkSnapshotNormalizationTests(unittest.TestCase):
    def test_known_alert_collections_are_immutable_and_json_serializable(self):
        alert = {
            "alert_id": "alert-1",
            "route_ids": ["Q"],
            "stop_ids": ["Q01"],
        }
        with patch.object(
            network_snapshot_module,
            "parse_bytes",
            return_value=[],
        ), patch.object(
            network_snapshot_module,
            "_build_subway_vehicle_positions",
            return_value=([], {}),
        ), patch.object(
            network_snapshot_module,
            "parse_service_alerts",
            return_value=[alert],
        ), patch.object(
            network_snapshot_module,
            "parse_service_alerts_for_service_board",
            return_value=[alert],
        ):
            snapshot = network_snapshot_module._normalize_network_data(
                [{"content": b"feed"}],
                b"alerts",
                1,
            )

        for record in (*snapshot.alerts, *snapshot.service_alerts):
            self.assertEqual(record["route_ids"], ("Q",))
            self.assertEqual(record["stop_ids"], ("Q01",))
            self.assertEqual(
                json.loads(json.dumps(dict(record)))["route_ids"],
                ["Q"],
            )


class NetworkSnapshotStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_refresh_callers_share_one_build(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        active_builds = 0
        peak_active_builds = 0

        async def builder(generation):
            nonlocal calls, active_builds, peak_active_builds
            calls += 1
            active_builds += 1
            peak_active_builds = max(peak_active_builds, active_builds)
            try:
                entered.set()
                await release.wait()
                return network_snapshot(generation)
            finally:
                active_builds -= 1

        store = NetworkSnapshotStore(builder)
        callers = [asyncio.create_task(store.refresh()) for _ in range(12)]
        await entered.wait()
        self.assertEqual(calls, 1)
        self.assertEqual(active_builds, 1)
        release.set()
        results = await asyncio.gather(*callers)

        self.assertTrue(all(result is results[0] for result in results))
        self.assertEqual(active_builds, 0)
        self.assertEqual(peak_active_builds, 1)

    async def test_failure_preserves_current_and_next_refresh_retries(self):
        attempts = 0

        async def builder(generation):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise RuntimeError("temporary provider failure")
            return network_snapshot(generation)

        store = NetworkSnapshotStore(builder)
        first = await store.refresh()
        with self.assertRaises(RuntimeError):
            await store.refresh()
        self.assertIs(store.current, first)

        recovered = await store.refresh()
        self.assertIs(store.current, recovered)
        self.assertGreater(recovered.generation, first.generation)
        self.assertEqual(attempts, 3)

    async def test_store_retains_only_current_generation_and_one_signal(self):
        async def builder(generation):
            return network_snapshot(generation)

        store = NetworkSnapshotStore(builder)
        first_signal = store.refresh_event()
        first = await store.refresh()
        first_ref = weakref.ref(first)
        second_signal = store.refresh_event()
        second = await store.refresh()

        self.assertTrue(first_signal.is_set())
        self.assertTrue(second_signal.is_set())
        self.assertFalse(store.refresh_event().is_set())
        self.assertIs(store.current, second)
        self.assertFalse(any("history" in key for key in store.__dict__))

        del first
        await asyncio.sleep(0)
        gc.collect()
        self.assertIsNone(first_ref())

    async def test_slow_consumer_reads_latest_without_accumulated_updates(self):
        generations = []

        async def builder(generation):
            generations.append(generation)
            return network_snapshot(generation)

        store = NetworkSnapshotStore(builder)
        consumer_signal = store.refresh_event()
        for _ in range(5):
            await store.refresh()

        self.assertTrue(consumer_signal.is_set())
        self.assertEqual(store.current.generation, 5)
        self.assertEqual(generations, [1, 2, 3, 4, 5])
        self.assertFalse(
            any(
                isinstance(value, asyncio.Queue)
                for value in store.__dict__.values()
            )
        )


class RiderSnapshotSharingTests(unittest.IsolatedAsyncioTestCase):
    async def test_rider_filtering_does_not_mutate_shared_network_records(self):
        shared_vehicle = MappingProxyType({
            "id": "vehicle-1",
            "trip_id": "trip-1",
            "route_id": "Q",
            "lat": 40.73,
            "lng": -73.99,
            "stop_id": "Q01N",
            "position_source": "vehicle_position",
        })
        network = network_snapshot(
            7,
            vehicles=(shared_vehicle,),
            vehicle_debug=MappingProxyType({"vehicle_entities": 1}),
        )

        class GTFS:
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
                return {}

            def get_trip_stop_context(self, _trip_ids):
                return {}

        with patch.object(
            rider_snapshot,
            "_safe_nearby_bus_arrivals",
            AsyncMock(return_value=([], {"bus_arrivals_supported": False})),
        ):
            result = await rider_snapshot._build_live_snapshot(
                GTFS(), network, 40.73, -73.99
            )

        self.assertEqual(result["vehicles"][0]["route_name"], "Q train")
        self.assertNotIn("route_name", shared_vehicle)
        self.assertEqual(network.generation, 7)


class SocketOwnershipTests(unittest.IsolatedAsyncioTestCase):
    class Socket:
        query_params = {"ticket": "valid"}
        url = SimpleNamespace(path="/ws/live-feed")
        app = SimpleNamespace(state=SimpleNamespace(gtfs=object()))

        def __init__(self):
            self.accepted = 0
            self.close_codes = []

        async def accept(self):
            self.accepted += 1

        async def close(self, code):
            self.close_codes.append(code)

    def dependencies(self, receive, *, verify=None, guard=None, release=None):
        async def idle_guard(_lease, stopped, _failed, _owner):
            await stopped.wait()

        return live_feed_socket.LiveFeedSocketDependencies(
            disconnect_error=WebSocketDisconnect,
            admission_denied=RuntimeError,
            acquire=AsyncMock(return_value=object()),
            release=release or AsyncMock(),
            verify=verify or AsyncMock(return_value=("v1.principal", False)),
            guard=guard or idle_guard,
            receive=receive,
            send=AsyncMock(return_value=True),
            refresh_event=asyncio.Event,
            service_payload=AsyncMock(return_value={}),
            alert_signatures=lambda _alerts: {},
            snapshot=AsyncMock(return_value={
                "nearest_stop": None,
                "arrivals": [],
                "vehicles": [],
                "degraded": False,
                "debug": {},
            }),
            normalize=lambda values: set(values or []),
            location_log=lambda *_args: "location",
            failure_log=lambda channel, exc: f"{channel}:{type(exc).__name__}",
            vlog=lambda _message: None,
        )

    async def test_repeated_disconnects_return_tasks_and_connections_to_baseline(self):
        baseline = set(asyncio.all_tasks())
        release = AsyncMock()
        sockets = []

        for connection_id in range(20):
            messages = iter([
                {"type": "location", "lat": 40.7, "lng": -73.9},
                WebSocketDisconnect(code=1000),
            ])

            async def receive(_socket):
                value = next(messages)
                if isinstance(value, Exception):
                    raise value
                return value

            socket = self.Socket()
            sockets.append(socket)
            await live_feed_socket.stream_live_feed(
                socket,
                connection_id,
                self.dependencies(receive, release=release),
            )

        await asyncio.sleep(0)
        self.assertEqual(set(asyncio.all_tasks()), baseline)
        self.assertEqual(release.await_count, 20)
        self.assertTrue(all(socket.accepted == 1 for socket in sockets))
        self.assertTrue(all(socket.close_codes == [] for socket in sockets))

    async def test_auth_rejection_starts_no_connection_tasks(self):
        baseline = set(asyncio.all_tasks())
        socket = self.Socket()
        deps = self.dependencies(
            AsyncMock(),
            verify=AsyncMock(return_value=(None, False)),
        )

        await live_feed_socket.stream_live_feed(socket, 1, deps)
        await asyncio.sleep(0)

        self.assertEqual(socket.accepted, 0)
        self.assertEqual(socket.close_codes, [1008])
        self.assertEqual(set(asyncio.all_tasks()), baseline)

    async def test_lease_failure_closes_once_during_unified_cleanup(self):
        async def failed_guard(_lease, _stopped, lease_failed, owner):
            lease_failed.set()
            owner.cancel()

        async def receive(_socket):
            await asyncio.Event().wait()

        socket = self.Socket()
        await live_feed_socket.stream_live_feed(
            socket,
            1,
            self.dependencies(receive, guard=failed_guard),
        )

        self.assertEqual(socket.close_codes, [1013])

    async def test_close_helper_is_safe_when_cleanup_runs_twice(self):
        class AlreadyClosingSocket:
            def __init__(self):
                self.calls = 0

            async def close(self, code):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("already closing")

        socket = AlreadyClosingSocket()
        await live_feed_socket.close_socket_safe(socket, 1000, WebSocketDisconnect)
        await live_feed_socket.close_socket_safe(socket, 1000, WebSocketDisconnect)
        self.assertEqual(socket.calls, 2)


if __name__ == "__main__":
    unittest.main()
