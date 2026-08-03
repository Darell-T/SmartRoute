"""Trip endpoint integration tests for the advisory incident boundary."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.trips import incidents


def _load_trips_module():
    fake_fastapi = types.ModuleType("fastapi")

    class _Router:
        def post(self, *_args, **_kwargs):
            return lambda function: function

    class _HttpError(Exception):
        def __init__(self, status_code, detail, **_kwargs):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fake_fastapi.APIRouter = _Router
    fake_fastapi.HTTPException = _HttpError
    fake_fastapi.Request = object
    fake_pydantic = types.ModuleType("pydantic")

    class _BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    fake_pydantic.BaseModel = _BaseModel
    fake_pydantic.ConfigDict = dict
    fake_directions = types.ModuleType("app.services.directions")
    fake_directions.get_transit_route = AsyncMock(return_value={"routes": ["unused"]})
    fake_directions.parse_response = lambda _response: []
    fake_advisor = types.ModuleType("app.services.ai_advisor")

    async def stream(_payload):
        yield "[ROUTE:0] Take the Q."

    fake_advisor.stream_recommendation = stream
    with patch.dict(sys.modules, {
        "fastapi": fake_fastapi,
        "pydantic": fake_pydantic,
        "app.services.directions": fake_directions,
        "app.services.ai_advisor": fake_advisor,
    }):
        sys.modules.pop("app.routers.trips", None)
        return importlib.import_module("app.routers.trips")


class TripIncidentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_incident_scan_starts_before_mta_context_and_is_advisor_input(self):
        trips = _load_trips_module()
        route = [[{
            "type": "SUBWAY",
            "route_id": "Q",
            "departure_stop": "Church Av",
            "arrival_stop": "Atlantic Av-Barclays Ctr",
            "departure_coords": {"lat": 40.650, "lng": -73.963},
            "arrival_coords": {"lat": 40.684, "lng": -73.977},
        }]]
        scan_started = asyncio.Event()
        mta_started = asyncio.Event()
        release_scan = asyncio.Event()
        advisor_payload: dict = {}

        async def scan(_context):
            scan_started.set()
            await release_scan.wait()
            return {
                "incidents": [{
                    "location": "Church Avenue",
                    "nearby_station": "Church Av",
                    "severity": "high",
                    "description": "Verified station access restriction.",
                    "impact_scope": "station_access",
                    "affected_candidate_route_ids": ["candidate-0"],
                    "evidence": [],
                    "corroborated": True,
                    "advisor_eligible": True,
                }],
                "scan_metadata": {"status": "complete", "sources": {"completed": ["x_search", "web_search"]}},
            }

        async def alerts():
            self.assertTrue(scan_started.is_set())
            mta_started.set()
            return []

        async def stream(payload):
            advisor_payload.update(payload)
            yield "[ROUTE:0] Take the Q."

        async def enrich(_gtfs, _steps):
            return {"subway_legs": 1, "bus_legs": 0, "subway_with_stops": 1, "bus_with_stops": 0}

        async def passthrough(displayed, **_kwargs):
            return displayed

        request = SimpleNamespace(
            headers={"X-SmartRoute-Principal": "v1.test-principal-opaque-123456"},
            app=SimpleNamespace(state=SimpleNamespace(gtfs=None)),
        )
        payload = trips.TripRequest(origin_lat=40.65, origin_lng=-73.96, destination="Atlantic Av")
        with patch.object(trips, "get_transit_route", AsyncMock(return_value={"routes": ["unused"]})), patch.object(
            trips, "parse_response", return_value=route
        ), patch.object(trips.trip_incidents, "build_candidate_stop_context", return_value=[object()]), patch.object(
            trips.trip_incidents, "scan_route_incidents", new=scan
        ), patch.object(trips, "fetch_service_alerts", new=alerts), patch.object(
            trips, "get_stalled_trains", AsyncMock(return_value=[])
        ), patch.object(trips, "get_stalled_buses", AsyncMock(return_value=[])), patch.object(
            trips, "parse_service_alerts", return_value=[]
        ), patch.object(trips, "filter_alerts_for_routes", return_value=[]), patch.object(
            trips, "stream_recommendation", new=stream
        ), patch.object(trips.enrichment, "_enrich_route", new=enrich), patch.object(
            trips.production_shadow, "run_trip_shadow", new=passthrough
        ), patch.object(trips.admission, "acquire", AsyncMock(return_value=object())), patch.object(
            trips.admission, "release", AsyncMock()
        ):
            task = asyncio.create_task(trips.plan_trip(request, payload))
            await mta_started.wait()
            release_scan.set()
            response = await task

        self.assertEqual(advisor_payload["incidents"][0]["nearby_station"], "Church Av")
        self.assertEqual(response["selected_route_index"], 0)

    def test_only_corroborated_route_impacting_evidence_reaches_advisor(self):
        raw = {
            "incidents": [
                {
                    "location": "Church Avenue",
                    "nearby_station": "Church Av",
                    "severity": "high",
                    "description": "Single source report.",
                    "impact_scope": "station_access",
                    "affected_candidate_route_ids": ["candidate-0"],
                    "evidence": [{
                        "source_type": "x_search",
                        "source_url": "https://x.com/one-source/status/1",
                        "source_origin": "@one-source",
                        "observed_at": "2026-08-02T16:00:00Z",
                    }],
                    "corroborated": False,
                    "advisor_eligible": False,
                },
                {
                    "location": "Church Avenue",
                    "nearby_station": "Church Av",
                    "severity": "high",
                    "description": "Two independent sources report a restriction.",
                    "impact_scope": "station_access",
                    "affected_candidate_route_ids": ["candidate-0"],
                    "evidence": [
                        {"source_type": "x_search", "source_url": "https://x.com/first/status/2", "source_origin": "@first", "observed_at": "2026-08-02T16:00:00Z"},
                        {"source_type": "web_search", "source_url": "https://web.example.test/post2", "source_origin": "Independent Outlet", "observed_at": "2026-08-02T16:00:00Z"},
                    ],
                    "corroborated": True,
                    "advisor_eligible": True,
                },
            ],
            "scan_metadata": {"status": "complete", "sources": {"completed": ["x_search", "web_search"]}},
        }

        result = incidents._normalized_contract(raw)

        self.assertEqual(len(result["incidents"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertTrue(result["incidents"][0]["advisor_eligible"])


if __name__ == "__main__":
    unittest.main()
