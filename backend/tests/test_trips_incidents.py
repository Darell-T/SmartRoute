import asyncio
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

    fake_voice = types.ModuleType("app.services.voice")

    def _fake_generate_speech(*args, **kwargs):
        return b""

    fake_voice.generate_speech = _fake_generate_speech

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = lambda api_key=None: SimpleNamespace(api_key=api_key)

    fake_elevenlabs = types.ModuleType("elevenlabs")
    fake_elevenlabs_client = types.ModuleType("elevenlabs.client")

    class _FakeElevenLabs:
        def __init__(self, api_key=None):
            self.api_key = api_key

    fake_elevenlabs_client.ElevenLabs = _FakeElevenLabs
    fake_elevenlabs.client = fake_elevenlabs_client

    with patch.dict(
        sys.modules,
        {
            "fastapi": fake_fastapi,
            "pydantic": fake_pydantic,
            "anthropic": fake_anthropic,
            "elevenlabs": fake_elevenlabs,
            "elevenlabs.client": fake_elevenlabs_client,
            "app.services.directions": fake_directions,
            "app.services.ai_advisor": fake_ai_advisor,
            "app.services.incident_monitor": fake_incident_monitor,
            "app.services.bus_routes": fake_bus_routes,
            "app.services.mta_feed": fake_mta_feed,
            "app.services.voice": fake_voice,
        },
    ):
        # Re-import the trips router AND its services.trips submodules fresh so
        # the incident-scan module globals (_LAST_INCIDENTS / _INCIDENT_SCAN_INFLIGHT,
        # now in services/trips/incidents.py) reset between test classes -- a bare
        # reload of the router alone would leave that submodule state stale.
        for _m in [k for k in list(sys.modules) if k == "app.routers.trips" or k.startswith("app.services.trips")]:
            sys.modules.pop(_m, None)
        return importlib.import_module("app.routers.trips")


class TripsIncidentPayloadTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.trips = _load_trips_module()

    async def test_plan_trip_unwraps_incidents_dict_before_streaming_to_claude(self):
        captured = {}

        parsed_route = [
            [
                {
                    "type": "SUBWAY",
                    "route_id": "Q",
                    "departure_stop": "Church Avenue",
                    "arrival_stop": "Atlantic Avenue-Barclays Center",
                }
            ]
        ]

        async def fake_stream_recommendation(payload):
            captured["payload"] = payload
            yield "Take the Q now, sir. [ROUTE:0]"

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    gtfs=SimpleNamespace(
                        get_intermediate_stops=lambda route_id, departure_stop, arrival_stop: [
                            "Church Avenue",
                            "Prospect Park",
                        ],
                        # trips.py now asks for the coords variant; the names
                        # consumed downstream derive from these rows.
                        get_intermediate_stops_with_coords=lambda route_id, departure_stop, arrival_stop, *coords: [
                            {"name": "Church Avenue", "lat": 40.650, "lng": -73.963},
                            {"name": "Prospect Park", "lat": 40.661, "lng": -73.962},
                        ],
                    )
                )
            )
        )
        payload = self.trips.TripRequest(
            origin_lat=40.6501,
            origin_lng=-73.9796,
            destination="Atlantic Avenue-Barclays Center",
        )

        # Reset the module-level background-incident state for determinism.
        self.trips.trip_incidents._LAST_INCIDENTS = []
        self.trips.trip_incidents._INCIDENT_SCAN_INFLIGHT = False

        with patch.object(
            self.trips, "get_transit_route", AsyncMock(return_value={"routes": ["unused"]})
        ), patch.object(
            self.trips, "parse_response", return_value=parsed_route
        ), patch.object(
            self.trips, "fetch_service_alerts", AsyncMock(return_value=[])
        ), patch.object(
            self.trips.trip_incidents,
            "get_incidents",
            AsyncMock(
                return_value={
                    "incidents": [
                        {
                            "location": "Flatbush Avenue",
                            "nearby_station": "Church Avenue",
                            "severity": "high",
                            "description": "Police activity outside the station.",
                            "source": "@NYScanner",
                        }
                    ]
                }
            ),
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
            self.trips, "generate_speech", return_value=b"audio"
        ):
            response = await self.trips.plan_trip(request, payload)

        # Incidents are scanned OFF the hot path now: the first trip returns
        # immediately with empty incidents + a pending flag while the background
        # scan runs.
        self.assertEqual(response["recommendation"], "Take the Q now, sir.")
        self.assertTrue(response["incidents_pending"])
        self.assertIsInstance(captured["payload"]["incidents"], list)
        self.assertEqual(captured["payload"]["incidents"], [])
        self.assertEqual(response["incidents"], [])

        # The background scan unwraps the {"incidents": [...]} dict and caches the
        # list for subsequent trips to serve.
        await asyncio.gather(*list(self.trips.trip_incidents._INCIDENT_BG_TASKS))
        self.assertEqual(
            self.trips.trip_incidents._LAST_INCIDENTS,
            [
                {
                    "location": "Flatbush Avenue",
                    "nearby_station": "Church Avenue",
                    "severity": "high",
                    "description": "Police activity outside the station.",
                    "source": "@NYScanner",
                }
            ],
        )

    async def test_plan_trip_falls_back_to_empty_incidents_list_for_bad_shape(self):
        captured = {}

        async def fake_stream_recommendation(payload):
            captured["payload"] = payload
            yield "Take the Q now, sir. [ROUTE:0]"

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gtfs=None)))
        payload = self.trips.TripRequest(
            origin_lat=40.6501,
            origin_lng=-73.9796,
            destination="Atlantic Avenue-Barclays Center",
        )

        with patch.object(
            self.trips, "get_transit_route", AsyncMock(return_value={"routes": ["unused"]})
        ), patch.object(
            self.trips,
            "parse_response",
            return_value=[
                [
                    {
                        "type": "SUBWAY",
                        "route_id": "Q",
                        "departure_stop": "Church Avenue",
                        "arrival_stop": "Atlantic Avenue-Barclays Center",
                    }
                ]
            ],
        ), patch.object(
            self.trips, "fetch_service_alerts", AsyncMock(return_value=[])
        ), patch.object(
            self.trips.trip_incidents, "get_incidents", AsyncMock(return_value="bad-shape")
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
            self.trips, "generate_speech", return_value=b"audio"
        ):
            await self.trips.plan_trip(request, payload)

        self.assertEqual(captured["payload"]["incidents"], [])

    async def test_plan_trip_falls_back_to_empty_incidents_list_for_bad_nested_shape(self):
        captured = {}

        async def fake_stream_recommendation(payload):
            captured["payload"] = payload
            yield "Take the Q now, sir. [ROUTE:0]"

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gtfs=None)))
        payload = self.trips.TripRequest(
            origin_lat=40.6501,
            origin_lng=-73.9796,
            destination="Atlantic Avenue-Barclays Center",
        )

        with patch.object(
            self.trips, "get_transit_route", AsyncMock(return_value={"routes": ["unused"]})
        ), patch.object(
            self.trips,
            "parse_response",
            return_value=[
                [
                    {
                        "type": "SUBWAY",
                        "route_id": "Q",
                        "departure_stop": "Church Avenue",
                        "arrival_stop": "Atlantic Avenue-Barclays Center",
                    }
                ]
            ],
        ), patch.object(
            self.trips, "fetch_service_alerts", AsyncMock(return_value=[])
        ), patch.object(
            self.trips.trip_incidents,
            "get_incidents",
            AsyncMock(return_value={"incidents": {"incidents": ["bad"]}}),
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
            self.trips, "generate_speech", return_value=b"audio"
        ):
            await self.trips.plan_trip(request, payload)

        self.assertEqual(captured["payload"]["incidents"], [])
