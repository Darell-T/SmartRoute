import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _load_trips_module():
    fake_fastapi = types.ModuleType("fastapi")

    class _FakeAPIRouter:
        def post(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class _FakeHTTPException(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _FakeRequest:
        pass

    fake_fastapi.APIRouter = _FakeAPIRouter
    fake_fastapi.HTTPException = _FakeHTTPException
    fake_fastapi.Request = _FakeRequest

    fake_pydantic = types.ModuleType("pydantic")

    class _FakeBaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    fake_pydantic.BaseModel = _FakeBaseModel

    fake_directions = types.ModuleType("app.services.directions")

    async def _fake_get_transit_route(*args, **kwargs):
        return {"routes": []}

    def _fake_parse_response(*args, **kwargs):
        return []

    fake_directions.get_transit_route = _fake_get_transit_route
    fake_directions.parse_response = _fake_parse_response

    fake_ai_advisor = types.ModuleType("app.services.ai_advisor")

    async def _fake_stream_recommendation(*args, **kwargs):
        if False:
            yield ""

    fake_ai_advisor.stream_recommendation = _fake_stream_recommendation

    fake_incident_monitor = types.ModuleType("app.services.incident_monitor")

    async def _fake_get_incidents(*args, **kwargs):
        return {"incidents": []}

    fake_incident_monitor.get_incidents = _fake_get_incidents

    fake_bus_routes = types.ModuleType("app.services.bus_routes")

    async def _fake_fetch_bus_route_stop_groups(*args, **kwargs):
        return {}

    def _fake_slice_route_stops(*args, **kwargs):
        return []

    fake_bus_routes.fetch_bus_route_stop_groups = _fake_fetch_bus_route_stop_groups
    fake_bus_routes.slice_route_stops = _fake_slice_route_stops

    fake_mta_feed = types.ModuleType("app.services.mta_feed")

    async def _fake_fetch_service_alerts(*args, **kwargs):
        return []

    async def _fake_get_stalled_buses(*args, **kwargs):
        return []

    async def _fake_get_stalled_trains(*args, **kwargs):
        return []

    def _fake_parse_service_alerts(*args, **kwargs):
        return []

    def _fake_filter_alerts_for_routes(*args, **kwargs):
        return []

    fake_mta_feed.fetch_service_alerts = _fake_fetch_service_alerts
    fake_mta_feed.get_stalled_buses = _fake_get_stalled_buses
    fake_mta_feed.parse_service_alerts = _fake_parse_service_alerts
    fake_mta_feed.filter_alerts_for_routes = _fake_filter_alerts_for_routes
    fake_mta_feed.get_stalled_trains = _fake_get_stalled_trains

    with patch.dict(
        sys.modules,
        {
            "fastapi": fake_fastapi,
            "pydantic": fake_pydantic,
            "app.services.directions": fake_directions,
            "app.services.ai_advisor": fake_ai_advisor,
            "app.services.incident_monitor": fake_incident_monitor,
            "app.services.bus_routes": fake_bus_routes,
            "app.services.mta_feed": fake_mta_feed,
        },
    ):
        for module_name in [
            key
            for key in list(sys.modules)
            if key == "app.routers.trips" or key.startswith("app.services.trips")
        ]:
            sys.modules.pop(module_name, None)
        return importlib.import_module("app.routers.trips")


class TripsIncidentPayloadTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.trips = _load_trips_module()

    async def test_plan_trip_sends_route_incidents_to_claude(self):
        captured = {}
        parsed_route = [
            [
                {
                    "type": "SUBWAY",
                    "route_id": "Q",
                    "departure_stop": "Church Avenue",
                    "arrival_stop": "Atlantic Avenue-Barclays Center",
                    "departure_coords": {"latitude": 40.650, "longitude": -73.963},
                    "arrival_coords": {"latitude": 40.684, "longitude": -73.977},
                }
            ]
        ]
        incident = {
            "location": "Flatbush Avenue",
            "nearby_station": "Church Avenue",
            "severity": "high",
            "description": "Police activity outside the station.",
            "source": "@NYScanner",
        }

        async def fake_stream_recommendation(payload):
            captured["payload"] = payload
            yield "[ROUTE:0] Take the Q."

        async def fake_enrich_route(_gtfs, steps):
            steps[0]["intermediate_stop_locations"] = [
                {"name": "Church Avenue", "lat": 40.650, "lng": -73.963},
                {"name": "Prospect Park", "lat": 40.661, "lng": -73.962},
            ]
            return {"subway_legs": 1, "bus_legs": 0, "subway_with_stops": 1, "bus_with_stops": 0}

        pattern_index = SimpleNamespace(
            get_intermediate_stops_with_coords=lambda *args, **kwargs: (
                [
                    {"name": "Church Avenue", "lat": 40.650, "lng": -73.963},
                    {"name": "Prospect Park", "lat": 40.661, "lng": -73.962},
                ],
                {},
            )
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(gtfs=SimpleNamespace(_pattern_index=pattern_index))
            )
        )
        payload = self.trips.TripRequest(
            origin_lat=40.6501,
            origin_lng=-73.9796,
            destination="Atlantic Avenue-Barclays Center",
        )

        with patch.object(
            self.trips, "get_transit_route", AsyncMock(return_value={"routes": ["unused"]})
        ), patch.object(
            self.trips, "parse_response", return_value=parsed_route
        ), patch.object(
            self.trips, "fetch_service_alerts", AsyncMock(return_value=[])
        ), patch.object(
            self.trips.trip_incidents, "get_incidents", AsyncMock(return_value={"incidents": [incident]})
        ), patch.object(
            self.trips, "get_stalled_trains", AsyncMock(return_value=[])
        ), patch.object(
            self.trips, "get_stalled_buses", AsyncMock(return_value=[])
        ), patch.object(
            self.trips, "parse_service_alerts", return_value=[]
        ), patch.object(
            self.trips, "filter_alerts_for_routes", return_value=[]
        ), patch.object(
            self.trips, "stream_recommendation", fake_stream_recommendation
        ), patch.object(
            self.trips.enrichment, "_enrich_route", fake_enrich_route
        ):
            response = await self.trips.plan_trip(request, payload)

        self.assertEqual(captured["payload"]["incidents"], [incident])
        self.assertEqual(captured["payload"]["planning_mode"], "intelligence")
        self.assertEqual(response["recommendation"], "Take the Q.")
        self.assertNotIn("audio", response)
        self.assertNotIn("incidents", response)
        self.assertNotIn("incidents_pending", response)

    async def test_scan_route_incidents_returns_empty_for_bad_shape(self):
        with patch.object(
            self.trips.trip_incidents,
            "get_incidents",
            AsyncMock(return_value={"incidents": {"bad": "shape"}}),
        ):
            incidents = await self.trips.trip_incidents._scan_route_incidents(["Church Avenue"])

        self.assertEqual(incidents, [])

    async def test_scan_merges_only_related_evidence_before_advisor_normalization(self):
        related = {
            "source": "511ny",
            "source_id": "event-1",
            "latitude": 40.65,
            "longitude": -73.96,
            "location": "Church Avenue",
            "nearby_station": "Church Avenue",
            "severity": "high",
            "description": "Road closure near the station.",
        }
        duplicate = {**related, "description": "Same official road closure."}
        separate = {
            **related,
            "source_id": "event-2",
            "latitude": 40.69,
            "location": "Atlantic Avenue",
            "nearby_station": "Atlantic Avenue",
            "description": "Separate incident.",
        }
        with patch.object(
            self.trips.trip_incidents,
            "get_incidents",
            AsyncMock(return_value={
                "incidents": [related, duplicate, separate],
                "scan_metadata": {"status": "complete", "snapshot_status": "fresh"},
            }),
        ):
            result = await self.trips.trip_incidents._scan_route_incidents_with_metadata(["Church Avenue"])

        self.assertEqual(result["scan_metadata"]["merge"]["before_count"], 3)
        self.assertEqual(result["scan_metadata"]["merge"]["after_count"], 2)
        self.assertEqual(len(result["incidents"]), 2)
        self.assertEqual(set(result["incidents"][0]), {"location", "nearby_station", "severity", "description", "source"})

    async def test_cross_source_merge_preserves_attribution_in_metadata_and_advisor_source(self):
        official = {
            "source": "511ny", "source_id": "event-1", "latitude": 40.65, "longitude": -73.96,
            "location": "Church Avenue", "nearby_station": "Church Avenue", "severity": "high",
            "description": "Road closure on Church Avenue.",
        }
        corroboration = {
            "source": "@NYScanner", "source_id": "social-1", "latitude": 40.6501, "longitude": -73.9601,
            "location": "Church Avenue", "nearby_station": "Church Avenue", "severity": "high",
            "description": "Road closure on Church Avenue reported by responders.",
        }
        with patch.object(self.trips.trip_incidents, "get_incidents", AsyncMock(return_value={
            "incidents": [official, corroboration], "scan_metadata": {"status": "complete", "snapshot_status": "fresh"},
        })):
            result = await self.trips.trip_incidents._scan_route_incidents_with_metadata(["Church Avenue"])
        self.assertEqual(result["scan_metadata"]["merge"]["sources"], {"511ny": 1, "@NYScanner": 1})
        self.assertEqual(len(result["incidents"]), 1)
        self.assertIn("511ny", result["incidents"][0]["source"])
        self.assertIn("@NYScanner", result["incidents"][0]["source"])
        self.assertEqual(set(result["incidents"][0]), {"location", "nearby_station", "severity", "description", "source"})
