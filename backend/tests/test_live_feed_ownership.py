import asyncio
import gc
import importlib
import json
import time
import unittest
import weakref
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from app import main as app_main
from app.services.live_feed import network_snapshot as network_snapshot_module
from app.services.live_feed import snapshot as rider_snapshot
from app.services.live_feed.network_snapshot import (
    NetworkSnapshot,
    NetworkSnapshotStore,
)
from app.services.mta import bus, bus_runtime, bus_updates
from fastapi import WebSocketDisconnect

live_feed_router = importlib.import_module("app.routers.live_feed.router")
live_feed_socket = importlib.import_module("app.routers.live_feed.socket")


class TemporaryProviderError(RuntimeError):
    pass


class ForbiddenTripContextError(AssertionError):
    pass


class AlreadyClosingError(RuntimeError):
    def __init__(self, code: int) -> None:
        super().__init__(f"already closing ({code})")


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
            assert record["route_ids"] == ("Q",)
            assert record["stop_ids"] == ("Q01",)
            assert json.loads(json.dumps(dict(record)))["route_ids"] == ["Q"]


class NetworkSnapshotStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_snapshot_fetches_do_not_write_provider_cache(self):
        expected = network_snapshot(1)
        with patch.object(
            network_snapshot_module,
            "fetch_feeds_with_metadata",
            new=AsyncMock(return_value=[{"content": b"feed"}]),
        ) as feeds, patch.object(
            network_snapshot_module,
            "fetch_service_alerts",
            new=AsyncMock(return_value=b"alerts"),
        ) as alerts, patch.object(
            network_snapshot_module,
            "_normalize_network_data",
            return_value=expected,
        ):
            result = await network_snapshot_module.build_network_snapshot(1)

        assert result is expected
        feeds.assert_awaited_once_with(
            network_snapshot_module.ALL_SUBWAY_ROUTES,
            "network_snapshot",
            force_refresh=True,
            cache_result=False,
        )
        alerts.assert_awaited_once_with(force_refresh=True, cache_result=False)

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
        assert calls == 1
        assert active_builds == 1
        release.set()
        results = await asyncio.gather(*callers)

        assert all(result is results[0] for result in results)
        assert active_builds == 0
        assert peak_active_builds == 1

    async def test_failure_preserves_current_and_next_refresh_retries(self):
        attempts = 0

        async def builder(generation):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise TemporaryProviderError
            return network_snapshot(generation)

        store = NetworkSnapshotStore(builder)
        first = await store.refresh()
        with pytest.raises(RuntimeError):
            await store.refresh()
        assert store.current is first

        recovered = await store.refresh()
        assert store.current is recovered
        assert recovered.generation > first.generation
        assert attempts == 3

    async def test_store_retains_only_current_generation_and_one_signal(self):
        async def builder(generation):
            return network_snapshot(generation)

        store = NetworkSnapshotStore(builder)
        first_signal = store.refresh_event()
        first = await store.refresh()
        first_ref = weakref.ref(first)
        second_signal = store.refresh_event()
        second = await store.refresh()

        assert first_signal.is_set()
        assert second_signal.is_set()
        assert not store.refresh_event().is_set()
        assert store.current is second
        assert not any("history" in key for key in store.__dict__)

        del first
        await asyncio.sleep(0)
        gc.collect()
        assert first_ref() is None

    async def test_slow_consumer_reads_latest_without_accumulated_updates(self):
        generations = []

        async def builder(generation):
            generations.append(generation)
            return network_snapshot(generation)

        store = NetworkSnapshotStore(builder)
        consumer_signal = store.refresh_event()
        for _ in range(5):
            await store.refresh()

        assert consumer_signal.is_set()
        assert store.current.generation == 5
        assert generations == [1, 2, 3, 4, 5]
        assert not any(isinstance(value, asyncio.Queue) for value in store.__dict__.values())

    async def test_stale_first_request_refreshes_and_records_demand(self):
        calls = 0

        async def builder(generation):
            nonlocal calls
            calls += 1
            return network_snapshot(generation)

        store = NetworkSnapshotStore(builder)
        store._current = network_snapshot(1, updated_at=int(time.time()) - 60)

        refreshed = await store.get_or_refresh(max_age_seconds=30)

        assert calls == 1
        assert refreshed.generation == 1
        assert store.has_recent_demand(45)

    async def test_fresh_request_reuses_current_without_building(self):
        builder = AsyncMock()
        store = NetworkSnapshotStore(builder)
        current = network_snapshot(4)
        store._current = current

        result = await store.get_or_refresh(max_age_seconds=30)

        assert result is current
        builder.assert_not_awaited()
        assert store.has_recent_demand(45)


class RealtimeWarmLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_loop_does_not_refresh_network(self):
        store = SimpleNamespace(
            has_recent_demand=Mock(return_value=False),
            refresh=AsyncMock(),
        )
        sleep = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch.object(app_main, "network_snapshot_store", store),
            patch.object(
                app_main.asyncio,
                "sleep",
                new=sleep,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await app_main._realtime_warm_loop()

        store.has_recent_demand.assert_called_once_with(
            app_main.REALTIME_ACTIVE_WINDOW_S
        )
        store.refresh.assert_not_awaited()
        sleep.assert_awaited_once_with(app_main.REALTIME_REFRESH_INTERVAL_S)

    async def test_active_loop_refreshes_once_before_sleeping(self):
        store = SimpleNamespace(
            has_recent_demand=Mock(return_value=True),
            refresh=AsyncMock(return_value=network_snapshot(1)),
        )

        with (
            patch.object(app_main, "network_snapshot_store", store),
            patch.object(
                app_main.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await app_main._realtime_warm_loop()

        store.refresh.assert_awaited_once_with()

    async def test_decode_error_does_not_kill_the_warm_loop(self):
        from google.protobuf.message import DecodeError

        store = SimpleNamespace(
            has_recent_demand=Mock(return_value=True),
            refresh=AsyncMock(side_effect=DecodeError("truncated")),
        )

        with (
            patch.object(app_main, "network_snapshot_store", store),
            patch.object(
                app_main.asyncio,
                "sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await app_main._realtime_warm_loop()

        store.refresh.assert_awaited_once_with()


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
                raise ForbiddenTripContextError

        with patch.object(
            rider_snapshot.mta_realtime,
            "cached_nearby_bus_update",
            return_value=None,
        ):
            result = await rider_snapshot._build_live_snapshot(
                GTFS(), network, 40.73, -73.99
            )

        assert result["vehicles"][0]["route_name"] == "Q train"
        assert "route_name" not in shared_vehicle
        assert network.generation == 7

    def test_realtime_trip_context_is_ordered_and_uses_static_stop_facts(self):
        updates = (
            {"trip_id": "trip-1", "stop_id": "Q02N", "stop_sequence": 2},
            {"trip_id": "trip-1", "stop_id": "Q01N", "stop_sequence": 1},
            {"trip_id": "other", "stop_id": "R01S", "stop_sequence": 1},
        )
        locations = {
            "Q01": {
                "stop_name": "Canal St",
                "lat": 40.72,
                "lng": -74.0,
                "parent_station": "Q01",
            },
            "Q02": {
                "stop_name": "Times Sq",
                "lat": 40.75,
                "lng": -73.99,
                "parent_station": "Q02",
            },
        }

        context = rider_snapshot._realtime_trip_stop_context(
            updates,
            {"trip-1"},
            locations,
        )

        assert [stop["stop_name"] for stop in context["trip-1"]] == ["Canal St", "Times Sq"]
        assert "other" not in context

    def test_vehicle_segment_skips_incomplete_static_coordinates(self):
        vehicle = {
            "trip_id": "trip-1",
            "stop_id": "Q02N",
            "status": "IN_TRANSIT_TO",
        }
        stops = [
            {"stop_id": "Q01N", "stop_name": "Known", "lat": None, "lng": None},
            {"stop_id": "Q02N", "stop_name": "Target", "lat": 40.7, "lng": -73.9},
        ]

        attached = rider_snapshot.vehicle_enrichment._attach_trip_segment(
            vehicle,
            stops,
            {},
            1_000,
        )

        assert not attached
        assert "lat" not in vehicle


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
            assert calls == 1
            release.set()
            left, right = await asyncio.gather(first, second)

        assert left["status"] == "ready"
        assert right["status"] == "ready"

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

        assert update["status"] == "cached"
        assert update["arrivals"][0]["route_id"] == "B35"

    async def test_stale_bus_fallback_expires_and_is_removed(self):
        cache_key = bus_updates._arrival_cache_key(40.7, -73.9, 804.672, 10, 4)
        bus_runtime.set_cached(
            bus_runtime.nearby_arrivals_cache,
            cache_key,
            {"arrivals": [], "fetched_at": 1, "status": "ready", "debug": {}},
            ttl_s=-bus_updates.BUS_UPDATE_MAX_STALE_S - 1,
            stale_ttl_s=bus_updates.BUS_UPDATE_MAX_STALE_S,
        )

        assert bus_runtime.get_last_cached(bus_runtime.nearby_arrivals_cache, cache_key) is None
        assert cache_key not in bus_runtime.nearby_arrivals_cache

    async def test_bus_cache_lru_capacity_evicts_the_oldest_key(self):
        store = bus_runtime.BoundedCache(2)
        bus_runtime.set_cached(store, "first", {"id": 1}, 60)
        bus_runtime.set_cached(store, "second", {"id": 2}, 60)
        assert bus_runtime.get_cached(store, "first") == {"id": 1}
        bus_runtime.set_cached(store, "third", {"id": 3}, 60)

        assert "second" not in store
        assert set(store) == {"first", "third"}

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
            assert calls == 1
            release.set()
            await asyncio.gather(first, second)

        assert calls == 1

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
            assert calls == 1
            release.set()
            await asyncio.gather(first, second)

        assert calls == 1

    async def test_stops_for_route_uses_eight_second_timeout(self):
        captured = {}

        async def get(*_args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            raise httpx.ConnectError("offline")

        client = SimpleNamespace(get=get)
        with patch.object(bus, "_bus_api_key", return_value="bus-key"), patch.object(
            bus, "cache_get", return_value=None
        ), patch.object(
            bus_runtime, "bus_client", AsyncMock(return_value=client)
        ):
            result = await bus.fetch_bus_route_stop_groups("M15")

        assert result is None
        assert captured["timeout"] == 8.0
        assert captured["timeout"] >= bus_runtime.BUS_REQUEST_TIMEOUT_S

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
            live_feed_router._live_feed_snapshot,
            "build_live_snapshot",
            AsyncMock(return_value=snapshot),
        ), patch.object(live_feed_router.mta_realtime, "fetch_nearby_bus_update", refresh):
            response = await live_feed_router._live_feed_impl(object(), payload)
            assert response.status_code == 200
            refresh.assert_not_awaited()
            await asyncio.sleep(0)

        refresh.assert_awaited_once()


class SocketOwnershipTests(unittest.IsolatedAsyncioTestCase):
    class Socket:
        query_params: ClassVar[dict] = {"ticket": "valid"}
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

            async def receive(_socket, pending=messages):
                value = next(pending)
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
        assert set(asyncio.all_tasks()) == baseline
        assert release.await_count == 20
        assert all(socket.accepted == 1 for socket in sockets)
        assert all(socket.close_codes == [] for socket in sockets)

    async def test_auth_rejection_starts_no_connection_tasks(self):
        baseline = set(asyncio.all_tasks())
        socket = self.Socket()
        deps = self.dependencies(
            AsyncMock(),
            verify=AsyncMock(return_value=(None, False)),
        )

        await live_feed_socket.stream_live_feed(socket, 1, deps)
        await asyncio.sleep(0)

        assert socket.accepted == 0
        assert socket.close_codes == [1008]
        assert set(asyncio.all_tasks()) == baseline

    async def test_refresh_event_pushes_a_new_snapshot_without_reconnecting(self):
        refresh_event = asyncio.Event()
        disconnect = asyncio.Event()
        receive_count = 0
        snapshot_count = 0

        async def receive(_socket):
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {"type": "location", "lat": 40.7, "lng": -73.9}
            await disconnect.wait()
            raise WebSocketDisconnect(code=1000)

        async def snapshot(*_args, **_kwargs):
            nonlocal snapshot_count, refresh_event
            snapshot_count += 1
            if snapshot_count == 1:
                previous = refresh_event
                refresh_event = asyncio.Event()
                previous.set()
            return {
                "nearest_stop": None,
                "arrivals": [],
                "vehicles": [],
                "degraded": False,
                "bus_status": "cached",
                "debug": {"network_generation": snapshot_count},
            }

        sent = []

        async def send(_socket, payload):
            sent.append(payload)
            if payload.get("type") == "snapshot" and len(sent) > 1:
                disconnect.set()
            return True

        socket = self.Socket()
        deps = replace(
            self.dependencies(receive),
            refresh_event=lambda: refresh_event,
            snapshot=snapshot,
            send=send,
        )
        await asyncio.wait_for(
            live_feed_socket.stream_live_feed(socket, 1, deps),
            timeout=2,
        )

        snapshots = [message for message in sent if message.get("type") == "snapshot"]
        assert [message["data"]["debug"]["network_generation"] for message in snapshots] == [1, 2]
        assert socket.accepted == 1

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

        assert socket.close_codes == [1013]

    async def test_close_helper_is_safe_when_cleanup_runs_twice(self):
        class AlreadyClosingSocket:
            def __init__(self):
                self.calls = 0

            async def close(self, code):
                self.calls += 1
                if self.calls > 1:
                    raise AlreadyClosingError(code)

        socket = AlreadyClosingSocket()
        await live_feed_socket.close_socket_safe(socket, 1000, WebSocketDisconnect)
        await live_feed_socket.close_socket_safe(socket, 1000, WebSocketDisconnect)
        assert socket.calls == 2

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

        assert cancelled.is_set()

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

        assert first_cancelled.is_set()
        bus_messages = [
            call.args[1]
            for call in deps.send.await_args_list
            if call.args[1].get("type") == "bus_update"
        ]
        assert [message["data"]["generation"] for message in bus_messages] == [2]
        assert set(bus_messages[0]["data"]) == {"generation", "arrivals", "fetched_at", "status"}

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
        assert [message["data"]["generation"] for message in bus_messages] == [2]
        assert bus_messages[0]["data"]["arrivals"] == [{"route_id": "B2"}]

    async def test_snapshot_decode_error_sends_unavailable_and_keeps_the_socket(self):
        from google.protobuf.message import DecodeError

        frames = iter(
            [
                {"type": "location", "lat": 40.7, "lng": -73.9},
                WebSocketDisconnect(code=1000),
            ]
        )

        async def receive(_socket):
            value = next(frames)
            if isinstance(value, Exception):
                raise value
            return value

        deps = self.dependencies(receive)
        deps = replace(
            deps,
            snapshot=AsyncMock(side_effect=DecodeError("truncated")),
        )
        with patch.object(live_feed_socket.asyncio, "sleep", AsyncMock()):
            await live_feed_socket.stream_live_feed(self.Socket(), 1, deps)

        error_messages = [
            call.args[1]
            for call in deps.send.await_args_list
            if call.args[1].get("type") == "error"
        ]
        assert error_messages[0]["message"] == "live feed temporarily unavailable"


if __name__ == "__main__":
    unittest.main()
