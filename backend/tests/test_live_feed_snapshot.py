import time
import unittest
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

from app.services.live_feed import snapshot as rider_snapshot
from app.services.live_feed.network_snapshot import NetworkSnapshot

from tests.test_live_feed_stall_issues import stall_alert, stall_pattern_index


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

NOW = 1_700_000_000
CANAL_LAT = 40.73
CANAL_LNG = -73.99


class SnapshotGTFS:
    def __init__(
        self,
        stops,
        routes_by_stop=None,
        children=None,
        locations=None,
        pattern_index=None,
    ):
        self._stops = stops
        self._routes_by_stop = routes_by_stop or {}
        self._children = children or {}
        self._locations = locations or {}
        self._pattern_index = pattern_index

    def get_all_parent_stops(self):
        return self._stops

    def get_route_ids_for_parent_stop(self, stop_id):
        return self._routes_by_stop.get(stop_id, [])

    def get_child_stop_ids(self, stop_id):
        return self._children.get(stop_id, [])

    def get_stop_locations(self, _stop_ids):
        return dict(self._locations)

    def get_trip_stop_context(self, trip_ids):
        raise AssertionError(f"rider snapshot attempted static trip lookup: {trip_ids}")


def canal_gtfs(**overrides):
    values = {
        "stops": [
            {
                "stop_id": "Q01",
                "stop_name": "Canal St",
                "stop_lat": CANAL_LAT,
                "stop_lon": CANAL_LNG,
            }
        ],
        "routes_by_stop": {"Q01": ["Q"]},
        "children": {"Q01": ["Q01N", "Q01S"]},
        "locations": {
            "Q01": {
                "stop_name": "Canal St",
                "lat": CANAL_LAT,
                "lng": CANAL_LNG,
                "parent_station": "",
            },
            "Q01N": {
                "stop_name": "Canal St",
                "lat": CANAL_LAT,
                "lng": CANAL_LNG,
                "parent_station": "Q01",
            },
        },
    }
    values.update(overrides)
    return SnapshotGTFS(**values)


def subway_arrival(trip_id, arrival_time, stop_id="Q01N", route_id="Q"):
    return {
        "trip_id": trip_id,
        "stop_id": stop_id,
        "route_id": route_id,
        "arrival_time": arrival_time,
        "stop_sequence": 1,
    }


def positioned_vehicle(route_id, **extra):
    record = {
        "id": f"vehicle-{route_id}",
        "trip_id": f"trip-{route_id}",
        "route_id": route_id,
        "lat": CANAL_LAT,
        "lng": CANAL_LNG,
        "stop_id": "Q01N",
        "position_source": "vehicle_position",
    }
    record.update(extra)
    return MappingProxyType(record)


async def live_snapshot(gtfs, network, selected=None, bus=None, lat=CANAL_LAT, lng=CANAL_LNG):
    with (
        patch.object(
            rider_snapshot.network_snapshot_store,
            "get_or_refresh",
            AsyncMock(return_value=network),
        ),
        patch.object(
            rider_snapshot.mta_realtime,
            "cached_nearby_bus_update",
            return_value=bus,
        ),
        patch.object(rider_snapshot.time, "time", return_value=NOW),
    ):
        return await rider_snapshot.build_live_snapshot(gtfs, lat, lng, selected)


class NearbyStopFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_radius_search_falls_back_to_unbounded_limit_5_nearest(self):
        gtfs = SnapshotGTFS(
            stops=[
                {
                    "stop_id": "FAR1",
                    "stop_name": "Far Station",
                    "stop_lat": 40.78,
                    "stop_lon": CANAL_LNG,
                }
            ],
            routes_by_stop={"FAR1": ["Q"]},
            children={"FAR1": ["FAR1N"]},
        )
        result = await live_snapshot(gtfs, network_snapshot(1))

        assert result["nearest_stop"]["stop_id"] == "FAR1"
        assert result["stops"][0]["stop_id"] == "FAR1"
        assert result["debug"]["nearby_stop_count"] == 1


class ArrivalFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_subway_arrival_now_minus_61_drops_from_arrivals(self):
        network = network_snapshot(
            1,
            trip_updates=(
                subway_arrival("expired", NOW - 61),
                subway_arrival("fresh", NOW + 30),
            ),
        )
        result = await live_snapshot(canal_gtfs(), network)
        trip_ids = [arrival["trip_id"] for arrival in result["arrivals"]]

        assert "expired" not in trip_ids
        assert "fresh" in trip_ids

    async def test_boundary_subway_arrival_now_minus_60_is_kept(self):
        network = network_snapshot(
            1,
            trip_updates=(subway_arrival("boundary", NOW - 60),),
        )
        result = await live_snapshot(canal_gtfs(), network)

        assert [arrival["trip_id"] for arrival in result["arrivals"]] == ["boundary"]
        assert result["arrivals"][0]["parent_stop_id"] == "Q01"


class BusOverlayTests(unittest.IsolatedAsyncioTestCase):
    async def test_bus_cache_overlay_merges_and_stable_sorts_by_arrival_time(self):
        network = network_snapshot(
            1,
            trip_updates=(subway_arrival("subway-late", NOW + 80),),
        )
        bus = {
            "status": "ready",
            "arrivals": [
                {"mode": "bus", "route_id": "B1", "arrival_time": NOW + 40},
                {"mode": "bus", "route_id": "B2", "arrival_time": NOW + 40},
            ],
            "debug": {"bus_arrivals_supported": True, "bus_arrival_count": 2},
        }
        result = await live_snapshot(canal_gtfs(), network, bus=bus)
        ordered = [
            arrival.get("trip_id") or arrival.get("route_id")
            for arrival in result["arrivals"]
        ]

        assert ordered == ["B1", "B2", "subway-late"]
        assert result["bus_status"] == "ready"

    async def test_bus_cache_overlay_caps_arrivals_at_40(self):
        bus = {
            "status": "ready",
            "arrivals": [
                {"mode": "bus", "route_id": f"B{index}", "arrival_time": NOW + index}
                for index in range(41)
            ],
            "debug": {"bus_arrival_count": 41},
        }
        result = await live_snapshot(canal_gtfs(), network_snapshot(1), bus=bus)
        route_ids = [arrival["route_id"] for arrival in result["arrivals"]]

        assert len(result["arrivals"]) == 40
        assert "B40" not in route_ids
        assert route_ids[-1] == "B39"

    async def test_missing_bus_cache_sets_bus_status_pending(self):
        result = await live_snapshot(canal_gtfs(), network_snapshot(1), bus=None)

        assert result["bus_status"] == "pending"
        assert result["arrivals"] == []
        assert result["debug"]["bus_arrivals_supported"] is False


class VehicleScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_off_route_vehicle_is_dropped_from_snapshot(self):
        network = network_snapshot(
            1,
            vehicles=(
                positioned_vehicle("Q"),
                positioned_vehicle("R"),
            ),
        )
        result = await live_snapshot(canal_gtfs(), network)
        route_ids = [vehicle["route_id"] for vehicle in result["vehicles"]]

        assert route_ids == ["Q"]
        assert "R" not in result["debug"]["vehicle_route_ids"]

    async def test_selected_s_expands_vehicle_scope_to_fs_gs_h(self):
        network = network_snapshot(
            1,
            vehicles=(
                positioned_vehicle("FS"),
                positioned_vehicle("GS"),
                positioned_vehicle("H"),
                positioned_vehicle("R"),
            ),
        )
        result = await live_snapshot(canal_gtfs(), network, selected={"S"})
        route_ids = [vehicle["route_id"] for vehicle in result["vehicles"]]

        assert sorted(route_ids) == ["FS", "GS", "H"]
        assert "R" not in route_ids
        assert set(result["debug"]["vehicle_route_ids"]) >= {"S", "FS", "GS", "H"}

    async def test_selected_route_ids_are_normalized_upper(self):
        result = await live_snapshot(
            canal_gtfs(), network_snapshot(1), selected={"q"}
        )

        assert result["debug"]["selected_route_ids"] == ["Q"]
        assert "Q" in result["debug"]["vehicle_route_ids"]


class AlertProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_off_route_alert_omitted_from_alerts_still_counts_in_signals(self):
        q_stall = stall_alert("Q01", alert_id="q-stall")
        a_delay = {
            "alert_id": "a-delay",
            "header": "A trains delayed",
            "description": "",
            "route_ids": ["A"],
            "stop_ids": ["A01"],
        }
        network = network_snapshot(1, alerts=(q_stall, a_delay))
        result = await live_snapshot(
            canal_gtfs(pattern_index=stall_pattern_index()),
            network,
        )
        alert_ids = [alert["alert_id"] for alert in result["alerts"]]

        assert alert_ids == ["q-stall"]
        assert "a-delay" not in alert_ids
        assert result["signals"]["active_alert_count"] == 2
        assert result["nearby_issues"][0]["id"] == "q-stall"

    async def test_service_alerts_collection_is_not_used_for_rider_alerts_or_issues(self):
        network = network_snapshot(
            1,
            alerts=(),
            service_alerts=(stall_alert("Q01", alert_id="board-only"),),
        )
        result = await live_snapshot(
            canal_gtfs(pattern_index=stall_pattern_index()),
            network,
        )

        assert result["alerts"] == []
        assert result["nearby_issues"] == []
        assert result["signals"]["active_alert_count"] == 0

    async def test_snapshot_payload_includes_contract_keys(self):
        result = await live_snapshot(canal_gtfs(), network_snapshot(1))

        assert set(result) == {
            "nearest_stop",
            "stops",
            "arrivals",
            "alerts",
            "nearby_issues",
            "vehicles",
            "signals",
            "bus_status",
            "updated_at",
            "degraded",
            "debug",
        }
        assert set(result["debug"]) == {
            "network_generation",
            "route_ids",
            "nearest_route_ids",
            "nearby_route_ids",
            "selected_route_ids",
            "vehicle_route_ids",
            "arrival_radius_m",
            "nearby_stop_count",
            "nearby_child_stop_count",
            "bus_arrivals_supported",
            "nearby_bus_stop_count",
            "bus_arrival_count",
            "bus_stop_monitoring_failures",
            "bus_arrivals_reason",
            "feed_count",
            "vehicle_count",
            "vehicle_scope",
            "vehicle_parse",
            "arrivals_ms",
            "build_ms",
        }
        assert result["debug"]["vehicle_scope"] == "nearest_plus_selected"
        assert result["debug"]["arrival_radius_m"] == 804.672
        assert result["degraded"] is False


class LiveSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_alerts_or_stale_network_status_healthy(self):
        result = await live_snapshot(canal_gtfs(), network_snapshot(1))

        assert result["signals"]["network_status"] == "healthy"
        assert result["signals"]["active_alert_count"] == 0
        assert result["signals"]["major_alert_count"] == 0

    async def test_one_alert_network_status_caution(self):
        network = network_snapshot(
            1,
            alerts=(
                MappingProxyType(
                    {"header": "Q trains delayed", "description": "", "route_ids": ["Q"]}
                ),
            ),
        )
        result = await live_snapshot(canal_gtfs(), network)

        assert result["signals"]["network_status"] == "caution"
        assert result["signals"]["active_alert_count"] == 1
        assert result["signals"]["major_alert_count"] == 0

    async def test_two_disruption_keyword_alerts_network_status_disrupted(self):
        network = network_snapshot(
            1,
            alerts=(
                MappingProxyType(
                    {
                        "header": "F service suspended",
                        "description": "",
                        "route_ids": ["F"],
                    }
                ),
                MappingProxyType(
                    {
                        "header": "G trains skip stations",
                        "description": "",
                        "route_ids": ["G"],
                    }
                ),
            ),
        )
        result = await live_snapshot(canal_gtfs(), network)

        assert result["signals"]["network_status"] == "disrupted"
        assert result["signals"]["major_alert_count"] == 2
        assert result["signals"]["affected_route_count"] == 2

    async def test_stale_count_20_network_status_disrupted(self):
        vehicles = tuple(
            positioned_vehicle(
                "Q",
                id=f"stale-{index}",
                trip_id=f"trip-stale-{index}",
                stale=True,
            )
            for index in range(20)
        )
        result = await live_snapshot(canal_gtfs(), network_snapshot(1, vehicles=vehicles))

        assert result["signals"]["network_status"] == "disrupted"
        assert result["signals"]["stale_vehicle_count"] == 20

    async def test_feed_failure_with_half_unpositioned_network_status_disrupted(self):
        network = network_snapshot(
            1,
            vehicle_debug=MappingProxyType(
                {
                    "feed_failures": 1,
                    "vehicle_entities": 10,
                    "vehicles_without_position": 5,
                }
            ),
        )
        result = await live_snapshot(canal_gtfs(), network)

        assert result["signals"]["network_status"] == "disrupted"


class TripStopContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_stop_sequence_preserves_trip_update_order(self):
        network = network_snapshot(
            1,
            trip_updates=(
                subway_arrival("trip-1", NOW + 30, stop_id="Q02N"),
                {
                    "trip_id": "trip-1",
                    "stop_id": "Q01N",
                    "route_id": "Q",
                    "arrival_time": NOW + 60,
                },
            ),
            vehicles=(
                MappingProxyType(
                    {
                        "id": "v1",
                        "trip_id": "trip-1",
                        "route_id": "Q",
                        "stop_id": "Q01N",
                        "status": "IN_TRANSIT_TO",
                        "position_source": "trip_descriptor",
                    }
                ),
            ),
        )
        gtfs = canal_gtfs(
            locations={
                "Q01": {
                    "stop_name": "Canal St",
                    "lat": CANAL_LAT,
                    "lng": CANAL_LNG,
                    "parent_station": "Q01",
                },
                "Q01N": {
                    "stop_name": "Canal St",
                    "lat": CANAL_LAT,
                    "lng": CANAL_LNG,
                    "parent_station": "Q01",
                },
                "Q02N": {
                    "stop_name": "Times Sq",
                    "lat": 40.76,
                    "lng": -73.99,
                    "parent_station": "Q02",
                },
            }
        )
        result = await live_snapshot(gtfs, network)
        vehicle = result["vehicles"][0]

        assert vehicle["segment"]["from_stop_id"] == "Q02N"
        assert vehicle["segment"]["to_stop_id"] == "Q01N"

    async def test_directional_stop_id_uses_rstrip_ns_location_fallback(self):
        network = network_snapshot(
            1,
            trip_updates=(
                subway_arrival("trip-1", NOW + 30, stop_id="Q01N"),
            ),
            vehicles=(
                MappingProxyType(
                    {
                        "id": "v1",
                        "trip_id": "trip-1",
                        "route_id": "Q",
                        "stop_id": "Q01N",
                        "status": "STOPPED_AT",
                        "position_source": "trip_descriptor",
                    }
                ),
            ),
        )
        gtfs = canal_gtfs(
            locations={
                "Q01": {
                    "stop_name": "Canal St",
                    "lat": 40.72,
                    "lng": -74.0,
                    "parent_station": "Q01",
                }
            }
        )
        result = await live_snapshot(gtfs, network)
        vehicle = result["vehicles"][0]

        assert vehicle["stop_name"] == "Canal St"
        assert vehicle["lat"] == 40.72


if __name__ == "__main__":
    unittest.main()
