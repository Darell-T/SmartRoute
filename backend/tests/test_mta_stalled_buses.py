from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.mta import bus


def _vehicle_payload(route_id: str) -> dict:
    return {
        "Siri": {
            "ServiceDelivery": {
                "VehicleMonitoringDelivery": [
                    {
                        "VehicleActivity": [
                            {
                                "RecordedAtTime": "2026-08-30T12:00:00-04:00",
                                "MonitoredVehicleJourney": {
                                    "LineRef": f"MTA NYCT_{route_id}",
                                    "ProgressRate": "noProgress",
                                    "ProgressStatus": [],
                                    "VehicleLocation": {
                                        "Latitude": 40.7,
                                        "Longitude": -73.9,
                                    },
                                },
                            }
                        ]
                    }
                ]
            }
        }
    }


class StalledBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_lookup_sends_each_route_to_the_bustime_vehicle_feed(self):
        calls: list[tuple[str, dict]] = []

        class Response:
            def json(self) -> dict:
                return _vehicle_payload("M15")

        class Client:
            async def get(self, url: str, *, params: dict):
                calls.append((url, params))
                return Response()

        async def client() -> Client:
            return Client()

        with (
            patch.object(bus.bus_runtime, "bus_client", new=client),
            patch.dict(os.environ, {"MTA_BUS_API_KEY": "bus-test-key"}, clear=False),
        ):
            stalled = await bus.get_stalled_buses({"M15"})

        assert stalled[0]["route_id"] == "M15"
        assert calls == [
            (
                bus.BUS_URL,
                {
                    "key": "bus-test-key",
                    "version": 2,
                    "LineRef": "M15",
                },
            )
        ]

    async def test_missing_bus_key_returns_an_explicit_unsupported_result(self):
        with patch.dict(os.environ, {"MTA_BUS_API_KEY": ""}, clear=False):
            stops, metadata = await bus.fetch_nearby_bus_stops(40.7, -73.9)

        assert stops == []
        assert metadata == {
            "bus_arrivals_supported": False,
            "reason": "missing_mta_bus_api_key",
        }

    async def test_one_provider_failure_does_not_hide_a_sibling_stalled_bus(self):
        async def fetch(route_id: str) -> dict:
            if route_id == "B41":
                raise TimeoutError("provider timeout")
            return _vehicle_payload(route_id)

        with patch.object(bus, "fetch_bus_positions", new=fetch):
            stalled = await bus.get_stalled_buses({"B41", "M15"})

        assert stalled == [
            {
                "route_id": "M15",
                "location": {"Latitude": 40.7, "Longitude": -73.9},
                "time_recorded": "2026-08-30T12:00:00-04:00",
            }
        ]

    async def test_empty_route_set_avoids_provider_work(self):
        async def unexpected_fetch(_route_id: str) -> dict:
            raise AssertionError("empty input must not call the provider")

        with patch.object(bus, "fetch_bus_positions", new=unexpected_fetch):
            assert await bus.get_stalled_buses(set()) == []


if __name__ == "__main__":
    unittest.main()
