"""Tests for pure canonical itinerary builder (no network).

Covers WALK→SUBWAY→WALK normalization: seconds totals, ISO-based ride
duration (not minutes_until_arrival), walk haversine estimate, transfer_count.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.utils import geo

ET = ZoneInfo("America/New_York")


def _walk_step(
    *,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    route_total_minutes: int = 25,
) -> dict:
    return {
        "type": "WALK",
        "start_point": {"latitude": lat1, "longitude": lon1},
        "end_point": {"latitude": lat2, "longitude": lon2},
        "route_total_minutes": route_total_minutes,
        "polyline": {"encodedPolyline": "walk_poly"},
    }


def _subway_step(
    *,
    line: str = "Q",
    departure_stop: str = "Prospect Park",
    arrival_stop: str = "Canal St",
    departure_time_iso: str,
    arrival_time_iso: str,
    minutes_until_arrival: float = 999.0,
    minutes_until_train_arrives: float = 50.0,
    route_total_minutes: int = 25,
) -> dict:
    """minutes_until_* deliberately wrong so tests fail if used as duration."""
    return {
        "type": "SUBWAY",
        "route_id": line,
        "train_line": line,
        "departure_stop": departure_stop,
        "arrival_stop": arrival_stop,
        "departure_coords": {"latitude": 40.6616, "longitude": -73.9622},
        "arrival_coords": {"latitude": 40.7180, "longitude": -74.0000},
        "minutes_until_train_arrives": minutes_until_train_arrives,
        "minutes_until_arrival": minutes_until_arrival,
        "departure_time_iso": departure_time_iso,
        "arrival_time_iso": arrival_time_iso,
        "route_total_minutes": route_total_minutes,
        "polyline": {"encodedPolyline": "subway_poly"},
    }


def _walk_subway_walk_fixture() -> list[dict]:
    """~400m access walk, 12 min subway ride by ISO, short egress walk."""
    # Prospect Park area → nearby station (~280 m)
    walk1 = _walk_step(
        lat1=40.6602,
        lon1=-73.9690,
        lat2=40.6616,
        lon2=-73.9622,
        route_total_minutes=25,
    )
    subway = _subway_step(
        departure_time_iso="2026-07-23T09:10:00-04:00",
        arrival_time_iso="2026-07-23T09:22:00-04:00",  # 12 min = 720 s
        minutes_until_arrival=999.0,  # must NOT become ride duration
        route_total_minutes=25,
    )
    # Canal St area short egress (~150 m)
    walk2 = _walk_step(
        lat1=40.7180,
        lon1=-74.0000,
        lat2=40.7190,
        lon2=-74.0010,
        route_total_minutes=25,
    )
    return [walk1, subway, walk2]


class BuildCanonicalItineraryTests(unittest.TestCase):
    def test_preserves_provider_seconds_before_legacy_minute_alias(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = _walk_subway_walk_fixture()
        for step in steps:
            step["route_total_seconds"] = 1_531
            step["route_total_minutes"] = 26  # legacy alias must not win

        result = build_canonical_itinerary(steps, origin="A", destination="B")
        self.assertEqual(result["total_duration_seconds"], 1_531)

    def test_walk_subway_walk_totals_and_transfer_count(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = _walk_subway_walk_fixture()
        result = build_canonical_itinerary(
            steps,
            origin="Prospect Park",
            destination="Canal St",
            planning_mode="leave_now",
            generated_at="2026-07-23T09:00:00-04:00",
            itinerary_id="it-test-1",
        )

        self.assertEqual(result["itinerary_id"], "it-test-1")
        self.assertEqual(result["origin"], "Prospect Park")
        self.assertEqual(result["destination"], "Canal St")
        self.assertEqual(result["waypoints"], [])
        self.assertEqual(result["timezone"], "America/New_York")
        self.assertEqual(result["planning_mode"], "leave_now")
        self.assertEqual(result["data_basis"], "mixed")
        self.assertEqual(result["generated_at"], "2026-07-23T09:00:00-04:00")

        # Whole-trip total prefers Google route_total_minutes * 60
        self.assertEqual(result["total_duration_seconds"], 25 * 60)
        self.assertEqual(result["transfer_count"], 0)
        self.assertEqual(result["total_dwell_seconds"], 0)

        # Three legs: walk, subway, walk
        self.assertEqual(len(result["legs"]), 3)
        modes = [leg["mode"] for leg in result["legs"]]
        self.assertEqual(modes, ["WALK", "SUBWAY", "WALK"])

    def test_ride_seconds_from_iso_not_minutes_until_arrival(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = _walk_subway_walk_fixture()
        result = build_canonical_itinerary(
            steps,
            origin="A",
            destination="B",
        )
        subway_leg = result["legs"][1]
        # ISO delta is 12 minutes → 720 seconds; minutes_until_arrival is 999
        self.assertEqual(subway_leg["ride_seconds"], 720)
        self.assertNotEqual(subway_leg["ride_seconds"], 999 * 60)
        self.assertEqual(subway_leg["service_id"], "Q")
        self.assertEqual(subway_leg["board"], "Prospect Park")
        self.assertEqual(subway_leg["alight"], "Canal St")
        self.assertEqual(subway_leg["departure_at"], "2026-07-23T09:10:00-04:00")
        self.assertEqual(subway_leg["arrival_at"], "2026-07-23T09:22:00-04:00")

    def test_departure_arrival_at_from_absolute_iso(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = _walk_subway_walk_fixture()
        result = build_canonical_itinerary(steps, origin="A", destination="B")
        # First/last absolute times on the route (walk steps have no ISO)
        self.assertEqual(result["departure_at"], "2026-07-23T09:10:00-04:00")
        self.assertEqual(result["arrival_at"], "2026-07-23T09:22:00-04:00")

    def test_walk_seconds_from_haversine_not_magic_four_minutes(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = _walk_subway_walk_fixture()
        result = build_canonical_itinerary(steps, origin="A", destination="B")

        walk1 = result["legs"][0]
        walk2 = result["legs"][2]
        # Expected: meters / 1.4 m/s (same default as geo.walking_time_minutes)
        m1 = geo.distance_meters(40.6602, -73.9690, 40.6616, -73.9622)
        m2 = geo.distance_meters(40.7180, -74.0000, 40.7190, -74.0010)
        expected1 = max(0, int(round(m1 / 1.4)))
        expected2 = max(0, int(round(m2 / 1.4)))

        self.assertEqual(walk1["walk_seconds"], expected1)
        self.assertEqual(walk2["walk_seconds"], expected2)
        self.assertEqual(walk1["ride_seconds"], 0)
        self.assertEqual(walk2["ride_seconds"], 0)
        # Must not invent 4-minute walks (240 s) as scoring does
        self.assertNotEqual(walk1["walk_seconds"], 240)

        self.assertEqual(result["total_walk_seconds"], expected1 + expected2)
        self.assertEqual(result["total_in_vehicle_seconds"], 720)

    def test_no_invented_one_minute_transfer_filler(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = _walk_subway_walk_fixture()
        result = build_canonical_itinerary(steps, origin="A", destination="B")
        for leg in result["legs"]:
            self.assertEqual(leg["transfer_seconds"], 0)
        # Single transit → transfer_count 0; wait not fabricated as 60s filler
        self.assertEqual(result["transfer_count"], 0)

    def test_two_transit_legs_transfer_count(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = [
            _walk_step(lat1=40.66, lon1=-73.97, lat2=40.661, lon2=-73.962, route_total_minutes=40),
            _subway_step(
                line="B",
                departure_stop="Prospect Park",
                arrival_stop="Atlantic Av",
                departure_time_iso="2026-07-23T09:10:00-04:00",
                arrival_time_iso="2026-07-23T09:20:00-04:00",
                route_total_minutes=40,
            ),
            _subway_step(
                line="2",
                departure_stop="Atlantic Av",
                arrival_stop="Times Sq",
                departure_time_iso="2026-07-23T09:25:00-04:00",
                arrival_time_iso="2026-07-23T09:40:00-04:00",
                route_total_minutes=40,
            ),
        ]
        result = build_canonical_itinerary(steps, origin="A", destination="B")
        self.assertEqual(result["transfer_count"], 1)
        self.assertEqual(result["total_duration_seconds"], 40 * 60)
        # Ride seconds from ISO only
        self.assertEqual(result["legs"][1]["ride_seconds"], 600)
        self.assertEqual(result["legs"][2]["ride_seconds"], 900)
        self.assertEqual(result["total_in_vehicle_seconds"], 1500)
        # ISO gap between alight and next board is measurable transfer time
        self.assertEqual(result["legs"][2]["transfer_seconds"], 300)
        self.assertEqual(result["total_wait_seconds"] + result["legs"][2]["transfer_seconds"] >= 0, True)

    def test_total_duration_falls_back_to_component_sum_without_route_total(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = [
            {
                "type": "SUBWAY",
                "route_id": "Q",
                "train_line": "Q",
                "departure_stop": "A",
                "arrival_stop": "B",
                "departure_time_iso": "2026-07-23T10:00:00-04:00",
                "arrival_time_iso": "2026-07-23T10:15:00-04:00",
                "minutes_until_arrival": 99,
            }
        ]
        result = build_canonical_itinerary(steps, origin="A", destination="B")
        self.assertEqual(result["total_in_vehicle_seconds"], 900)
        self.assertEqual(result["total_duration_seconds"], 900)
        self.assertEqual(result["transfer_count"], 0)

    def test_walk_without_coords_is_zero_not_magic(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = [
            {"type": "WALK", "route_total_minutes": 10},
            _subway_step(
                departure_time_iso="2026-07-23T09:00:00-04:00",
                arrival_time_iso="2026-07-23T09:10:00-04:00",
                route_total_minutes=10,
            ),
        ]
        result = build_canonical_itinerary(steps, origin="A", destination="B")
        self.assertEqual(result["legs"][0]["walk_seconds"], 0)
        self.assertEqual(result["total_walk_seconds"], 0)

    def test_reasons_and_defaults(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        result = build_canonical_itinerary(
            [],
            origin="X",
            destination="Y",
            reasons=["Fastest option"],
        )
        self.assertEqual(result["origin"], "X")
        self.assertEqual(result["destination"], "Y")
        self.assertEqual(result["legs"], [])
        self.assertEqual(result["total_duration_seconds"], 0)
        self.assertEqual(result["transfer_count"], 0)
        self.assertEqual(result["structured_recommendation_reasons"], ["Fastest option"])
        self.assertIsNotNone(result["itinerary_id"])
        self.assertIn("data_freshness", result)
        # Required leg fields present on every leg when steps exist is covered above;
        # empty route still exposes totals keys.
        for key in (
            "total_walk_seconds",
            "total_wait_seconds",
            "total_in_vehicle_seconds",
            "total_dwell_seconds",
        ):
            self.assertIn(key, result)

    def test_leg_geometry_and_service_data_basis(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        steps = _walk_subway_walk_fixture()
        result = build_canonical_itinerary(
            steps,
            origin="A",
            destination="B",
            data_basis="realtime",
        )
        self.assertEqual(result["legs"][0]["geometry"], {"encodedPolyline": "walk_poly"})
        self.assertEqual(result["legs"][1]["geometry"], {"encodedPolyline": "subway_poly"})
        self.assertEqual(result["legs"][1]["service_data_basis"], "realtime")

    def test_preserves_provider_stop_count_and_ordered_stop_locations(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        step = _subway_step(
            line="B",
            departure_stop="Church Av",
            arrival_stop="Atlantic Av-Barclays Ctr",
            departure_time_iso="2026-07-23T09:10:00-04:00",
            arrival_time_iso="2026-07-23T09:21:00-04:00",
        )
        step["stop_count"] = 6
        step["intermediate_stop_locations"] = [
            {"name": "Church Av", "lat": 40.6505, "lng": -73.9629},
            {"name": "Beverley Rd", "lat": 40.6443, "lng": -73.9644},
            {"name": "Cortelyou Rd", "lat": 40.6409, "lng": -73.9639},
            {"name": "Newkirk Plaza", "lat": 40.6351, "lng": -73.9628},
            {"name": "Avenue H", "lat": 40.6293, "lng": -73.9617},
            {"name": "Atlantic Av-Barclays Ctr", "lat": 40.6844, "lng": -73.9777},
        ]

        result = build_canonical_itinerary([step], origin="A", destination="B")
        leg = result["legs"][0]

        self.assertEqual(leg["stop_count"], 6)
        self.assertEqual(
            [stop["name"] for stop in leg["stops"]],
            [
                "Church Av",
                "Beverley Rd",
                "Cortelyou Rd",
                "Newkirk Plaza",
                "Avenue H",
                "Atlantic Av-Barclays Ctr",
            ],
        )
        self.assertEqual(leg["stops"][1]["lat"], 40.6443)
        self.assertEqual(leg["stops"][1]["lng"], -73.9644)

    def test_does_not_fabricate_stops_when_provider_omits_them(self):
        from app.services.trips.itinerary import build_canonical_itinerary

        step = _subway_step(
            departure_time_iso="2026-07-23T09:10:00-04:00",
            arrival_time_iso="2026-07-23T09:22:00-04:00",
        )
        step["intermediate_stops"] = ["Prospect Park", "7 Av", "Canal St"]
        result = build_canonical_itinerary([step], origin="A", destination="B")

        self.assertEqual(
            result["legs"][0]["stops"],
            [{"name": "Prospect Park"}, {"name": "7 Av"}, {"name": "Canal St"}],
        )
        self.assertIsNone(result["legs"][0]["stop_count"])


if __name__ == "__main__":
    unittest.main()
