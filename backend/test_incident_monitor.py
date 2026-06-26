import asyncio
import importlib
import pathlib
import sys
import types
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import incident_monitor


class IncidentMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.originals = {
            "client": incident_monitor.client,
            "system": incident_monitor.system,
            "user": incident_monitor.user,
            "x_search": incident_monitor.x_search,
            "_XAI_API_KEY": incident_monitor._XAI_API_KEY,
            "_run_incident_agent": incident_monitor._run_incident_agent,
        }

    def tearDown(self):
        for key, value in self.originals.items():
            setattr(incident_monitor, key, value)

    def test_normalize_station_names_dedupes_and_strips(self):
        result = incident_monitor._normalize_station_names(
            ["Church Av", " Church Avenue ", "", "Prospect Park", "prospect park "]
        )
        self.assertEqual(result, ["Church Av", "Prospect Park"])

    def test_parse_json_object_accepts_fenced_json(self):
        payload = incident_monitor._parse_json_object(
            "```json\n{\"incidents\": []}\n```"
        )
        self.assertEqual(payload, {"incidents": []})

    def test_normalize_incident_payload_drops_invalid_and_off_route_items(self):
        payload = {
            "incidents": [
                {
                    "location": "123 Main St",
                    "nearby_station": "Church Avenue",
                    "severity": "high",
                    "description": "Smoke reported near the entrance.",
                    "source": "@NYScanner",
                    "extra": "drop-me",
                },
                {
                    "location": "456 Side St",
                    "nearby_station": "Off Route Stop",
                    "severity": "medium",
                    "description": "Police activity nearby.",
                    "source": "@CitizenAppNYC",
                },
                {
                    "location": "",
                    "nearby_station": "Church Avenue",
                    "severity": "high",
                    "description": "Bad payload",
                    "source": "@Noise",
                },
            ]
        }

        normalized = incident_monitor._normalize_incident_payload(
            payload,
            ["Church Avenue", "Prospect Park"],
        )

        self.assertEqual(
            normalized,
            {
                "incidents": [
                    {
                        "location": "123 Main St",
                        "nearby_station": "Church Avenue",
                        "severity": "high",
                        "description": "Smoke reported near the entrance.",
                        "source": "@NYScanner",
                    }
                ]
            },
        )

    async def test_get_incidents_empty_input_short_circuits(self):
        self.assertEqual(await incident_monitor.get_incidents([]), {"incidents": []})

    def test_run_incident_agent_uses_current_model_and_search_tool(self):
        calls: dict[str, object] = {}

        class FakeResponse:
            finish_reason = "stop"
            content = (
                '{"incidents":[{"location":"123 Main St","nearby_station":"Church Avenue",'
                '"severity":"high","description":"Smoke near the entrance.","source":"@NYScanner"}]}'
            )

        class FakeChat:
            def __init__(self):
                self.messages = []

            def append(self, message):
                self.messages.append(message)

            def sample(self):
                return FakeResponse()

        class FakeChatFactory:
            def create(self, **kwargs):
                calls["create"] = kwargs
                chat = FakeChat()
                calls["chat"] = chat
                return chat

        class FakeClient:
            def __init__(self):
                self.chat = FakeChatFactory()

        incident_monitor.client = FakeClient()
        incident_monitor.system = lambda text: {"role": "system", "content": text}
        incident_monitor.user = lambda text: {"role": "user", "content": text}
        incident_monitor.x_search = lambda: "X_SEARCH_TOOL"
        incident_monitor._XAI_API_KEY = "token"

        result = incident_monitor._run_incident_agent(
            "Church Avenue",
            ["Church Avenue"],
        )

        self.assertEqual(calls["create"]["model"], "grok-4-1-fast-reasoning")
        self.assertNotIn("max_turns", calls["create"])
        self.assertEqual(calls["create"]["tools"], ["X_SEARCH_TOOL"])
        self.assertEqual(
            result,
            {
                "incidents": [
                    {
                        "location": "123 Main St",
                        "nearby_station": "Church Avenue",
                        "severity": "high",
                        "description": "Smoke near the entrance.",
                        "source": "@NYScanner",
                    }
                ]
            },
        )

    def test_normalize_incident_payload_rejects_non_string_fields(self):
        payload = {
            "incidents": [
                {
                    "location": ["123 Main St"],
                    "nearby_station": "Church Avenue",
                    "severity": "high",
                    "description": "Smoke near the entrance.",
                    "source": {"handle": "@NYScanner"},
                }
            ]
        }

        self.assertEqual(
            incident_monitor._normalize_incident_payload(payload),
            {"incidents": []},
        )

    async def test_get_incidents_malformed_response_falls_back(self):
        incident_monitor._XAI_API_KEY = "token"
        incident_monitor._run_incident_agent = lambda station_names, station_list: {"incidents": []}
        result = await incident_monitor.get_incidents(["Church Avenue"])
        self.assertEqual(result, {"incidents": []})


class TripsIncidentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_trip_payload_uses_unwrapped_incidents_and_subway_stops_only(self):
        captured: dict[str, object] = {}

        for name in [
            "app.routers.trips",
            "app.services.trips",
            "app.services.trips.text",
            "app.services.trips.scoring",
            "app.services.trips.enrichment",
            "app.services.trips.candidates",
            "app.services.trips.incidents",
            "app.services.directions",
            "app.services.ai_advisor",
            "app.services.incident_monitor",
            "app.services.mta_feed",
            "app.services.voice",
            "fastapi",
            "pydantic",
        ]:
            sys.modules.pop(name, None)

        fastapi = types.ModuleType("fastapi")

        class APIRouter:
            def post(self, *args, **kwargs):
                def decorator(fn):
                    return fn

                return decorator

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail: str):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class Request:
            pass

        fastapi.APIRouter = APIRouter
        fastapi.HTTPException = HTTPException
        fastapi.Request = Request
        sys.modules["fastapi"] = fastapi

        pydantic = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        pydantic.BaseModel = BaseModel
        sys.modules["pydantic"] = pydantic

        directions = types.ModuleType("app.services.directions")

        async def get_transit_route(origin, destination, dest_coords=None):
            return {"ok": True}

        def parse_response(raw):
            return [
                [
                    {
                        "type": "SUBWAY",
                        "route_id": "Q",
                        "departure_stop": "Church Avenue",
                        "arrival_stop": "Prospect Park",
                    },
                    {
                        "type": "BUS",
                        "route_id": "B35",
                        "departure_stop": "Flatbush Ave",
                        "arrival_stop": "Church Avenue",
                    },
                ]
            ]

        directions.get_transit_route = get_transit_route
        directions.parse_response = parse_response
        sys.modules["app.services.directions"] = directions

        ai_advisor = types.ModuleType("app.services.ai_advisor")

        async def stream_recommendation(payload):
            captured["jarvis_payload"] = payload
            yield "Take the Q now. [ROUTE:0]"

        ai_advisor.stream_recommendation = stream_recommendation
        sys.modules["app.services.ai_advisor"] = ai_advisor

        incident_service = types.ModuleType("app.services.incident_monitor")

        async def get_incidents(route_stops):
            captured["route_stops"] = list(route_stops)
            return {
                "incidents": [
                    {
                        "location": "123 Main St",
                        "nearby_station": "Church Avenue",
                        "severity": "high",
                        "description": "Smoke near the entrance.",
                        "source": "@NYScanner",
                    }
                ]
            }

        incident_service.get_incidents = get_incidents
        sys.modules["app.services.incident_monitor"] = incident_service

        mta_feed = types.ModuleType("app.services.mta_feed")

        async def fetch_service_alerts():
            return b""

        async def get_stalled_buses(route_ids):
            return []

        def parse_service_alerts(raw):
            return []

        def filter_alerts_for_routes(alerts, route_ids):
            return []

        async def get_stalled_trains(route_ids):
            return []

        mta_feed.fetch_service_alerts = fetch_service_alerts
        mta_feed.get_stalled_buses = get_stalled_buses
        mta_feed.parse_service_alerts = parse_service_alerts
        mta_feed.filter_alerts_for_routes = filter_alerts_for_routes
        mta_feed.get_stalled_trains = get_stalled_trains
        sys.modules["app.services.mta_feed"] = mta_feed

        voice = types.ModuleType("app.services.voice")
        voice.generate_speech = lambda text: b"audio"
        sys.modules["app.services.voice"] = voice

        trips = importlib.import_module("app.routers.trips")

        class DummyGtfs:
            def get_intermediate_stops(self, route_id, departure_stop, arrival_stop):
                return ["Parkside Avenue"]

            # trips.py now asks for the coords variant; names derive from it.
            def get_intermediate_stops_with_coords(self, route_id, departure_stop, arrival_stop, *coords):
                return [{"name": "Parkside Avenue", "lat": 40.655, "lng": -73.961}]

        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(gtfs=DummyGtfs())))
        payload = trips.TripRequest(
            origin_lat=40.65,
            origin_lng=-73.97,
            destination="Times Square",
        )

        result = await trips.plan_trip(request, payload)

        # Incidents are scanned OFF the hot path now: the synchronous advisor
        # payload starts empty while a background scan fills the cache.
        self.assertIsInstance(captured["jarvis_payload"]["incidents"], list)
        self.assertEqual(captured["jarvis_payload"]["incidents"], [])

        # Drain the background scan. ATLAS scans EVERY station the rider could
        # encounter across all candidate routes -- board + alight of both the
        # subway and bus legs. (Intermediate subway stops would be added too when
        # a static pattern index is present; this DummyGtfs has none, so only the
        # endpoints are gathered.) The unwrapped incident list is then cached.
        await asyncio.gather(*list(trips.trip_incidents._INCIDENT_BG_TASKS))
        self.assertEqual(
            captured["route_stops"],
            ["Church Avenue", "Prospect Park", "Flatbush Ave"],
        )
        self.assertEqual(len(trips.trip_incidents._LAST_INCIDENTS), 1)
        self.assertEqual(result["route"][0]["route_id"], "Q")


if __name__ == "__main__":
    unittest.main()
