import unittest

from app.utils.geo import find_nearest_stops


class FakeGTFS:
    def get_all_parent_stops(self):
        return [
            {
                "stop_id": "near",
                "stop_name": "Near Station",
                "stop_lat": 40.0,
                "stop_lon": -73.999,
            },
            {
                "stop_id": "mid",
                "stop_name": "Mid Station",
                "stop_lat": 40.0,
                "stop_lon": -73.992,
            },
            {
                "stop_id": "far",
                "stop_name": "Far Station",
                "stop_lat": 40.0,
                "stop_lon": -73.980,
            },
        ]


class FindNearestStopsTest(unittest.TestCase):
    def test_find_nearest_stops_can_filter_to_half_mile_radius(self):
        stops = find_nearest_stops(
            40.0,
            -74.0,
            FakeGTFS(),
            limit=10,
            radius_m=804.672,
        )

        self.assertEqual([stop["stop_id"] for stop in stops], ["near", "mid"])
        self.assertTrue(all(stop["distance_m"] <= 804.672 for stop in stops))


if __name__ == "__main__":
    unittest.main()
