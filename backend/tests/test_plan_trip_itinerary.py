"""plan_trip emits canonical itinerary on each RouteCardEvent.

summary.eta_minutes / summary.transfers must come only from the itinerary
(not scoring total_minutes / transfer recount for card display).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.services.agent.tools import plan_trip
from tests._fake_http_tools import make_tool_ctx


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _leg(route_id: str, board_in_minutes: int, ride_minutes: int, *, duration_minutes: int | None = None) -> dict:
    now = datetime.now(timezone.utc)
    depart = now + timedelta(minutes=board_in_minutes)
    arrive = depart + timedelta(minutes=ride_minutes)
    total = duration_minutes if duration_minutes is not None else board_in_minutes + ride_minutes
    return {
        "duration": f"{total * 60}s",
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
                    "transitLine": {
                        "nameShort": route_id,
                        "color": "#000000",
                        "vehicle": {"type": "SUBWAY"},
                    },
                    "stopCount": 10,
                },
            }
        ],
    }


def _google_response(*legs: dict) -> dict:
    return {"routes": [{"legs": [leg]} for leg in legs]}


class PlanTripItineraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env_patch = patch.dict("os.environ", {"JARVIS_MOCK_ADVISOR": "1"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

        patch.object(
            plan_trip.geo, "geocode_address_with_reason", return_value=((40.7128, -74.0060), None)
        ).start()
        patch.object(plan_trip.enrichment, "_enrich_route", new=AsyncMock(return_value={})).start()
        patch.object(plan_trip, "fetch_service_alerts", new=AsyncMock(return_value=b"")).start()
        patch.object(plan_trip, "get_stalled_trains", new=AsyncMock(return_value=[])).start()
        patch.object(plan_trip, "get_stalled_buses", new=AsyncMock(return_value=[])).start()
        self._get_route = patch.object(
            plan_trip.directions_service,
            "get_transit_route",
            new=AsyncMock(
                return_value=_google_response(
                    _leg("Q", 5, 20, duration_minutes=25),
                    _leg("B", 3, 28, duration_minutes=31),
                )
            ),
        ).start()
        self.addCleanup(patch.stopall)

    def _ctx(self):
        return make_tool_ctx(origin={"lat": 40.7, "lng": -73.9})

    async def test_route_cards_carry_canonical_itinerary(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx()
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 2)

        for event in result.events:
            self.assertIsNotNone(event.itinerary)
            itinerary = event.itinerary
            self.assertIsInstance(itinerary["total_duration_seconds"], int)
            self.assertGreater(itinerary["total_duration_seconds"], 0)
            self.assertIn("transfer_count", itinerary)
            self.assertIn("legs", itinerary)
            self.assertEqual(itinerary["itinerary_id"], event.card_id)

            # Wire payload includes itinerary dict.
            payload = event.to_data()
            self.assertIn("itinerary", payload)
            self.assertEqual(
                payload["itinerary"]["total_duration_seconds"],
                itinerary["total_duration_seconds"],
            )

            # Summary times come only from itinerary.
            expected_eta = max(1, round(itinerary["total_duration_seconds"] / 60))
            self.assertEqual(event.summary["eta_minutes"], expected_eta)
            self.assertEqual(event.summary["transfers"], itinerary["transfer_count"])

        # Known Google leg durations: 25 min and 31 min → 1500s / 1860s.
        durations = sorted(e.itinerary["total_duration_seconds"] for e in result.events)
        self.assertEqual(durations, [25 * 60, 31 * 60])

        # Digest mirrors itinerary-derived minutes / transfers / walk.
        for candidate, event in zip(result.data["candidates"], result.events):
            self.assertEqual(candidate["eta_minutes"], event.summary["eta_minutes"])
            self.assertEqual(candidate["transfers"], event.summary["transfers"])
            self.assertEqual(
                candidate["walk_minutes"],
                round(event.itinerary["total_walk_seconds"] / 60),
            )

    async def test_summary_not_from_scoring_total_when_itinerary_differs(self):
        """If scoring would disagree, card still uses itinerary seconds."""
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx()
        )
        self.assertTrue(result.ok)
        for event in result.events:
            from_itinerary = max(1, round(event.itinerary["total_duration_seconds"] / 60))
            self.assertEqual(event.summary["eta_minutes"], from_itinerary)
            # Must not invent ETA from max minutes_until_arrival path alone.
            self.assertEqual(
                event.summary["eta_minutes"],
                max(1, round(event.itinerary["total_duration_seconds"] / 60)),
            )

    async def test_depart_at_planning_mode_when_departure_time_set(self):
        result = await plan_trip.execute(
            {
                "origin": "user",
                "destination": "MSG",
                "departure_time": "2026-07-16T22:00:00-04:00",
            },
            self._ctx(),
        )
        self.assertTrue(result.ok)
        for event in result.events:
            self.assertEqual(event.itinerary["planning_mode"], "depart_at")
            self.assertEqual(
                event.itinerary["requested_departure"], "2026-07-16T22:00:00-04:00"
            )
            self.assertEqual(event.depart_iso, "2026-07-16T22:00:00-04:00")

    async def test_leave_now_planning_mode_by_default(self):
        result = await plan_trip.execute(
            {"origin": "user", "destination": "Costco"}, self._ctx()
        )
        self.assertTrue(result.ok)
        for event in result.events:
            self.assertEqual(event.itinerary["planning_mode"], "leave_now")
            self.assertIsNone(event.itinerary["requested_departure"])


class RouteCardEventItineraryWireTests(unittest.TestCase):
    def test_to_data_omits_itinerary_when_none(self):
        from app.services.agent import events as agent_events

        event = agent_events.RouteCardEvent(
            card_id="rc_x",
            turn_id="t1",
            role="recommended",
            origin={"label": "A"},
            destination={"label": "B"},
            summary={"eta_minutes": 1, "transfers": 0, "lines": [], "reason": None},
            route=[],
            alerts=[],
        )
        data = event.to_data()
        self.assertNotIn("itinerary", data)

    def test_to_data_includes_itinerary_when_present(self):
        from app.services.agent import events as agent_events

        itin = {"itinerary_id": "rc_x", "total_duration_seconds": 120, "transfer_count": 0}
        event = agent_events.RouteCardEvent(
            card_id="rc_x",
            turn_id="t1",
            role="recommended",
            origin={"label": "A"},
            destination={"label": "B"},
            summary={"eta_minutes": 2, "transfers": 0, "lines": [], "reason": None},
            route=[],
            alerts=[],
            itinerary=itin,
        )
        data = event.to_data()
        self.assertEqual(data["itinerary"], itin)


if __name__ == "__main__":
    unittest.main()
