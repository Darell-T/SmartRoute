"""Regression tests for route timing reconciliation and event serialization."""

from __future__ import annotations

import unittest

from app.services.agent import events as agent_events
from app.services.agent.tools.route import route_projection


class FirstLegTimingTests(unittest.TestCase):
    def test_live_first_boarding_reconciles_wait_total_and_arrival_clock(self):
        itinerary = {
            "total_duration_seconds": 7 * 60,
            "total_walk_seconds": 3 * 60,
            "total_street_walking_seconds": 3 * 60,
            "total_in_station_transfer_seconds": 0,
            "total_wait_seconds": 0,
            "total_in_vehicle_seconds": 4 * 60,
            "total_transfer_seconds": 0,
            "total_dwell_seconds": 0,
            "departure_at": None,
            "arrival_at": None,
            "legs": [
                {
                    "mode": "WALK",
                    "walk_seconds": 2 * 60,
                    "street_walking_seconds": 2 * 60,
                    "in_station_transfer_seconds": 0,
                    "wait_seconds": 0,
                    "ride_seconds": 0,
                    "transfer_seconds": 0,
                },
                {
                    "mode": "SUBWAY",
                    "walk_seconds": 0,
                    "street_walking_seconds": 0,
                    "in_station_transfer_seconds": 0,
                    "wait_seconds": 0,
                    "ride_seconds": 4 * 60,
                    "transfer_seconds": 0,
                },
                {
                    "mode": "WALK",
                    "walk_seconds": 60,
                    "street_walking_seconds": 60,
                    "in_station_transfer_seconds": 0,
                    "wait_seconds": 0,
                    "ride_seconds": 0,
                    "transfer_seconds": 0,
                },
            ],
        }

        reconciled = route_projection.reconcile_first_boarding_timing(
            itinerary,
            {
                "source_status": "live",
                "walking_minutes": 2,
                "catchable_arrival_minutes": 12,
            },
            now_iso="2026-08-13T17:00:00-04:00",
        )

        self.assertEqual(reconciled["total_wait_seconds"], 10 * 60)
        self.assertEqual(reconciled["total_duration_seconds"], 17 * 60)
        self.assertEqual(
            reconciled["total_duration_seconds"],
            reconciled["total_walk_seconds"]
            + reconciled["total_wait_seconds"]
            + reconciled["total_in_vehicle_seconds"]
            + reconciled["total_transfer_seconds"]
            + reconciled["total_dwell_seconds"],
        )
        self.assertEqual(reconciled["departure_at"], "2026-08-13T17:00:00-04:00")
        self.assertEqual(reconciled["arrival_at"], "2026-08-13T17:17:00-04:00")

    def test_impossible_live_boarding_does_not_corrupt_canonical_timing(self):
        itinerary = {
            "total_duration_seconds": 15 * 60,
            "total_walk_seconds": 8 * 60,
            "total_wait_seconds": 0,
            "total_in_vehicle_seconds": 7 * 60,
            "total_transfer_seconds": 0,
            "total_dwell_seconds": 0,
            "legs": [
                {
                    "mode": "WALK",
                    "street_walking_seconds": 8 * 60,
                    "wait_seconds": 0,
                    "ride_seconds": 0,
                    "transfer_seconds": 0,
                },
                {
                    "mode": "SUBWAY",
                    "street_walking_seconds": 0,
                    "wait_seconds": 0,
                    "ride_seconds": 7 * 60,
                    "transfer_seconds": 0,
                },
            ],
        }

        reconciled = route_projection.reconcile_first_boarding_timing(
            itinerary,
            {"source_status": "live", "catchable_arrival_minutes": 5},
            now_iso="2026-08-13T17:00:00-04:00",
        )

        self.assertIs(reconciled, itinerary)
        self.assertEqual(reconciled["total_duration_seconds"], 15 * 60)


class RouteCardEventItineraryWireTests(unittest.TestCase):
    def test_to_data_omits_itinerary_when_none(self):
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
        itinerary = {
            "itinerary_id": "rc_x",
            "total_duration_seconds": 120,
            "transfer_count": 0,
        }
        event = agent_events.RouteCardEvent(
            card_id="rc_x",
            turn_id="t1",
            role="recommended",
            origin={"label": "A"},
            destination={"label": "B"},
            summary={"eta_minutes": 2, "transfers": 0, "lines": [], "reason": None},
            route=[],
            alerts=[],
            itinerary=itinerary,
        )
        data = event.to_data()
        self.assertEqual(data["itinerary"], itinerary)


class PatternStopListProjectionTests(unittest.TestCase):
    def test_copies_provider_stop_names_onto_empty_itinerary_legs(self):
        itinerary = {
            "legs": [
                {
                    "mode": "WALK",
                    "board": {"name": "Your location"},
                    "alight": {"name": "Canal St"},
                },
                {
                    "mode": "SUBWAY",
                    "service_id": "A",
                    "board": {"name": "Canal St"},
                    "alight": {"name": "Jay St-MetroTech"},
                    "stop_count": 4,
                    "stops": [],
                },
            ]
        }
        route = [
            {"type": "WALK"},
            {
                "type": "SUBWAY",
                "route_id": "A",
                "departure_stop": "Canal St",
                "arrival_stop": "Jay St-MetroTech",
                "intermediate_stops": [
                    "Canal St",
                    "Chambers St",
                    "Fulton St",
                    "High St",
                    "Jay St-MetroTech",
                ],
            },
        ]

        route_projection.attach_pattern_stop_lists(itinerary, route, gtfs=None)

        self.assertEqual(
            [stop["name"] for stop in itinerary["legs"][1]["stops"]],
            [
                "Canal St",
                "Chambers St",
                "Fulton St",
                "High St",
                "Jay St-MetroTech",
            ],
        )

    def test_fills_empty_legs_from_local_pattern_index_without_mutating_route(self):
        itinerary = {
            "legs": [
                {
                    "mode": "SUBWAY",
                    "service_id": "A",
                    "board": {"name": "Canal St"},
                    "alight": {"name": "Jay St-MetroTech"},
                    "stop_count": 4,
                }
            ]
        }
        route = [
            {
                "type": "SUBWAY",
                "route_id": "A",
                "departure_stop": "Canal St",
                "arrival_stop": "Jay St-MetroTech",
            }
        ]
        calls = []

        class FakeGtfs:
            def get_intermediate_stops_with_coords(
                self, route_id, origin, dest, origin_coords=None, dest_coords=None
            ):
                calls.append((route_id, origin, dest, origin_coords, dest_coords))
                return [
                    {"name": origin, "lat": 40.718, "lng": -74.0},
                    {"name": "Chambers St", "lat": 40.715, "lng": -74.009},
                    {"name": dest, "lat": 40.692, "lng": -73.987},
                ]

        route_projection.attach_pattern_stop_lists(itinerary, route, FakeGtfs())

        self.assertEqual(
            [stop["name"] for stop in itinerary["legs"][0]["stops"]],
            ["Canal St", "Chambers St", "Jay St-MetroTech"],
        )
        self.assertEqual(calls, [("A", "Canal St", "Jay St-MetroTech", None, None)])
        self.assertNotIn("intermediate_stops", route[0])

    def test_does_not_overwrite_existing_named_stops(self):
        itinerary = {
            "legs": [
                {
                    "mode": "SUBWAY",
                    "service_id": "A",
                    "stops": [{"name": "Spring St"}],
                }
            ]
        }

        class FakeGtfs:
            def get_intermediate_stops_with_coords(self, *_args, **_kwargs):
                return [{"name": "Should not replace"}]

        route_projection.attach_pattern_stop_lists(
            itinerary,
            [{"type": "SUBWAY", "route_id": "A"}],
            FakeGtfs(),
        )
        self.assertEqual(itinerary["legs"][0]["stops"], [{"name": "Spring St"}])


if __name__ == "__main__":
    unittest.main()
