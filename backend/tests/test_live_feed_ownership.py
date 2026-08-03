import asyncio
import gc
import json
import time
import unittest
import weakref
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect

from app.routers import live_feed as live_feed_router
from app.routers import live_feed_socket
from app.services.live_feed import network_snapshot as network_snapshot_module
from app.services.live_feed import snapshot as rider_snapshot
from app.services.live_feed.network_snapshot import NetworkSnapshot, NetworkSnapshotStore
from app.services.mta import bus, bus_runtime, bus_updates


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
            rider_snapshot.mta_feed,
            "cached_nearby_bus_update",
            return_value=None,
        ):
            result = await rider_snapshot._build_live_snapshot(
                GTFS(), network, 40.73, -73.99
            )

        self.assertEqual(result["vehicles"][0]["route_name"], "Q train")
        self.assertNotIn("route_name", shared_vehicle)
        self.assertEqual(network.generation, 7)


class BusUpdateOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await bus_runtime.close_bus_client()

    async def asyncTearDown(self):
        await bus_runtime.close_bus_client()

    async def test_duplicate_bus_refreshes_share_one_inflight_request(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch(*_args):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return [], {"bus_arrivals_supported": True}

        with patch.object(bus_updates, "_fetch_nearby_bus_arrivals", side_effect=fetch):
            first = asyncio.create_task(bus_updates.fetch_nearby_bus_update(40.7, -73.9))
            second = asyncio.create_task(bus_updates.fetch_nearby_bus_update(40.7, -73.9))
            await entered.wait()
            self.assertEqual(calls, 1)
            release.set()
            left, right = await asyncio.gather(first, second)

        self.assertEqual(left["status"], "ready")
        self.assertEqual(right["status"], "ready")

    async def test_expired_bus_cache_is_used_only_when_refresh_fails(self):
        cache_key = bus_updates._arrival_cache_key(40.7, -73.9, 804.672, 10, 4)
        bus_runtime.set_cached(
            bus_runtime.nearby_arrivals_cache,
            cache_key,
            {
                "arrivals": [{"mode": "bus", "route_id": "B35"}],
                "fetched_at": 1,
                "status": "ready",
                "debug": {"bus_arrivals_supported": True},
            },
            ttl_s=-1,
            stale_ttl_s=bus_updates.BUS_UPDATE_MAX_STALE_S,
        )

        with patch.object(
            bus_updates,
            "_fetch_nearby_bus_arrivals",
            AsyncMock(side_effect=TimeoutError()),
        ):
            update = await bus_updates.fetch_nearby_bus_update(40.7, -73.9)

        self.assertEqual(update["status"], "cached")
        self.assertEqual(update["arrivals"][0]["route_id"], "B35")

    async def test_stale_bus_fallback_expires_and_is_removed(self):
        cache_key = bus_updates._arrival_cache_key(40.7, -73.9, 804.672, 10, 4)
        bus_runtime.set_cached(
            bus_runtime.nearby_arrivals_cache,
            cache_key,
            {"arrivals": [], "fetched_at": 1, "status": "ready", "debug": {}},
            ttl_s=-bus_updates.BUS_UPDATE_MAX_STALE_S - 1,
            stale_ttl_s=bus_updates.BUS_UPDATE_MAX_STALE_S,
        )

        self.assertIsNone(bus_runtime.get_last_cached(bus_runtime.nearby_arrivals_cache, cache_key))
        self.assertNotIn(cache_key, bus_runtime.nearby_arrivals_cache)

    async def test_bus_cache_lru_capacity_evicts_the_oldest_key(self):
        store = bus_runtime.BoundedCache(2)
        bus_runtime.set_cached(store, "first", {"id": 1}, 60)
        bus_runtime.set_cached(store, "second", {"id": 2}, 60)
        self.assertEqual(bus_runtime.get_cached(store, "first"), {"id": 1})
        bus_runtime.set_cached(store, "third", {"id": 3}, 60)

        self.assertNotIn("second", store)
        self.assertEqual(set(store), {"first", "third"})

    async def test_overlapping_stop_monitoring_requests_share_one_request(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def get(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return SimpleNamespace(status_code=200, json=lambda: {"Siri": {}})

        client = SimpleNamespace(get=get)
        with patch.object(bus, "_bus_api_key", return_value="bus-key"), patch.object(
            bus_runtime, "bus_client", AsyncMock(return_value=client)
        ):
            first = asyncio.create_task(bus.fetch_bus_stop_monitoring("MTA NYCT_308209", 4))
            second = asyncio.create_task(bus.fetch_bus_stop_monitoring("308209", 4))
            await entered.wait()
            self.assertEqual(calls, 1)
            release.set()
            await asyncio.gather(first, second)

        self.assertEqual(calls, 1)

    async def test_overlapping_nearby_stop_discovery_shares_one_request(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def get(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return SimpleNamespace(status_code=200, json=lambda: {"data": {"stops": []}})

        client = SimpleNamespace(get=get)
        with patch.object(bus, "_bus_api_key", return_value="bus-key"), patch.object(
            bus_runtime, "bus_client", AsyncMock(return_value=client)
        ):
            first = asyncio.create_task(bus.fetch_nearby_bus_stops(40.7, -73.9, limit=4))
            second = asyncio.create_task(bus.fetch_nearby_bus_stops(40.7, -73.9, limit=10))
            await entered.wait()
            self.assertEqual(calls, 1)
            release.set()
            await asyncio.gather(first, second)

        self.assertEqual(calls, 1)

    async def test_rest_returns_primary_snapshot_before_bus_refresh(self):
        snapshot = {
            "nearest_stop": None,
            "stops": [],
            "arrivals": [],
            "alerts": [],
            "vehicles": [],
            "signals": None,
            "bus_status": "pending",
            "updated_at": 1,
            "degraded": False,
            "debug": {},
        }
        refresh = AsyncMock(return_value={"status": "unavailable", "arrivals": []})
        payload = live_feed_router.LiveFeedRequest(lat=40.7, lng=-73.9)

        with patch.object(
            live_feed_router,
            "_build_live_snapshot",
            AsyncMock(return_value=snapshot),
        ), patch.object(live_feed_router.mta_feed, "fetch_nearby_bus_update", refresh):
            response = await live_feed_router._live_feed_impl(object(), payload)
            self.assertEqual(response.status_code, 200)
            refresh.assert_not_awaited()
            await asyncio.sleep(0)

        refresh.assert_awaited_once()


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
                "bus_status": "pending",
                "debug": {},
            }),
            bus_update=AsyncMock(return_value={
                "arrivals": [], "fetched_at": 0, "status": "unavailable"
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

    async def test_disconnect_cancels_the_socket_owned_bus_waiter(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_bus(_lat, _lng):
            try:
                started.set()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        messages = iter([
            {"type": "location", "lat": 40.7, "lng": -73.9},
            WebSocketDisconnect(code=1000),
        ])

        async def receive(_socket):
            value = next(messages)
            if isinstance(value, Exception):
                await started.wait()
                raise value
            return value

        socket = self.Socket()
        deps = self.dependencies(receive)
        deps = replace(deps, bus_update=slow_bus)
        await live_feed_socket.stream_live_feed(socket, 1, deps)

        self.assertTrue(cancelled.is_set())

    async def test_location_change_discards_the_previous_bus_generation(self):
        first_started = asyncio.Event()
        first_cancelled = asyncio.Event()
        second_started = asyncio.Event()
        release_disconnect = asyncio.Event()
        calls = 0

        async def bus_update(_lat, _lng):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    first_cancelled.set()
                    raise
            second_started.set()
            return {"arrivals": [], "fetched_at": 2, "status": "ready"}

        receive_count = 0

        async def receive(_socket):
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {"type": "location", "lat": 40.7, "lng": -73.9}
            if receive_count == 2:
                await first_started.wait()
                return {"type": "location", "lat": 40.8, "lng": -73.8}
            await second_started.wait()
            await release_disconnect.wait()
            raise WebSocketDisconnect(code=1000)

        async def send(_socket, payload):
            if payload.get("type") == "bus_update":
                release_disconnect.set()
            return True

        socket = self.Socket()
        deps = replace(
            self.dependencies(receive),
            bus_update=bus_update,
            send=AsyncMock(side_effect=send),
        )
        await live_feed_socket.stream_live_feed(socket, 1, deps)

        self.assertTrue(first_cancelled.is_set())
        bus_messages = [
            call.args[1]
            for call in deps.send.await_args_list
            if call.args[1].get("type") == "bus_update"
        ]
        self.assertEqual([message["data"]["generation"] for message in bus_messages], [2])
        self.assertEqual(
            set(bus_messages[0]["data"]),
            {"generation", "arrivals", "fetched_at", "status"},
        )

    async def test_same_tick_location_change_discards_completed_old_bus_update(self):
        """A received location frame wins over a simultaneously completed bus task."""
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_finished = asyncio.Event()
        release_disconnect = asyncio.Event()
        calls = 0

        async def bus_update(_lat, _lng):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await release_first.wait()
                return {"arrivals": [{"route_id": "B1"}], "fetched_at": 1, "status": "ready"}
            second_finished.set()
            return {"arrivals": [{"route_id": "B2"}], "fetched_at": 2, "status": "ready"}

        receive_count = 0

        async def receive(_socket):
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {"type": "location", "lat": 40.7, "lng": -73.9}
            if receive_count == 2:
                await first_started.wait()
                release_first.set()
                await asyncio.sleep(0)
                return {"type": "location", "lat": 40.8, "lng": -73.8}
            await second_finished.wait()
            await release_disconnect.wait()
            raise WebSocketDisconnect(code=1000)

        async def send(_socket, payload):
            if payload.get("type") == "bus_update":
                release_disconnect.set()
            return True

        socket = self.Socket()
        deps = replace(
            self.dependencies(receive),
            bus_update=bus_update,
            send=AsyncMock(side_effect=send),
        )
        await live_feed_socket.stream_live_feed(socket, 1, deps)

        bus_messages = [
            call.args[1]
            for call in deps.send.await_args_list
            if call.args[1].get("type") == "bus_update"
        ]
        self.assertEqual([message["data"]["generation"] for message in bus_messages], [2])
        self.assertEqual(bus_messages[0]["data"]["arrivals"], [{"route_id": "B2"}])


if __name__ == "__main__":
    unittest.main()
