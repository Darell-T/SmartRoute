"""GTFSStaticData live-map delegation: static in-memory methods bypass the DB.

The module does NOT open a psycopg2 pool at import time; ``_pool`` is lazy and
is only created by ``init_pool()`` from the FastAPI lifespan. Importing
``GTFSStaticData`` normally is therefore safe -- the driver is never faked and
the shared module is never reloaded (faking/reloading shared modules can
poison later tests in the same pytest process). Every test replaces the
instance ``_query`` with either a fail-fast recorder or a scripted fallback
before calling a DB-capable method, so no pool or connection is ever touched.

With a StopPatternIndex attached, get_all_parent_stops, get_route_ids_for_
parent_stop, and get_stop_locations must perform ZERO ``_query`` calls; the
PostgreSQL fallbacks stay byte-for-byte intact when no index is attached.
"""

import unittest
from pathlib import Path

from app.services.mta.static_gtfs.store import GTFSStaticData
from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex

_ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "stop_patterns.json"


class IndexDelegationTests(unittest.TestCase):
    def _gtfs_with_index(self, index):
        gtfs = GTFSStaticData()
        gtfs.set_pattern_index(index)
        calls = []

        def failing_query(sql, params=None):
            calls.append(sql)
            raise AssertionError(f"index attached: DB must not be queried ({sql})")

        gtfs._query = failing_query
        return gtfs, calls

    def test_three_methods_run_without_db_when_index_attached(self):
        artifact = {
            "stops": {
                "R14": {"name": "14 St-Union Sq", "lat": 40.7355, "lon": -73.9897},
                "R16": {"name": "Times Sq-42 St", "lat": 40.754672, "lon": -73.986754},
            },
            "patterns": [
                {"route_id": "N", "route_short_name": "N", "direction_id": 0,
                 "trip_count": 10, "signature": "n1", "stop_ids": ["R14", "R16"]},
                {"route_id": "R", "route_short_name": "R", "direction_id": 0,
                 "trip_count": 10, "signature": "r1", "stop_ids": ["R14", "R16"]},
                {"route_id": "W", "route_short_name": "W", "direction_id": 0,
                 "trip_count": 10, "signature": "w1", "stop_ids": ["R16"]},
                {"route_id": "Q", "route_short_name": "Q", "direction_id": 0,
                 "trip_count": 10, "signature": "q1", "stop_ids": ["R14"]},
            ],
        }
        gtfs, calls = self._gtfs_with_index(StopPatternIndex(artifact))

        parents = gtfs.get_all_parent_stops()
        self.assertEqual(
            [stop["stop_id"] for stop in parents],
            ["R14", "R16"],
        )
        self.assertEqual(
            parents[0],
            {
                "stop_id": "R14",
                "stop_name": "14 St-Union Sq",
                "stop_lat": 40.7355,
                "stop_lon": -73.9897,
            },
        )
        self.assertEqual(gtfs.get_route_ids_for_parent_stop("R16"), ["N", "R", "W"])
        self.assertEqual(gtfs.get_route_ids_for_parent_stop("R14"), ["N", "Q", "R"])
        self.assertEqual(gtfs.get_route_ids_for_parent_stop("unknown"), [])

        locations = gtfs.get_stop_locations(["R16N", "R16"])
        self.assertEqual(
            locations["R16N"],
            {
                "stop_name": "Times Sq-42 St",
                "lat": 40.754672,
                "lng": -73.986754,
                "parent_station": "R16",
            },
        )
        self.assertEqual(locations["R16"]["parent_station"], "")
        self.assertEqual(gtfs.get_stop_locations([]), {})
        self.assertEqual(gtfs.get_stop_locations(["unknown"]), {})
        self.assertEqual(calls, [])

    @unittest.skipUnless(
        _ARTIFACT_PATH.exists(),
        "stop_patterns.json not built",
    )
    def test_real_startup_index_serves_live_map_lookups_without_db(self):
        # The production startup shape: the real artifact attached to a
        # GTFSStaticData. All live-map static lookups must run with _query
        # patched to fail (zero DB round trips).
        gtfs, calls = self._gtfs_with_index(StopPatternIndex.load(_ARTIFACT_PATH))

        parents = gtfs.get_all_parent_stops()
        self.assertEqual(len(parents), 496)
        r16 = next(stop for stop in parents if stop["stop_id"] == "R16")
        self.assertEqual(r16["stop_name"], "Times Sq-42 St")
        self.assertEqual(gtfs.get_route_ids_for_parent_stop("R16"), ["N", "Q", "R", "W"])
        locations = gtfs.get_stop_locations(["R16N", "R16", "D28S"])
        self.assertEqual(locations["R16N"]["parent_station"], "R16")
        self.assertEqual(locations["R16"]["parent_station"], "")
        self.assertEqual(locations["D28S"]["parent_station"], "D28")
        self.assertEqual(calls, [])


class NoIndexFallbackTests(unittest.TestCase):
    """No index attached: the PostgreSQL fallbacks remain exercised unchanged."""

    def _scripted_query(self, sql, params=None):
        if "WHERE location_type = '1'" in sql:
            return [{
                "stop_id": "P1",
                "stop_name": "Parent One",
                "stop_lat": 40.0,
                "stop_lon": -73.0,
            }]
        if "WHERE s.parent_station = %s" in sql:
            return [{"route_id": "B"}, {"route_id": "A"}]
        if "WHERE stop_id = ANY" in sql:
            return [
                {
                    "stop_id": "P1N",
                    "stop_name": "Parent One",
                    "stop_lat": 40.0,
                    "stop_lon": -73.0,
                    "parent_station": "P1",
                },
                {
                    "stop_id": "P1",
                    "stop_name": "Parent One",
                    "stop_lat": 40.0,
                    "stop_lon": -73.0,
                    "parent_station": "",
                },
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    def test_all_three_methods_fall_back_to_db_without_index(self):
        gtfs = GTFSStaticData()
        gtfs._query = self._scripted_query

        self.assertEqual(
            gtfs.get_all_parent_stops(),
            [{
                "stop_id": "P1",
                "stop_name": "Parent One",
                "stop_lat": 40.0,
                "stop_lon": -73.0,
            }],
        )
        # The fallback returns route ids in the DB's (ordered) row order.
        self.assertEqual(gtfs.get_route_ids_for_parent_stop("P1"), ["B", "A"])
        self.assertEqual(
            gtfs.get_stop_locations(["P1N"]),
            {
                "P1N": {
                    "stop_name": "Parent One",
                    "lat": 40.0,
                    "lng": -73.0,
                    "parent_station": "P1",
                },
                "P1": {
                    "stop_name": "Parent One",
                    "lat": 40.0,
                    "lng": -73.0,
                    "parent_station": "",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
