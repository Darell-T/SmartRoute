"""plan_trip step enrichment: intermediate stop locations.

Loads app.routers.trips with faked dependencies and asserts that subway steps
get coordinate-bearing
intermediate stops from GTFS, bus steps get sliced OneBusAway stops, OBA
failures degrade to empty lists without breaking the trip, and the
enrichment lands on EVERY candidate (alternatives render identically when
picked).
"""

import importlib
import asyncio
import sys
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SUBWAY_LOCATED = [
    {"name": "Church Av", "lat": 40.644, "lng": -73.979},
    {"name": "Fort Hamilton Pkwy", "lat": 40.650, "lng": -73.975},
]
BUS_LOCATED = [
    {"name": "AV A/1 ST", "lat": 40.700, "lng": -73.990},
    {"name": "AV A/3 ST", "lat": 40.703, "lng": -73.9905},
]


def _load_trips_module(bus_fetch, *, routes=None):
    # app.routers.trips now imports route_constraints from the agent tools
    # package. Import that package under the REAL environment first: re-running
    # app.services.agent.tools.__init__ under the faked fastapi below would fail
    # (transit_snapshot -> app.routers.live_feed needs WebSocket/JSONResponse).
    import app.services.agent.tools.route_option_assembly  # noqa: F401

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
    fake_pydantic.ConfigDict = dict

    fake_directions = types.ModuleType("app.services.directions")

    def _route_steps():
        subway_step = {
            "type": "SUBWAY",
            "route_id": "G",
            "departure_stop": "Church Av",
            "arrival_stop": "Fort Hamilton Pkwy",
            # Deterministic selection picks the lowest (minutes, transfers,
            # index): route 0 at 5 min beats route 1 at 9 min, so the chosen
            # route stays the subway+bus candidate the enrichment tests assert.
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

    def _fake_parse_response(*args, **kwargs):
        if routes is not None:
            return routes
        s0, b0 = _route_steps()
        s1, _ = _route_steps()
        s1["route_total_minutes"] = 9
        return [[s0, b0], [s1]]

    async def _fake_get_transit_route(*args, **kwargs):
        return {"routes": []}

    class _FakeGoogleRoutesError(Exception):
        def __init__(self, code, message, *, provider_status=None, provider_summary=None):
            super().__init__(message)
            self.code = code
            self.provider_status = provider_status
            self.provider_summary = provider_summary

    fake_directions.get_transit_route = _fake_get_transit_route
    fake_directions.parse_response = _fake_parse_response
    fake_directions.GoogleRoutesError = _FakeGoogleRoutesError

    fake_mta_feed = types.ModuleType("app.services.mta_feed")

    async def _fake_fetch_service_alerts(*args, **kwargs):
        return []

    async def _fake_get_stalled_buses(*args, **kwargs):
        return []

    async def _fake_get_stalled_trains(*args, **kwargs):
        return []

    fake_mta_feed.fetch_service_alerts = _fake_fetch_service_alerts
    fake_mta_feed.get_stalled_buses = _fake_get_stalled_buses
    fake_mta_feed.get_stalled_trains = _fake_get_stalled_trains
    fake_mta_feed.parse_service_alerts = lambda *a, **k: []
    fake_mta_feed.filter_alerts_for_routes = lambda *a, **k: []

    fake_bus_routes = types.ModuleType("app.services.bus_routes")
    fake_bus_routes.fetch_bus_route_stop_groups = bus_fetch

    def _fake_slice_route_stops(parsed, board_coords, exit_coords, max_snap_m=250):
        return list(parsed.get("canned", []))

    fake_bus_routes.slice_route_stops = _fake_slice_route_stops

    with patch.dict(
        sys.modules,
        {
            "fastapi": fake_fastapi,
            "pydantic": fake_pydantic,
            "app.services.directions": fake_directions,
            "app.services.mta_feed": fake_mta_feed,
            "app.services.bus_routes": fake_bus_routes,
        },
    ):
        # Drop the trips router, its services.trips submodules, and the direct
        # preparation dependency factory so they re-import fresh inside this
        # stub context and bind the fake provider modules at module load.
        for _m in [
            k
            for k in list(sys.modules)
            if k
            in {
                "app.routers.trips",
                "app.routers.trip_enrichment",
                "app.services.agent.tools.plan_trip_dependencies",
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
        # Expose the injected fakes so focused tests can slow/fail them the
        # same way the router-level tests used to patch module attributes.
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
                    get_intermediate_stops_with_coords=lambda route_id, dep, arr, *coords: list(SUBWAY_LOCATED),
                )
            )
        )
    )


def _request_with_slow_gtfs():
    def _slow_lookup(route_id, dep, arr, *coords):
        time.sleep(0.1)
        return list(SUBWAY_LOCATED)

    return SimpleNamespace(
        headers={"X-SmartRoute-Principal": "v1.test-principal-opaque-123456"},
        app=SimpleNamespace(
            state=SimpleNamespace(
                gtfs=SimpleNamespace(get_intermediate_stops_with_coords=_slow_lookup)
            )
        )
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
        async def bus_fetch(route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))

        subway = result["route"][0]
        self.assertEqual(subway["intermediate_stop_locations"], SUBWAY_LOCATED)
        self.assertEqual(subway["intermediate_stops"], ["Church Av", "Fort Hamilton Pkwy"])

        bus = result["route"][1]
        self.assertEqual(bus["intermediate_stop_locations"], BUS_LOCATED)
        self.assertEqual(bus["intermediate_stops"], ["AV A/1 ST", "AV A/3 ST"])

        # Only the chosen route is enriched on the initial response.
        chosen_candidate = result["route_candidates"][0]
        self.assertTrue(chosen_candidate["enriched"])
        self.assertFalse(chosen_candidate["can_enrich_on_select"])
        self.assertIn("itinerary", chosen_candidate)
        self.assertEqual(
            chosen_candidate["total_minutes"],
            round(chosen_candidate["itinerary"]["total_duration_seconds"] / 60),
        )
        self.assertEqual(
            chosen_candidate["score_breakdown"]["transfers"],
            chosen_candidate["itinerary"]["transfer_count"],
        )

        # Alternates are deferred: empty stop lists, flagged for lazy enrichment.
        alt_candidate = result["route_candidates"][1]
        self.assertFalse(alt_candidate["enriched"])
        self.assertTrue(alt_candidate["can_enrich_on_select"])
        self.assertEqual(alt_candidate["steps"][0]["intermediate_stop_locations"], [])

    async def test_enrich_route_endpoint_fills_alternate_lazily(self):
        async def bus_fetch(route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        # Plan a trip, grab the un-enriched alternate, enrich it on demand.
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        alt_steps = result["route_candidates"][1]["steps"]
        self.assertEqual(alt_steps[0]["intermediate_stop_locations"], [])

        enriched = await trips.enrich_route(
            _request_with_gtfs(), trips.EnrichRouteRequest(steps=alt_steps)
        )
        self.assertTrue(enriched["enriched"])
        self.assertEqual(
            enriched["steps"][0]["intermediate_stop_locations"], SUBWAY_LOCATED
        )

    async def test_enrich_route_accepts_complete_step_and_rejects_invalid_steps_before_enrichment(self):
        async def bus_fetch(route_id):
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
        enrich = AsyncMock(return_value={
            "subway_legs": 1,
            "bus_legs": 0,
            "subway_with_stops": 1,
            "bus_with_stops": 0,
        })

        with patch.object(trips.direct_plan.enrichment, "_enrich_route", enrich):
            result = await trips.enrich_route(
                _request_with_gtfs(), trips.EnrichRouteRequest(steps=[step])
            )
        self.assertTrue(result["enriched"])
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
        with patch.object(trips.direct_plan.enrichment, "_enrich_route", rejected_enrich):
            for invalid_step in invalid_steps:
                with self.subTest(invalid_step=invalid_step), self.assertRaises(trips.HTTPException):
                    await trips.enrich_route(
                        _request_with_gtfs(),
                        trips.EnrichRouteRequest(steps=[invalid_step]),
                    )
        rejected_enrich.assert_not_awaited()

    async def test_enrich_route_accepts_rail_modes_without_rejecting_them(self):
        async def bus_fetch(route_id):
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
        enrich = AsyncMock(return_value={
            "subway_legs": 0,
            "bus_legs": 0,
            "subway_with_stops": 0,
            "bus_with_stops": 0,
        })
        with patch.object(trips.direct_plan.enrichment, "_enrich_route", enrich):
            result = await trips.enrich_route(
                _request_with_gtfs(), trips.EnrichRouteRequest(steps=[tram_step])
            )
        self.assertTrue(result["enriched"])
        enrich.assert_awaited_once()

    async def test_oba_failure_degrades_to_empty_without_breaking_trip(self):
        async def bus_fetch(route_id):
            raise RuntimeError("OBA down")

        trips = _load_trips_module(bus_fetch)
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))

        bus = result["route"][1]
        self.assertEqual(bus["intermediate_stop_locations"], [])
        self.assertEqual(bus["intermediate_stops"], [])
        self.assertIn("recommendation", result)

    async def test_slow_gtfs_enrichment_degrades_without_hanging_trip(self):
        async def bus_fetch(route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        trips.direct_plan.enrichment.TRIP_GTFS_ENRICH_TIMEOUT_S = 0.01
        started = time.monotonic()
        result = await trips.plan_trip(_request_with_slow_gtfs(), _payload(trips))

        self.assertLess(time.monotonic() - started, 0.08)
        subway = result["route"][0]
        self.assertEqual(subway["intermediate_stop_locations"], [])
        self.assertEqual(subway["intermediate_stops"], [])
        self.assertIn("recommendation", result)

    async def test_slow_live_context_degrades_without_hanging_trip(self):
        async def bus_fetch(route_id):
            return {"canned": BUS_LOCATED}

        trips = _load_trips_module(bus_fetch)
        trips.TRIP_CONTEXT_TIMEOUT_S = 0.01

        async def _slow_context(*args, **kwargs):
            await asyncio.sleep(0.1)
            return []

        mta_feed = trips._test_fakes["mta_feed"]
        mta_feed.fetch_service_alerts = _slow_context
        mta_feed.get_stalled_trains = _slow_context
        mta_feed.get_stalled_buses = _slow_context
        started = time.monotonic()
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))

        self.assertLess(time.monotonic() - started, 0.08)
        self.assertEqual(result["alerts"], [])
        self.assertNotIn("incidents", result)
        self.assertIn("recommendation", result)


if __name__ == "__main__":
    unittest.main()
