"""Focused direct Live Map planning tests.

Covers the production wiring of ``POST /api/trip``: the direct endpoint
delegates to the shared model-free ``prepare_single_leg`` pipeline and its
import graph never loads the nested advisor, selection parser, shadow
evaluator, or ``[ROUTE:N]`` control path. Also pins the REST coordinate
place resolution, named-destination resolution, and provider-error
translation contract.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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


BACKEND_DIR = Path(__file__).resolve().parent.parent

_BANNED_MODULES = {
    "evaluation.route_intelligence.advisor_context",
    "evaluation.route_intelligence.advisor",
    "evaluation.route_intelligence.shadow",
    "evaluation.route_intelligence.trip_shadow",
    "app.services.agent.tools.route.prepare_route_options",
    "app.services.agent.tools.route.present_route",
}


async def _bus_fetch(route_id):
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
    def test_api_trip_import_graph_is_advisor_and_shadow_free(self):
        """Production wiring: /api/trip never imports the legacy stacks."""
        probe = (
            "import sys\n"
            "import app.routers.trips  # noqa: F401\n"
            f"banned = {sorted(_BANNED_MODULES)!r}\n"
            "loaded = sorted(name for name in sys.modules if name in banned)\n"
            "if loaded:\n"
            "    print('LOADED:' + ','.join(loaded))\n"
            "    sys.exit(1)\n"
            "print('CLEAN')\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"advisor/shadow modules loaded: {completed.stdout}{completed.stderr}",
        )
        self.assertIn("CLEAN", completed.stdout)


class DirectPlanPlaceResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_coordinates_build_exact_resolved_places(self):
        origin, destination = _resolved_places(
            40.71, -73.99, "Brooklyn Bridge", 40.7061, -73.9969
        )
        self.assertEqual(origin.name, "Your location")
        self.assertEqual((origin.latitude, origin.longitude), (40.71, -73.99))
        self.assertEqual(origin.source, "user")
        self.assertEqual(destination.name, "Brooklyn Bridge")
        self.assertEqual((destination.latitude, destination.longitude), (40.7061, -73.9969))
        self.assertEqual(destination.source, "gps")

        _, without_coords = _resolved_places(
            40.71, -73.99, "Brooklyn Bridge", None, None
        )
        self.assertIsNone(without_coords)

    async def test_exact_places_reach_canonical_itinerary(self):
        trips = _load_trips_module(
            _bus_fetch,
            routes=[[_subway_step("A", 20)]],
        )
        result = await trips.plan_trip(_request_with_gtfs(), _payload(trips))
        itinerary = result["route_candidates"][0]["itinerary"]
        self.assertEqual(
            itinerary["origin"],
            {"label": "Your location", "lat": 40.7, "lng": -73.99},
        )
        self.assertEqual(
            itinerary["destination"],
            {"label": "Test Dest", "lat": 40.70, "lng": -73.98},
        )
        self.assertEqual(result["selection_decision"]["selection_reason"], "lowest_final_score")
        self.assertIs(result["selection_decision"], itinerary["selection_decision"])

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
        self.assertEqual(
            itinerary["destination"],
            {"label": "Test Dest", "lat": 40.70, "lng": -73.98},
        )
        self.assertNotIn("[ROUTE:", result["recommendation"])

    async def test_named_destination_failure_is_controlled_not_500(self):
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
            return_value=(None, "Address not found in NYC."),
        ):
            with self.assertRaises(trips.HTTPException) as error:
                await trips.plan_trip(_request_with_gtfs(), payload)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "No route found")


class DirectPlanErrorTranslationTests(unittest.TestCase):
    def _assert(self, error, status_code, detail):
        translated = _translate_prepare_error(error)
        self.assertIsInstance(translated, DirectTripError)
        self.assertEqual(translated.status_code, status_code)
        self.assertEqual(translated.detail, detail)

    def test_no_route_maps_to_404(self):
        self._assert("no transit route found between those points", 404, "No route found")
        self._assert("", 404, "No route found")

    def test_timeout_maps_to_503(self):
        self._assert("routing failed (timeout)", 503, "Google Routes API timed out")

    def test_not_configured_maps_to_500(self):
        self._assert("routing failed (not_configured)", 500, "Routing provider is not configured")

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

        with patch.object(direct_plan, "DIRECT_TRIP_DEADLINE_S", 0.05), patch.object(
            direct_plan, "_plan_direct_trip_once", new=_slow_once
        ):
            with self.assertRaises(direct_plan.DirectTripError) as raised:
                await direct_plan.plan_direct_trip(
                    gtfs=None,
                    origin_lat=40.7,
                    origin_lng=-73.99,
                    destination="Test Dest",
                    destination_lat=None,
                    destination_lng=None,
                    timings={},
                )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail, "Trip planning is temporarily unavailable."
        )

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
        self.assertEqual(result, {"route": "Test Dest"})


if __name__ == "__main__":
    unittest.main()
