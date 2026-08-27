"""Focused direct Live Map planning tests.

Covers the production wiring of ``POST /api/trip``: the direct endpoint
delegates to the shared model-free ``prepare_single_leg`` pipeline and its
import graph never loads the nested advisor, selection parser, or
``[ROUTE:N]`` control path. Also pins the REST coordinate
place resolution, named-destination resolution, and provider-error
translation contract.
"""

from __future__ import annotations

import asyncio
import importlib
import multiprocessing
import sys
import unittest
from unittest.mock import patch

import pytest
from app.services.trips.direct_plan import (
    DirectTripError,
    _resolved_places,
    _translate_prepare_error,
)

from tests.test_trips_enrichment import (
    _load_trips_module,
    _payload,
    _request_with_gtfs,
)

_BANNED_MODULES = {
    "evaluation.route_intelligence.advisor_context",
    "evaluation.route_intelligence.advisor",
    "app.services.agent.tools.route.prepare_route_options",
    "app.services.agent.tools.route.present_route",
}


def _probe_trip_router_import_graph(result_queue) -> None:
    importlib.import_module("app.routers.trips")
    result_queue.put(sorted(name for name in sys.modules if name in _BANNED_MODULES))


async def _bus_fetch(_route_id):
    return {"canned": []}


def _subway_step(route_id, minutes):
    return {
        "type": "SUBWAY",
        "route_id": route_id,
        "departure_stop": "14 St",
        "arrival_stop": "23 St",
        "route_total_minutes": minutes,
    }


class DirectPlanWiringTests(unittest.TestCase):
    def test_api_trip_import_graph_is_advisor_free(self):
        """Production wiring: /api/trip never imports the legacy stacks."""
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_probe_trip_router_import_graph,
            args=(result_queue,),
        )
        process.start()
        process.join(120)
        assert process.exitcode == 0
        assert result_queue.get(timeout=1) == []
        result_queue.close()


class DirectPlanPlaceResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_coordinates_build_exact_resolved_places(self):
        origin, destination = _resolved_places(
            40.71, -73.99, "Brooklyn Bridge", 40.7061, -73.9969
        )
        assert origin.name == "Your location"
        assert (origin.latitude, origin.longitude) == (40.71, -73.99)
        assert origin.source == "user"
        assert destination.name == "Brooklyn Bridge"
        assert (destination.latitude, destination.longitude) == (40.7061, -73.9969)
        assert destination.source == "gps"

        _, without_coords = _resolved_places(
            40.71, -73.99, "Brooklyn Bridge", None, None
        )
        assert without_coords is None

    async def test_exact_places_reach_canonical_itinerary(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[[_subway_step("A", 20)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        itinerary = result["route_candidates"][0]["itinerary"]
        assert itinerary["origin"] == {
            "label": "Your location",
            "lat": 40.7,
            "lng": -73.99,
        }
        assert itinerary["destination"] == {
            "label": "Test Dest",
            "lat": 40.7,
            "lng": -73.98,
        }
        assert result["selection_decision"]["selection_reason"] == "lowest_final_score"
        assert result["selection_decision"] is itinerary["selection_decision"]

    async def test_named_destination_resolves_when_coordinates_absent(self):
        trips = _load_trips_module(_bus_fetch, routes=[[_subway_step("A", 20)]])
        payload = trips.TripRequest(
            origin_lat=40.7,
            origin_lng=-73.99,
            destination="Test Dest",
            destination_lat=None,
            destination_lng=None,
        )
        with patch(
            "app.services.geography.geocode_address_with_reason",
            return_value=((40.70, -73.98), None),
        ):
            result = await trips.plan_trip(_request_with_gtfs(), payload)
        itinerary = result["route_candidates"][0]["itinerary"]
        assert itinerary["destination"] == {
            "label": "Test Dest",
            "lat": 40.7,
            "lng": -73.98,
        }
        assert "[ROUTE:" not in result["recommendation"]

    async def test_named_destination_failure_is_controlled_not_500(self):
        trips = _load_trips_module(_bus_fetch, routes=[[_subway_step("A", 20)]])
        payload = trips.TripRequest(
            origin_lat=40.7,
            origin_lng=-73.99,
            destination="Test Dest",
            destination_lat=None,
            destination_lng=None,
        )
        with (
            patch(
                "app.services.geography.geocode_address_with_reason",
                return_value=(None, "Address not found in NYC."),
            ),
            pytest.raises(trips.HTTPException) as error,
        ):
            await trips.plan_trip(_request_with_gtfs(), payload)
        assert error.value.status_code == 404
        assert error.value.detail == "No route found"


class DirectPlanErrorTranslationTests(unittest.TestCase):
    def _assert(self, error, status_code, detail):
        translated = _translate_prepare_error(error)
        assert isinstance(translated, DirectTripError)
        assert translated.status_code == status_code
        assert translated.detail == detail

    def test_no_route_maps_to_404(self):
        self._assert(
            "no transit route found between those points", 404, "No route found"
        )
        self._assert("", 404, "No route found")

    def test_timeout_maps_to_503(self):
        self._assert("routing failed (timeout)", 503, "Google Routes API timed out")

    def test_not_configured_maps_to_500(self):
        self._assert(
            "routing failed (not_configured)", 500, "Routing provider is not configured"
        )

    def test_http_status_maps_to_502(self):
        self._assert(
            "routing failed (http_429)",
            502,
            "Upstream routing provider error (http_429)",
        )

    def test_network_and_json_failures_map_to_502(self):
        self._assert(
            "routing failed (request_failed)",
            502,
            "Upstream routing provider network error",
        )
        self._assert(
            "routing failed (invalid_json)",
            502,
            "Upstream routing provider returned invalid data",
        )

    def test_destination_resolution_failures_map_to_404_or_503(self):
        self._assert(
            "could not find that destination in NYC",
            404,
            "No route found",
        )
        self._assert(
            "Geocoding service is temporarily unavailable.",
            503,
            "Destination lookup is temporarily unavailable.",
        )


class DirectPlanDeadlineTests(unittest.IsolatedAsyncioTestCase):
    """The outer deadline bounds multiplied provider retries (fix 4)."""

    async def test_slow_planning_raises_controlled_503(self):
        from app.services.trips import direct_plan

        async def _slow_once(**_kwargs):
            await asyncio.sleep(5)
            return {}

        with (
            patch.object(direct_plan, "DIRECT_TRIP_DEADLINE_S", 0.05),
            patch.object(direct_plan, "_plan_direct_trip_once", new=_slow_once),
            pytest.raises(direct_plan.DirectTripError) as raised,
        ):
            await direct_plan.plan_direct_trip(
                gtfs=None,
                origin_lat=40.7,
                origin_lng=-73.99,
                destination="Test Dest",
                destination_lat=None,
                destination_lng=None,
                timings={},
            )
        assert raised.value.status_code == 503
        assert raised.value.detail == "Trip planning is temporarily unavailable."

    async def test_fast_planning_is_not_deadline_blocked(self):
        from app.services.trips import direct_plan

        async def _fast_once(**kwargs):
            return {"route": kwargs.get("destination")}

        with patch.object(direct_plan, "_plan_direct_trip_once", new=_fast_once):
            result = await direct_plan.plan_direct_trip(
                gtfs=None,
                origin_lat=40.7,
                origin_lng=-73.99,
                destination="Test Dest",
                destination_lat=None,
                destination_lng=None,
                timings={},
            )
        assert result == {"route": "Test Dest"}


if __name__ == "__main__":
    unittest.main()
