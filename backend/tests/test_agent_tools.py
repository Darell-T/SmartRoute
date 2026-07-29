"""Layer-1 tests for the P0 agent tools (plan_trip, transit_snapshot).

Real anthropic/fastapi imports work in this environment, and
JARVIS_MOCK_ADVISOR=1 makes the judge deterministic without a network call,
so these tests patch only the specific I/O boundaries each tool touches
(Google Routes, MTA feeds, geocoding, GTFS enrichment) rather than faking
the whole `anthropic` module -- that's reserved for test_agent_loop.py,
which needs to script the orchestrator model itself.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.services.agent.tools import plan_trip, transit_snapshot
from tests._fake_http_tools import make_tool_ctx


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _leg(route_id: str, board_in_minutes: int, ride_minutes: int) -> dict:
    now = datetime.now(timezone.utc)
    depart = now + timedelta(minutes=board_in_minutes)
    arrive = depart + timedelta(minutes=ride_minutes)
    return {
        "duration": f"{(board_in_minutes + ride_minutes) * 60}s",
        "steps": [
            {
                "travelMode": "TRANSIT",
                "polyline": {"encodedPolyline": "poly"},
                "transitDetails": {
                    "stopDetails": {
                        "departureStop": {
                            "name": f"{route_id} Start",
                            "location": {"latLng": {"latitude": 40.75, "longitude": -73.98}},
                        },
                        "arrivalStop": {
                            "name": f"{route_id} End",
                            "location": {"latLng": {"latitude": 40.76, "longitude": -73.99}},
                        },
                        "departureTime": _iso(depart),
                        "arrivalTime": _iso(arrive),
                    },
                    "headsign": "Uptown",
                    "transitLine": {"nameShort": route_id, "color": "#000000", "vehicle": {"type": "SUBWAY"}},
                    "stopCount": 10,
                },
            }
        ],
    }


def _google_response(*legs: dict) -> dict:
    return {"routes": [{"legs": [leg]} for leg in legs]}


NYC_COORDS = (40.7128, -74.0060)


class PlanTripToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env_patch = patch.dict("os.environ", {"SMARTROUTE_ENV": "test", "JARVIS_MOCK_ADVISOR": "1"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

        self._geocode = patch.object(
            plan_trip.geo, "geocode_address_with_reason", return_value=(NYC_COORDS, None)
        ).start()
        self._enrich = patch.object(plan_trip.enrichment, "_enrich_route", new=AsyncMock(return_value={})).start()
        self._alerts = patch.object(plan_trip, "fetch_service_alerts", new=AsyncMock(return_value=b"")).start()
        self._stalled_trains = patch.object(plan_trip, "get_stalled_trains", new=AsyncMock(return_value=[])).start()
        self._stalled_buses = patch.object(plan_trip, "get_stalled_buses", new=AsyncMock(return_value=[])).start()
        self._get_route = patch.object(
            plan_trip.directions_service,
            "get_transit_route",
            new=AsyncMock(return_value=_google_response(_leg("Q", 5, 20), _leg("B", 3, 28))),
        ).start()
        self.addCleanup(patch.stopall)

    def _ctx(self, origin=None, gtfs=None):
        return make_tool_ctx(origin, gtfs=gtfs)

    async def test_destination_required(self):
        result = await plan_trip.execute({"origin": "user", "destination": ""}, self._ctx())
        self.assertFalse(result.ok)
        self.assertIn("destination", result.error)

    async def test_exclude_all_modes_is_rejected_without_network_calls(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco", "exclude_modes": ["BUS", "SUBWAY"]},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        self.assertFalse(result.ok)
        self.assertIn("no transit modes left", result.error)
        self._get_route.assert_not_awaited()
        self._geocode.assert_not_called()

    async def test_exclude_bus_maps_to_allowed_travel_modes_subway_only(self):
        await plan_trip.execute(
            {"origin": "user", "destination": "Costco", "exclude_modes": ["BUS"]},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        _args, kwargs = self._get_route.call_args
        self.assertEqual(kwargs["allowed_travel_modes"], ["SUBWAY"])

    async def test_departure_time_is_passed_through_to_directions(self):
        await plan_trip.execute(
            {"origin": "user", "destination": "MSG", "departure_time": "2026-07-16T22:00:00-04:00"},
            self._ctx(origin={"lat": 40.7, "lng": -73.9}),
        )
        _args, kwargs = self._get_route.call_args
        self.assertEqual(kwargs["departure_time"], "2026-07-16T22:00:00-04:00")

    async def test_origin_user_without_gps_asks_for_location(self):
        result = await plan_trip.execute({"origin": "user", "destination": "Costco"}, self._ctx(origin=None))
        self.assertFalse(result.ok)
        self.assertIn("location", result.error.lower())
        self._get_route.assert_not_awaited()

    async def test_origin_address_is_geocoded(self):
        await plan_trip.execute(
            {"origin": "350 5th Ave", "destination": "Costco"},
            self._ctx(origin=None),
        )
        self._geocode.assert_any_call("350 5th Ave")

    async def test_destination_geocode_failure_is_reported(self):
        # origin="user" resolves from ctx.origin without a geocode call, so
        # the only geocode() call is for the destination.
        self._geocode.side_effect = [(None, "Address not found in NYC.")]
        result = await plan_trip.execute(
            {"origin": "user", "destination": "nowhere"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Address not found in NYC.")

    async def test_successful_call_returns_digest_route_cards_and_session_cards(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertTrue(result.ok)
        candidates = result.data["candidates"]
        self.assertEqual(len(candidates), 2)
        for candidate in candidates:
            self.assertTrue(candidate["card_id"].startswith("rc_"))
            self.assertIn("lines", candidate)
            self.assertIn("eta_minutes", candidate)
            self.assertIn("transfers", candidate)
            self.assertIn("reason", candidate)
            # No route geometry leaks into the model-facing digest.
            self.assertNotIn("polyline", candidate)
            self.assertNotIn("route", candidate)

        self.assertEqual(len(result.events), 2)
        card_ids = {card["card_id"] for card in result.session_route_cards}
        self.assertEqual(card_ids, {c["card_id"] for c in candidates})
        roles = {event.role for event in result.events}
        self.assertEqual(roles, {"recommended", "alternative"})

        recommended = next(event for event in result.events if event.role == "recommended")
        self.assertEqual(recommended.turn_id, "t1")
        self.assertTrue(any(step.get("type") == "SUBWAY" for step in recommended.route))

    async def test_no_routes_found_is_reported(self):
        self._get_route.return_value = {"routes": []}
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertFalse(result.ok)
        self.assertIn("no transit route", result.error)

    async def test_google_routes_error_is_reported_without_traceback(self):
        self._get_route.side_effect = plan_trip.directions_service.GoogleRoutesError("timeout", "boom")
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
        )
        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error)
        self.assertNotIn("Traceback", result.error)

    async def test_include_incident_scan_false_by_default_skips_grok(self):
        with patch.object(plan_trip.trip_incidents, "_scan_route_incidents", new=AsyncMock(return_value=[])) as scan:
            await plan_trip.execute(
                {"origin": "user", "destination": "Costco"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
            )
            scan.assert_not_awaited()

    async def test_include_incident_scan_true_invokes_grok_scan(self):
        with patch.object(
            plan_trip.trip_incidents, "_scan_route_incidents", new=AsyncMock(return_value=[{"location": "X"}])
        ) as scan:
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Costco", "include_incident_scan": True},
                self._ctx(origin={"lat": 40.7, "lng": -73.9}),
            )
            scan.assert_awaited_once()
        self.assertTrue(result.ok)

    async def test_plan_trip_always_uses_shared_intelligence_advisor_mode(self):
        captured = {}

        async def capture_advisor(payload):
            captured["payload"] = payload
            return "[ROUTE:0] Take the Q."

        with patch.object(plan_trip.ai_advisor, "collect_recommendation", new=capture_advisor):
            result = await plan_trip.execute(
                {"origin": "user", "destination": "Costco"},
                self._ctx(origin={"lat": 40.7, "lng": -73.9}),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured["payload"]["planning_mode"], "intelligence")
        self.assertIn("route_candidate_labels", captured["payload"])


class TransitSnapshotToolTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self, origin=None, gtfs="fake-gtfs"):
        return make_tool_ctx(origin, gtfs=gtfs)

    async def test_near_user_without_gps_asks_for_location(self):
        result = await transit_snapshot.execute({"near": "user"}, self._ctx(origin=None))
        self.assertFalse(result.ok)
        self.assertIn("location", result.error.lower())

    async def test_near_resolves_and_builds_snapshot(self):
        snapshot = {
            "nearest_stop": {"stop_name": "Church Av", "distance_m": 50},
            "arrivals": [{"route_id": "Q", "station_name": "Church Av", "arrival_time": 123}],
            "alerts": [{"header": "Delays on Q", "route_ids": ["Q"]}],
            "signals": {"network_status": "healthy"},
        }
        with patch.object(
            transit_snapshot, "_build_live_snapshot", new=AsyncMock(return_value=snapshot)
        ) as build_snapshot:
            result = await transit_snapshot.execute(
                {"near": "user"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
            )
            build_snapshot.assert_awaited_once_with("fake-gtfs", 40.7, -73.9)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["network_status"], "healthy")
        self.assertEqual(len(result.data["arrivals"]), 1)

    async def test_near_without_gtfs_ready_is_reported(self):
        result = await transit_snapshot.execute(
            {"near": "user"}, self._ctx(origin={"lat": 40.7, "lng": -73.9}, gtfs=None)
        )
        self.assertFalse(result.ok)
        self.assertIn("not ready", result.error)

    async def test_lines_filter_alerts_without_location(self):
        alerts = [
            {"header": "Q delayed", "route_ids": ["Q"]},
            {"header": "B suspended", "route_ids": ["B"]},
        ]
        with patch.object(transit_snapshot.mta_feed, "fetch_service_alerts", new=AsyncMock(return_value=b"x")), \
             patch.object(transit_snapshot.mta_feed, "parse_service_alerts", return_value=alerts), \
             patch.object(
                 transit_snapshot.mta_feed,
                 "filter_alerts_for_routes",
                 side_effect=lambda parsed, route_ids: [a for a in parsed if set(a["route_ids"]) & route_ids],
             ):
            result = await transit_snapshot.execute({"lines": ["Q"]}, self._ctx(origin=None))
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["alerts"]), 1)
        self.assertIn("Q", result.data["alerts"][0]["header"])

    async def test_alert_headline_is_capped_via_safe_text(self):
        long_header = "X" * 500
        with patch.object(transit_snapshot.mta_feed, "fetch_service_alerts", new=AsyncMock(return_value=b"x")), \
             patch.object(
                 transit_snapshot.mta_feed, "parse_service_alerts", return_value=[{"header": long_header, "route_ids": []}]
             ):
            result = await transit_snapshot.execute({}, self._ctx(origin=None))
        self.assertLessEqual(len(result.data["alerts"][0]["header"]), 200)


if __name__ == "__main__":
    unittest.main()
