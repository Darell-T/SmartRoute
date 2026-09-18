from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from app.services.mta import bus, bus_runtime, bus_updates
from app.services.mta.config import NYC_TZ

NOW = 1_800_000_000
STOP_NEAR = {
    "stop_id": "MTA_100",
    "stop_name": "Near",
    "distance_m": 10.0,
    "stop_lat": 40.75,
    "stop_lon": -73.98,
    "stop_compass": "N",
}
STOP_FAR = {
    "stop_id": "MTA_200",
    "stop_name": "Far",
    "distance_m": 80.0,
    "stop_lat": 40.76,
    "stop_lon": -73.97,
    "stop_compass": "S",
}


def _siri(stop_id: str, arrival_epoch: int, route: str = "M15") -> dict:
    iso = datetime.fromtimestamp(arrival_epoch, tz=NYC_TZ).isoformat()
    return {
        "Siri": {
            "ServiceDelivery": {
                "StopMonitoringDelivery": {
                    "MonitoredStopVisit": {
                        "MonitoredVehicleJourney": {
                            "PublishedLineName": route,
                            "FramedVehicleJourneyRef": {
                                "DatedVehicleJourneyRef": f"trip-{stop_id}-{arrival_epoch}",
                            },
                            "DirectionRef": "0",
                            "DestinationName": "South Ferry",
                            "MonitoredCall": {
                                "ExpectedArrivalTime": iso,
                                "StopPointRef": stop_id,
                            },
                        }
                    }
                }
            }
        }
    }


class NearbyBusArrivalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await bus_runtime.close_bus_client()

    async def asyncTearDown(self):
        await bus_runtime.close_bus_client()

    async def _collect(self, stops, payloads, debug=None):
        monitoring_calls = []
        bus_runtime.nearby_arrivals_cache.clear()

        async def fake_stops(*_args, **_kwargs):
            return list(stops), dict(debug or {"bus_arrivals_supported": True})

        async def fake_monitoring(stop_id, visits):
            monitoring_calls.append((stop_id, visits))
            result = payloads[stop_id]
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(bus_updates, "fetch_nearby_bus_stops", fake_stops), patch.object(
            bus_updates, "fetch_bus_stop_monitoring", fake_monitoring
        ), patch.object(bus_updates, "datetime") as mocked_dt:
            mocked_dt.now.return_value.timestamp.return_value = NOW
            update = await bus_updates.fetch_nearby_bus_update(
                40.75, -73.99, 804.672, 10, 4
            )
        return update["arrivals"], update["debug"], monitoring_calls

    async def test_empty_stops_return_zero_arrivals_without_monitoring_or_failures(self):
        arrivals, debug, calls = await self._collect([], {})
        assert arrivals == []
        assert debug["bus_arrival_count"] == 0
        assert "bus_stop_monitoring_failures" not in debug
        assert calls == []

    async def test_exception_increments_failures_empty_payload_does_not_and_siblings_remain(self):
        arrivals, debug, calls = await self._collect(
            [STOP_NEAR, STOP_FAR],
            {
                STOP_NEAR["stop_id"]: RuntimeError("timeout"),
                STOP_FAR["stop_id"]: _siri(STOP_FAR["stop_id"], NOW + 120),
            },
        )
        assert [row["parent_stop_id"] for row in arrivals] == [STOP_FAR["stop_id"]]
        assert debug["bus_stop_monitoring_failures"] == 1
        assert debug["bus_arrival_count"] == 1
        assert [stop_id for stop_id, _visits in calls] == [
            STOP_NEAR["stop_id"],
            STOP_FAR["stop_id"],
        ]

        silent, silent_debug, _calls = await self._collect(
            [STOP_NEAR, STOP_FAR],
            {
                STOP_NEAR["stop_id"]: {},
                STOP_FAR["stop_id"]: _siri(STOP_FAR["stop_id"], NOW + 90, route="M14"),
            },
        )
        assert [row["route_id"] for row in silent] == ["M14"]
        assert silent_debug["bus_stop_monitoring_failures"] == 0

    async def test_expired_arrivals_drop_and_boundary_at_now_minus_60_is_kept(self):
        arrivals, _debug, _calls = await self._collect(
            [STOP_NEAR, STOP_FAR],
            {
                STOP_NEAR["stop_id"]: _siri(STOP_NEAR["stop_id"], NOW - 61),
                STOP_FAR["stop_id"]: _siri(STOP_FAR["stop_id"], NOW - 60),
            },
        )
        assert [row["arrival_time"] for row in arrivals] == [NOW - 60]

    async def test_equal_arrival_times_preserve_distance_sorted_stop_order(self):
        arrivals, _debug, _calls = await self._collect(
            [STOP_NEAR, STOP_FAR],
            {
                STOP_NEAR["stop_id"]: _siri(STOP_NEAR["stop_id"], NOW + 30, route="M15"),
                STOP_FAR["stop_id"]: _siri(STOP_FAR["stop_id"], NOW + 30, route="M14"),
            },
        )
        assert [row["route_id"] for row in arrivals] == ["M15", "M14"]
        later_first = await self._collect(
            [STOP_NEAR, STOP_FAR],
            {
                STOP_NEAR["stop_id"]: _siri(STOP_NEAR["stop_id"], NOW + 90, route="M15"),
                STOP_FAR["stop_id"]: _siri(STOP_FAR["stop_id"], NOW + 10, route="M14"),
            },
        )
        assert [row["route_id"] for row in later_first[0]] == ["M14", "M15"]

    async def test_empty_visits_with_stops_are_ready_and_missing_key_is_unavailable(self):
        async def stops_found(*_args, **_kwargs):
            return [STOP_NEAR], {"bus_arrivals_supported": True}

        async def empty_monitoring(_stop_id, _visits):
            return {}

        with patch.object(bus_updates, "fetch_nearby_bus_stops", stops_found), patch.object(
            bus_updates, "fetch_bus_stop_monitoring", empty_monitoring
        ), patch.object(bus_updates, "datetime") as mocked_dt:
            mocked_dt.now.return_value.timestamp.return_value = NOW
            ready = await bus_updates.fetch_nearby_bus_update(40.75, -73.98)
        assert ready["status"] == "ready"
        assert ready["arrivals"] == []
        assert ready["debug"]["bus_arrivals_supported"] is True
        cached = bus_updates.cached_nearby_bus_update(40.75, -73.98)
        assert cached is not None
        assert cached["status"] == "cached"

        await bus_runtime.close_bus_client()

        async def missing_key(*_args, **_kwargs):
            return [], {
                "bus_arrivals_supported": False,
                "reason": "missing_mta_bus_api_key",
            }

        with patch.object(bus_updates, "fetch_nearby_bus_stops", missing_key):
            unavailable = await bus_updates.fetch_nearby_bus_update(40.74, -73.97)
        assert unavailable["status"] == "unavailable"
        assert unavailable["debug"]["bus_arrivals_supported"] is False
        assert bus_updates.cached_nearby_bus_update(40.74, -73.97) is None

    def test_parse_accepts_dict_or_list_siri_deliveries(self):
        payload = _siri(STOP_NEAR["stop_id"], NOW + 15)
        as_dict = bus.parse_bus_stop_monitoring(payload, STOP_NEAR)
        as_list = bus.parse_bus_stop_monitoring(
            {
                "Siri": {
                    "ServiceDelivery": {
                        "StopMonitoringDelivery": [
                            payload["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"]
                        ]
                    }
                }
            },
            STOP_NEAR,
        )
        assert [row["route_id"] for row in as_dict] == ["M15"]
        assert [row["route_id"] for row in as_list] == ["M15"]
