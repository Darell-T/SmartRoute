from __future__ import annotations

import unittest

from app.services.trips import crowd_hotspots


class _PatternIndex:
    def get_intermediate_stops_with_coords(self, *_args):
        return (
            [
                {"name": "Church Av", "lat": 40.65, "lng": -73.96},
                {"name": "34 St-Herald Sq", "lat": 40.75, "lng": -73.99},
                {"name": "57 St-7 Av", "lat": 40.765, "lng": -73.98},
            ],
            {},
        )


class _Gtfs:
    _pattern_index = _PatternIndex()


class CrowdHotspotTests(unittest.TestCase):
    def test_intermediate_stations_trigger_curated_hotspots(self):
        routes = [
            [
                {
                    "type": "SUBWAY",
                    "route_id": "Q",
                    "departure_stop": "Church Av",
                    "arrival_stop": "57 St-7 Av",
                    "departure_coords": {"latitude": 40.65, "longitude": -73.96},
                    "arrival_coords": {"latitude": 40.765, "longitude": -73.98},
                    "departure_time_iso": "2026-07-25T20:00:00-04:00",
                    "arrival_time_iso": "2026-07-25T20:40:00-04:00",
                }
            ]
        ]

        hits = crowd_hotspots.find_hotspot_hits(_Gtfs(), routes)

        self.assertEqual(
            [(hit.hotspot_key, hit.station_name) for hit in hits],
            [
                ("midtown_34", "34 St-Herald Sq"),
                ("columbus_lincoln", "57 St-7 Av"),
            ],
        )
        self.assertEqual(hits[0].route_id, "Q")
        self.assertIsNotNone(hits[0].expected_at)

    def test_route_outside_registry_does_not_trigger(self):
        routes = [
            [
                {
                    "type": "BUS",
                    "route_id": "B35",
                    "departure_stop": "Church Av",
                    "arrival_stop": "New York Av",
                    "departure_coords": {"latitude": 40.65, "longitude": -73.96},
                    "arrival_coords": {"latitude": 40.66, "longitude": -73.95},
                }
            ]
        ]

        self.assertEqual(crowd_hotspots.find_hotspot_hits(None, routes), [])
