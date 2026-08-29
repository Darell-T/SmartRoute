"""plan_trip step enrichment: intermediate stop locations.

Loads app.routers.trips with faked dependencies and asserts that subway steps
get coordinate-bearing
intermediate stops from GTFS, bus steps get sliced OneBusAway stops, OBA
failures degrade to empty lists without breaking the trip, and the
enrichment lands on EVERY candidate (alternatives render identically when
picked).
"""

import asyncio
import importlib
import sys
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

SUBWAY_LOCATED = [
    {"name": "Church Av", "lat": 40.644, "lng": -73.979},
    {"name": "Fort Hamilton Pkwy", "lat": 40.650, "lng": -73.975},
]
BUS_LOCATED = [
    {"name": "AV A/1 ST", "lat": 40.700, "lng": -73.990},
    {"name": "AV A/3 ST", "lat": 40.703, "lng": -73.9905},
]


def _stub_fastapi():
    fake = types.ModuleType("fastapi")

    class _FakeAPIRouter:
        def post(self, *_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

    class _FakeHTTPError(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _FakeRequest:
        pass

    fake.APIRouter = _FakeAPIRouter
    fake.HTTPException = _FakeHTTPError
    fake.Request = _FakeRequest
    return fake


def _stub_pydantic():
    fake = types.ModuleType("pydantic")

    class _FakeBaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    fake.BaseModel = _FakeBaseModel
    fake.ConfigDict = dict
    return fake


def _stub_directions(routes):
    fake = types.ModuleType("app.services.directions")

    def _route_steps():
        subway_step = {
            "type": "SUBWAY",
            "route_id": "G",
            "departure_stop": "Church Av",
            "arrival_stop": "Fort Hamilton Pkwy",
            "route_total_minutes": 5,
        }
        bus_step = {
            "type": "BUS",
            "route_id": "M15",
            "departure_stop": "AV A/1 ST",
            "arrival_stop": "AV A/3 ST",
            "departure_coords": {"latitude": 40.700, "longitude": -73.990},
            "arrival_coords": {"latitude": 40.703, "longitude": -73.9905},
            "route_total_minutes": 5,
        }
        return subway_step, bus_step

    def _fake_parse_response(*_args, **_kwargs):
        if routes is not None:
            return routes
        s0, b0 = _route_steps()
        s1, _ = _route_steps()
        s1["route_total_minutes"] = 9
        return [[s0, b0], [s1]]

    async def _fake_get_transit_route(*_args, **_kwargs):
        return {"routes": []}

    class _FakeGoogleRoutesError(Exception):
        def __init__(
            self, code, message, *, provider_status=None, provider_summary=None
        ):
            super().__init__(message)
            self.code = code
            self.provider_status = provider_status
            self.provider_summary = provider_summary

    fake.get_transit_route = _fake_get_transit_route
    fake.parse_response = _fake_parse_response
    fake.GoogleRoutesError = _FakeGoogleRoutesError
    return fake


def _stub_mta_feed():
    fake = types.ModuleType("app.services.mta.realtime")

    async def _empty(*_args, **_kwargs):
        return []

    fake.fetch_service_alerts = _empty
    fake.get_stalled_buses = _empty
    fake.get_stalled_trains = _empty
    fake.parse_service_alerts = lambda *_args, **_kwargs: []
    fake.filter_alerts_for_routes = lambda *_args, **_kwargs: []
    return fake


def _stub_bus_routes(bus_fetch):
    fake = types.ModuleType("app.services.mta.bus")
    fake.fetch_bus_route_stop_groups = bus_fetch
    fake.slice_route_stops = lambda parsed, *_rest: list(parsed.get("canned", []))
    return fake


def _load_trips_module(bus_fetch, *, routes=None):
    fake_fastapi = _stub_fastapi()
    fake_pydantic = _stub_pydantic()
    fake_directions = _stub_directions(routes)
    fake_mta_feed = _stub_mta_feed()
    fake_bus_routes = _stub_bus_routes(bus_fetch)

    with patch.dict(
        sys.modules,
        {
            "fastapi": fake_fastapi,
            "pydantic": fake_pydantic,
            "app.services.directions": fake_directions,
            "app.services.mta.realtime": fake_mta_feed,
            "app.services.mta.bus": fake_bus_routes,
        },
    ):
        for _m in [
            k
            for k in list(sys.modules)
            if k
            in {
                "app.routers.trips",
                "app.services.agent.tools.route.preparation_adapter",
            }
            or k.startswith("app.services.trips")
        ]:
            sys.modules.pop(_m, None)
        module = importlib.import_module("app.routers.trips")
        real_admission = module.admission
        module.admission = SimpleNamespace(
            acquire=AsyncMock(
                return_value=real_admission.AdmissionLease(
                    "v1.test-principal-opaque-123456",
                    "trip",
                    "test-lease",
                )
            ),
            release=AsyncMock(),
            AdmissionDenied=real_admission.AdmissionDenied,
            principal_from_request=real_admission.principal_from_request,
        )
        module._test_fakes = {
            "directions": fake_directions,
            "mta_feed": fake_mta_feed,
            "bus_routes": fake_bus_routes,
        }
        return module


def _request_with_gtfs():
    return SimpleNamespace(
        headers={"X-SmartRoute-Principal": "v1.test-principal-opaque-123456"},
        app=SimpleNamespace(
            state=SimpleNamespace(
                gtfs=SimpleNamespace(
                    get_intermediate_stops_with_coords=lambda _route_id, _dep, _arr, *_coords: (
                        list(SUBWAY_LOCATED)
                    ),
                )
            )
        ),
    )


def _request_with_slow_gtfs():
    def _slow_lookup(_route_id, _dep, _arr, *_coords):
        time.sleep(0.1)
        return list(SUBWAY_LOCATED)

    return SimpleNamespace(
        headers={"X-SmartRoute-Principal": "v1.test-principal-opaque-123456"},
        app=SimpleNamespace(
            state=SimpleNamespace(
                gtfs=SimpleNamespace(get_intermediate_stops_with_coords=_slow_lookup)
            )
        ),
    )


def _payload(trips):
    return trips.TripRequest(
        origin_lat=40.7,
        origin_lng=-73.99,
        destination="Test Dest",
        destination_lat=40.70,
        destination_lng=-73.98,
    )


class TripEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_chosen_route_enriched_alternates_deferred(self):
        async def bus_fetch(_route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))

        subway = result["route"][0]
        assert subway["intermediate_stop_locations"] == SUBWAY_LOCATED
        assert subway["intermediate_stops"] == ["Church Av", "Fort Hamilton Pkwy"]

        bus = result["route"][1]
        assert bus["intermediate_stop_locations"] == BUS_LOCATED
        assert bus["intermediate_stops"] == ["AV A/1 ST", "AV A/3 ST"]

        # Only the chosen route is enriched on the initial response.
        chosen_candidate = result["route_candidates"][0]
        assert chosen_candidate["enriched"]
        assert not chosen_candidate["can_enrich_on_select"]
        assert "itinerary" in chosen_candidate
        assert chosen_candidate["total_minutes"] == round(
            chosen_candidate["itinerary"]["total_duration_seconds"] / 60
        )
        assert (
            chosen_candidate["score_breakdown"]["transfers"]
            == chosen_candidate["itinerary"]["transfer_count"]
        )

        # Alternates are deferred: empty stop lists, flagged for lazy enrichment.
        alt_candidate = result["route_candidates"][1]
        assert not alt_candidate["enriched"]
        assert alt_candidate["can_enrich_on_select"]
        assert alt_candidate["steps"][0]["intermediate_stop_locations"] == []

    async def test_enrich_route_endpoint_fills_alternate_lazily(self):
        async def bus_fetch(_route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        # Plan a trip, grab the un-enriched alternate, enrich it on demand.
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        alt_steps = result["route_candidates"][1]["steps"]
        assert alt_steps[0]["intermediate_stop_locations"] == []

        enriched = await trips.enrich_route(
            _request_with_gtfs(), trips.EnrichRouteRequest(steps=alt_steps)
        )
        assert enriched["enriched"]
        assert enriched["steps"][0]["intermediate_stop_locations"] == SUBWAY_LOCATED

    async def test_enrich_route_accepts_complete_step_and_rejects_invalid_steps_before_enrichment(
        self,
    ):
        async def bus_fetch(_route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        step = {
            "type": "SUBWAY",
            "route_id": "A",
            "train_line": "A",
            "line_color": "#0039A6",
            "direction": "Downtown",
            "departure_stop": "Jay St-MetroTech",
            "arrival_stop": "59 St-Columbus Circle",
            "departure_time_iso": "2026-07-28T12:00:00-04:00",
            "arrival_time_iso": "2026-07-28T12:20:00-04:00",
            "minutes_until_train_arrives": -1,
            "minutes_until_arrival": 19,
            "route_total_minutes": 20,
            "route_total_seconds": 1200,
            "duration_minutes": 18,
            "distance_meters": 4300,
            "stop_count": 5,
            "segment_index": 1,
            "start_point": {"lat": 40.692, "lng": -73.987},
            "end_point": {"lat": 40.764, "lng": -73.98},
            "departure_coords": {"latitude": 40.692, "longitude": -73.987},
            "arrival_coords": {"latitude": 40.764, "longitude": -73.98},
            "polyline": {"encodedPolyline": "abc"},
            "intermediate_stops": ["Canal St"],
            "intermediate_stop_locations": [
                {"name": "Canal St", "lat": 40.72, "lng": -74.0}
            ],
        }
        enrich = AsyncMock(
            return_value={
                "subway_legs": 1,
                "bus_legs": 0,
                "subway_with_stops": 1,
                "bus_with_stops": 0,
            }
        )

        with patch.object(trips.direct_plan.enrichment, "_enrich_route", enrich):
            result = await trips.enrich_route(
                _request_with_gtfs(), trips.EnrichRouteRequest(steps=[step])
            )
        assert result["enriched"]
        enrich.assert_awaited_once()

        invalid_steps = (
            {**step, "route_total_minutes": -1},
            {**step, "route_total_seconds": -1},
            {**step, "stop_count": 1.5},
            {**step, "unexpected": True},
            {**step, "route_id": {}},
            {**step, "departure_time_iso": 123},
            {**step, "direction": []},
            {**step, "route_id": "x" * 301},
            {**step, "arrival_time_iso": "x" * 65},
            {**step, "departure_coords": None},
            {**step, "start_point": {"lat": 40.692}},
            {**step, "end_point": {"lat": 40.764, "longitude": -73.98}},
            {
                **step,
                "arrival_coords": {
                    "lat": 40.764,
                    "lng": -73.98,
                    "latitude": 40.764,
                    "longitude": -73.98,
                },
            },
            {**step, "start_point": {"lat": True, "lng": -73.987}},
            {**step, "end_point": {"lat": float("nan"), "lng": -73.98}},
            {**step, "departure_coords": {"latitude": 42, "longitude": -73.987}},
            {
                **step,
                "intermediate_stop_locations": [
                    {"name": "Canal St", "lat": "invalid", "lng": -74.0}
                ],
            },
        )
        rejected_enrich = AsyncMock()
        with patch.object(
            trips.direct_plan.enrichment, "_enrich_route", rejected_enrich
        ):
            for invalid_step in invalid_steps:
                with (
                    self.subTest(invalid_step=invalid_step),
                    pytest.raises(trips.HTTPException),
                ):
                    await trips.enrich_route(
                        _request_with_gtfs(),
                        trips.EnrichRouteRequest(steps=[invalid_step]),
                    )
        rejected_enrich.assert_not_awaited()

    async def test_enrich_route_accepts_rail_modes_without_rejecting_them(self):
        async def bus_fetch(_route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        tram_step = {
            "type": "TRAM",
            "route_id": "R32",
            "train_line": "R32",
            "direction": "To Terminal 8",
            "departure_stop": "Jamaica",
            "arrival_stop": "Airport",
            "start_point": {"lat": 40.7, "lng": -73.8},
            "end_point": {"lat": 40.65, "lng": -73.78},
        }
        enrich = AsyncMock(
            return_value={
                "subway_legs": 0,
                "bus_legs": 0,
                "subway_with_stops": 0,
                "bus_with_stops": 0,
            }
        )
        with patch.object(trips.direct_plan.enrichment, "_enrich_route", enrich):
            result = await trips.enrich_route(
                _request_with_gtfs(), trips.EnrichRouteRequest(steps=[tram_step])
            )
        assert result["enriched"]
        enrich.assert_awaited_once()

    async def test_oba_failure_degrades_to_empty_without_breaking_trip(self):
        async def bus_fetch(_route_id):
            message = "OBA down"
            raise RuntimeError(message)

        trips = _load_trips_module(bus_fetch)
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))

        bus = result["route"][1]
        assert bus["intermediate_stop_locations"] == []
        assert bus["intermediate_stops"] == []
        assert "recommendation" in result

    async def test_slow_gtfs_enrichment_degrades_without_hanging_trip(self):
        async def bus_fetch(_route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        trips.direct_plan.enrichment.TRIP_GTFS_ENRICH_TIMEOUT_S = 0.01
        started = time.monotonic()
        result = await trips.plan_trip(_request_with_slow_gtfs(), _payload(trips))

        assert time.monotonic() - started < 0.08
        subway = result["route"][0]
        assert subway["intermediate_stop_locations"] == []
        assert subway["intermediate_stops"] == []
        assert "recommendation" in result

    async def test_slow_live_context_degrades_without_hanging_trip(self):
        async def bus_fetch(_route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        trips.TRIP_CONTEXT_TIMEOUT_S = 0.01

        async def _slow_context(*_args, **_kwargs):
            await asyncio.sleep(0.1)
            return []

        mta_feed = trips._test_fakes["mta_feed"]
        mta_feed.fetch_service_alerts = _slow_context
        mta_feed.get_stalled_trains = _slow_context
        mta_feed.get_stalled_buses = _slow_context
        started = time.monotonic()
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))

        assert time.monotonic() - started < 0.08
        assert result["alerts"] == []
        assert "incidents" not in result
        assert "recommendation" in result

    async def test_enrich_route_oserror_returns_unenriched_steps(self):
        from app.routers import trips as trips_mod

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gtfs=object())))
        payload = trips_mod.EnrichRouteRequest(steps=[{"type": "WALK"}])
        with patch.object(
            trips_mod.enrichment,
            "_enrich_route",
            AsyncMock(side_effect=OSError("gtfs down")),
        ):
            result = await trips_mod.enrich_route(request, payload)
        assert result == {"steps": [{"type": "WALK"}], "enriched": False}


if __name__ == "__main__":
    unittest.main()
