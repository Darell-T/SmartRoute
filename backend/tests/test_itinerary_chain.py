"""Tests for multi-stop chained itinerary builder (server-owned dwell).

Two OD segments + default 25 min waypoint dwell: totals and waypoints must
come from the server chain helper, not frontend invent.
"""

from __future__ import annotations

import unittest


def _subway_steps(
    *,
    line: str,
    board: str,
    alight: str,
    dep_iso: str,
    arr_iso: str,
    route_total_minutes: int,
) -> list[dict]:
    return [
        {
            "type": "SUBWAY",
            "route_id": line,
            "train_line": line,
            "departure_stop": board,
            "arrival_stop": alight,
            "departure_time_iso": dep_iso,
            "arrival_time_iso": arr_iso,
            "minutes_until_arrival": 999.0,
            "route_total_minutes": route_total_minutes,
            "polyline": {"encodedPolyline": f"{line}_poly"},
        }
    ]


class BuildChainedItineraryTests(unittest.TestCase):
    def test_two_segments_default_25_min_dwell(self):
        from app.services.trips.itinerary import build_chained_itinerary

        # Leg 1: 15 min door-to-door → intermediate stop
        # Leg 2: 20 min door-to-door → final destination
        # Dwell default 25 min between them
        seg1_steps = _subway_steps(
            line="Q",
            board="Prospect Park",
            alight="Canal St",
            dep_iso="2026-07-23T09:00:00-04:00",
            arr_iso="2026-07-23T09:15:00-04:00",
            route_total_minutes=15,
        )
        seg2_steps = _subway_steps(
            line="N",
            board="Canal St",
            alight="Times Sq",
            dep_iso="2026-07-23T09:40:00-04:00",
            arr_iso="2026-07-23T10:00:00-04:00",
            route_total_minutes=20,
        )

        result = build_chained_itinerary(
            [
                {
                    "steps": seg1_steps,
                    "destination_place": "Joe's Pizza",
                },
                {
                    "steps": seg2_steps,
                    "destination_place": "Times Square",
                },
            ],
            origin="Prospect Park",
            final_destination="Times Square",
            planning_mode="leave_now",
            generated_at="2026-07-23T08:55:00-04:00",
            itinerary_id="chain-1",
            reasons=["Multi-stop with pickup"],
        )

        self.assertEqual(result["itinerary_id"], "chain-1")
        self.assertEqual(result["origin"], "Prospect Park")
        self.assertEqual(result["destination"], "Times Square")
        self.assertEqual(result["timezone"], "America/New_York")
        self.assertEqual(result["planning_mode"], "leave_now")
        self.assertEqual(result["generated_at"], "2026-07-23T08:55:00-04:00")
        self.assertEqual(
            result["structured_recommendation_reasons"],
            ["Multi-stop with pickup"],
        )

        # One intermediate waypoint with server default dwell
        self.assertEqual(len(result["waypoints"]), 1)
        wp = result["waypoints"][0]
        self.assertEqual(wp["display_name"], "Joe's Pizza")
        self.assertEqual(wp["dwell_minutes"], 25)
        self.assertEqual(wp["dwell_source"], "default")

        self.assertEqual(result["total_dwell_seconds"], 25 * 60)
        # Segment totals 15+20 min + 25 dwell = 60 min = 3600 s
        self.assertEqual(result["total_duration_seconds"], (15 + 20 + 25) * 60)
        self.assertEqual(result["total_in_vehicle_seconds"], 15 * 60 + 20 * 60)

        # Legs concatenated (one subway leg per segment)
        self.assertEqual(len(result["legs"]), 2)
        self.assertEqual(result["legs"][0]["service_id"], "Q")
        self.assertEqual(result["legs"][1]["service_id"], "N")
        self.assertEqual(result["legs"][0]["ride_seconds"], 15 * 60)
        self.assertEqual(result["legs"][1]["ride_seconds"], 20 * 60)

        # Clocks from first / last segment
        self.assertEqual(result["departure_at"], "2026-07-23T09:00:00-04:00")
        self.assertEqual(result["arrival_at"], "2026-07-23T10:00:00-04:00")

        # A service change at the waypoint remains a transfer even though the
        # provider planned each OD segment independently.
        self.assertEqual(result["transfer_count"], 1)
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["segments"][0]["destination"]["display_name"], "Joe's Pizza")
        self.assertEqual(result["segments"][1]["destination"], "Times Square")
        self.assertEqual(result["segments"][0]["legs"][0]["segment_index"], 0)
        self.assertEqual(result["segments"][1]["legs"][0]["segment_index"], 1)
        self.assertEqual(result["dwell_events"], [{
            "event_type": "dwell",
            "after_segment_index": 0,
            "waypoint": wp,
            "duration_seconds": 25 * 60,
            "source": "default",
        }])

    def test_user_specified_dwell_source(self):
        from app.services.trips.itinerary import build_chained_itinerary

        seg1 = _subway_steps(
            line="B",
            board="A",
            alight="B",
            dep_iso="2026-07-23T12:00:00-04:00",
            arr_iso="2026-07-23T12:10:00-04:00",
            route_total_minutes=10,
        )
        seg2 = _subway_steps(
            line="D",
            board="B",
            alight="C",
            dep_iso="2026-07-23T12:25:00-04:00",
            arr_iso="2026-07-23T12:40:00-04:00",
            route_total_minutes=15,
        )

        result = build_chained_itinerary(
            [
                {
                    "steps": seg1,
                    "destination_place": {
                        "display_name": "Coffee Shop",
                        "place_id": "poi-1",
                        "lat": 40.72,
                        "lng": -74.0,
                    },
                    "dwell_minutes": 12,
                },
                {
                    "steps": seg2,
                    "destination_place": "Final",
                },
            ],
            origin="Home",
            final_destination="Work",
        )

        self.assertEqual(len(result["waypoints"]), 1)
        wp = result["waypoints"][0]
        self.assertEqual(wp["display_name"], "Coffee Shop")
        self.assertEqual(wp["place_id"], "poi-1")
        self.assertEqual(wp["lat"], 40.72)
        self.assertEqual(wp["lng"], -74.0)
        self.assertEqual(wp["dwell_minutes"], 12)
        self.assertEqual(wp["dwell_source"], "user")
        self.assertEqual(result["total_dwell_seconds"], 12 * 60)
        self.assertEqual(result["total_duration_seconds"], (10 + 15 + 12) * 60)
        self.assertEqual(result["destination"], "Work")

    def test_three_segments_two_waypoints(self):
        from app.services.trips.itinerary import build_chained_itinerary

        segments = []
        for i, (mins, place) in enumerate(
            [(10, "Stop A"), (8, "Stop B"), (12, "End")]
        ):
            dep_h = 9 + i
            segments.append(
                {
                    "steps": _subway_steps(
                        line=str(i + 1),
                        board=f"B{i}",
                        alight=f"A{i}",
                        dep_iso=f"2026-07-23T{dep_h:02d}:00:00-04:00",
                        arr_iso=f"2026-07-23T{dep_h:02d}:{mins:02d}:00-04:00",
                        route_total_minutes=mins,
                    ),
                    "destination_place": place,
                    # Explicit default-style dwell on intermediates only used
                    # for first two; third is final and ignored for waypoints.
                }
            )

        result = build_chained_itinerary(
            segments,
            origin="Start",
            final_destination="End",
        )

        self.assertEqual(len(result["waypoints"]), 2)
        self.assertEqual(
            [wp["display_name"] for wp in result["waypoints"]],
            ["Stop A", "Stop B"],
        )
        for wp in result["waypoints"]:
            self.assertEqual(wp["dwell_minutes"], 25)
            self.assertEqual(wp["dwell_source"], "default")
        self.assertEqual(result["total_dwell_seconds"], 2 * 25 * 60)
        self.assertEqual(
            result["total_duration_seconds"],
            (10 + 8 + 12 + 25 + 25) * 60,
        )
        self.assertEqual(len(result["legs"]), 3)
        self.assertEqual(result["departure_at"], "2026-07-23T09:00:00-04:00")
        self.assertEqual(result["arrival_at"], "2026-07-23T11:12:00-04:00")

    def test_single_segment_no_waypoints_or_dwell(self):
        from app.services.trips.itinerary import build_chained_itinerary

        steps = _subway_steps(
            line="Q",
            board="A",
            alight="B",
            dep_iso="2026-07-23T10:00:00-04:00",
            arr_iso="2026-07-23T10:20:00-04:00",
            route_total_minutes=20,
        )
        result = build_chained_itinerary(
            [{"steps": steps, "destination_place": "B"}],
            origin="A",
            final_destination="B",
        )
        self.assertEqual(result["waypoints"], [])
        self.assertEqual(result["total_dwell_seconds"], 0)
        self.assertEqual(result["total_duration_seconds"], 20 * 60)
        self.assertEqual(len(result["legs"]), 1)

    def test_empty_segments_raises(self):
        from app.services.trips.itinerary import build_chained_itinerary

        with self.assertRaises(ValueError):
            build_chained_itinerary(
                [],
                origin="A",
                final_destination="B",
            )

    def test_transfer_count_includes_cross_segment_service_change(self):
        from app.services.trips.itinerary import build_chained_itinerary

        # Segment 1: two subway legs → 1 transfer
        seg1_steps = [
            {
                "type": "SUBWAY",
                "route_id": "B",
                "train_line": "B",
                "departure_stop": "PP",
                "arrival_stop": "Atl",
                "departure_time_iso": "2026-07-23T09:00:00-04:00",
                "arrival_time_iso": "2026-07-23T09:10:00-04:00",
                "route_total_minutes": 25,
            },
            {
                "type": "SUBWAY",
                "route_id": "2",
                "train_line": "2",
                "departure_stop": "Atl",
                "arrival_stop": "TS",
                "departure_time_iso": "2026-07-23T09:15:00-04:00",
                "arrival_time_iso": "2026-07-23T09:25:00-04:00",
                "route_total_minutes": 25,
            },
        ]
        seg2_steps = _subway_steps(
            line="N",
            board="TS",
            alight="QNS",
            dep_iso="2026-07-23T09:50:00-04:00",
            arr_iso="2026-07-23T10:05:00-04:00",
            route_total_minutes=15,
        )
        result = build_chained_itinerary(
            [
                {"steps": seg1_steps, "destination_place": "Times Sq"},
                {"steps": seg2_steps, "destination_place": "Queens"},
            ],
            origin="Prospect Park",
            final_destination="Queens",
        )
        # 1 transfer in seg1 + B/2 -> N cross-segment service change;
        # dwell is not a transfer.
        self.assertEqual(result["transfer_count"], 2)
        self.assertEqual(result["total_dwell_seconds"], 25 * 60)
        self.assertEqual(result["total_duration_seconds"], (25 + 15 + 25) * 60)


if __name__ == "__main__":
    unittest.main()
