"""GTFSStaticData intermediate-stop queries.

The module opens a psycopg2 pool at import time, so the driver is faked
before import and ``_query`` is overridden per test -- no database needed.
Covers the names API (unchanged contract) and the new coords variant that
feeds route stop markers on the map.
"""

import importlib
import sys
import types
import unittest
from unittest.mock import patch


def _load_gtfs_module():
    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_pool_mod = types.ModuleType("psycopg2.pool")
    fake_extras_mod = types.ModuleType("psycopg2.extras")

    class _FakePool:
        def __init__(self, *args, **kwargs):
            pass

        def getconn(self):
            raise AssertionError("tests must stub _query, not hit the pool")

        def putconn(self, conn):
            pass

    fake_pool_mod.ThreadedConnectionPool = _FakePool
    fake_extras_mod.RealDictCursor = object
    fake_psycopg2.pool = fake_pool_mod
    fake_psycopg2.extras = fake_extras_mod

    with patch.dict(
        sys.modules,
        {
            "psycopg2": fake_psycopg2,
            "psycopg2.pool": fake_pool_mod,
            "psycopg2.extras": fake_extras_mod,
        },
    ):
        if "app.services.mta.static_gtfs.store" in sys.modules:
            return importlib.reload(sys.modules["app.services.mta.static_gtfs.store"])
        return importlib.import_module("app.services.mta.static_gtfs.store")


STOPS = {
    "G35N": {"stop_id": "G35N", "stop_name": "Church Av", "stop_lat": 40.644, "stop_lon": -73.979},
    "G34N": {"stop_id": "G34N", "stop_name": "Fort Hamilton Pkwy", "stop_lat": 40.650, "stop_lon": -73.975},
    "G33N": {"stop_id": "G33N", "stop_name": "15 St-Prospect Park", "stop_lat": 40.660, "stop_lon": -73.979},
}


def _scripted_query(sql, params=None):
    if "FROM stops WHERE stop_name" in sql:
        name = params[0]
        return [
            {"stop_id": row["stop_id"]}
            for row in STOPS.values()
            if row["stop_name"] == name
        ]
    if "WITH matching_trips" in sql:
        return [STOPS["G35N"], STOPS["G34N"], STOPS["G33N"]]
    if "FROM trips WHERE route_id" in sql:
        return [{"trip_id": "t1"}]
    if "FROM stop_times WHERE trip_id" in sql:
        return [{"stop_id": "G35N"}, {"stop_id": "G34N"}, {"stop_id": "G33N"}]
    if "FROM stops WHERE stop_id = ANY" in sql:
        wanted = params[0]
        return [STOPS[s] for s in STOPS if s in wanted]
    raise AssertionError(f"unexpected SQL: {sql}")


class IntermediateStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_gtfs_module()

    def _gtfs(self):
        gtfs = self.module.GTFSStaticData()
        gtfs._allow_db_fallback = True  # static index not loaded in tests; exercise DB path
        gtfs._query = _scripted_query
        return gtfs

    def test_names_api_unchanged(self):
        names = self._gtfs().get_intermediate_stops("G", "Church Av", "15 St-Prospect Park")
        self.assertEqual(names, ["Church Av", "Fort Hamilton Pkwy", "15 St-Prospect Park"])

    def test_coords_variant_returns_ordered_locations(self):
        rows = self._gtfs().get_intermediate_stops_with_coords(
            "G", "Church Av", "15 St-Prospect Park"
        )
        self.assertEqual(
            rows,
            [
                {"name": "Church Av", "lat": 40.644, "lng": -73.979},
                {"name": "Fort Hamilton Pkwy", "lat": 40.650, "lng": -73.975},
                {"name": "15 St-Prospect Park", "lat": 40.660, "lng": -73.979},
            ],
        )

    def test_no_ordered_trip_yields_empty_for_both(self):
        gtfs = self.module.GTFSStaticData()
        gtfs._allow_db_fallback = True  # static index not loaded in tests; exercise DB path

        def reversed_query(sql, params=None):
            if "WITH matching_trips" in sql:
                return []
            if "FROM stop_times WHERE trip_id" in sql:
                return [{"stop_id": "G33N"}, {"stop_id": "G34N"}, {"stop_id": "G35N"}]
            return _scripted_query(sql, params)

        gtfs._query = reversed_query
        self.assertEqual(gtfs.get_intermediate_stops("G", "Church Av", "15 St-Prospect Park"), [])
        self.assertEqual(
            gtfs.get_intermediate_stops_with_coords("G", "Church Av", "15 St-Prospect Park"),
            [],
        )


class CoordinateFallbackTests(unittest.TestCase):
    """When the provider's station name is not on the route's trips (e.g. a Q
    leg whose arrival is labeled as the off-line transfer station), the coords
    fallback snaps board/alight points to the nearest stops on the route."""

    @classmethod
    def setUpClass(cls):
        cls.module = _load_gtfs_module()

    # A short Q line: Church Av -> Prospect Park -> 7 Av -> DeKalb Av.
    Q_STOPS = {
        "Q05N": {"stop_id": "Q05N", "stop_name": "Church Av", "stop_lat": 40.650, "stop_lon": -73.962},
        "Q04N": {"stop_id": "Q04N", "stop_name": "Prospect Park", "stop_lat": 40.661, "stop_lon": -73.962},
        "Q03N": {"stop_id": "Q03N", "stop_name": "7 Av", "stop_lat": 40.666, "stop_lon": -73.973},
        "Q02N": {"stop_id": "Q02N", "stop_name": "DeKalb Av", "stop_lat": 40.690, "stop_lon": -73.982},
    }

    def _query(self, sql, params=None):
        if "st.trip_id = (SELECT trip_id" in sql:  # snap-to-representative-trip
            return [dict(v) for v in self.Q_STOPS.values()]
        if "FROM stops WHERE stop_name" in sql:
            name = params[0]
            return [{"stop_id": r["stop_id"]} for r in self.Q_STOPS.values() if r["stop_name"] == name]
        if "WITH matching_trips" in sql:
            return [dict(v) for v in self.Q_STOPS.values()]
        if "FROM trips WHERE route_id" in sql:
            return [{"trip_id": "qt1"}]
        if "FROM stop_times WHERE trip_id" in sql:
            return [{"stop_id": s} for s in self.Q_STOPS]
        if "FROM stops WHERE stop_id = ANY" in sql:
            wanted = params[0]
            return [self.Q_STOPS[s] for s in self.Q_STOPS if s in wanted]
        raise AssertionError(f"unexpected SQL: {sql}")

    def test_offline_arrival_name_falls_back_to_coord_snap(self):
        gtfs = self.module.GTFSStaticData()
        gtfs._allow_db_fallback = True  # static index not loaded in tests; exercise DB path
        gtfs._query = self._query
        # "Jay St - MetroTech" is not a Q stop, so the name lookup yields
        # nothing; the arrival coords sit next to DeKalb Av and snap to it.
        rows = gtfs.get_intermediate_stops_with_coords(
            "Q",
            "Church Av",
            "Jay St - MetroTech",
            {"latitude": 40.650, "longitude": -73.962},
            {"latitude": 40.692, "longitude": -73.986},
        )
        self.assertEqual(
            [r["name"] for r in rows],
            ["Church Av", "Prospect Park", "7 Av", "DeKalb Av"],
        )

    def test_no_coords_still_empty_when_name_off_line(self):
        gtfs = self.module.GTFSStaticData()
        gtfs._allow_db_fallback = True  # static index not loaded in tests; exercise DB path
        gtfs._query = self._query
        # Without coords there is no fallback, so an off-line name yields [].
        self.assertEqual(
            gtfs.get_intermediate_stops_with_coords("Q", "Church Av", "Jay St - MetroTech"),
            [],
        )


class IntermediateStopCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_gtfs_module()

    class PatternIndex:
        def __init__(self, marker="cached"):
            self.marker = marker
            self.calls = 0

        def get_intermediate_stops_with_coords(self, *_args):
            self.calls += 1
            return ([{"name": self.marker, "lat": 40.7, "lng": -73.9}], {
                "hit": True,
                "patterns_considered": 1,
            })

    def test_cache_is_bounded_and_repeated_lookups_hit(self):
        gtfs = self.module.GTFSStaticData()
        index = self.PatternIndex()
        gtfs.set_pattern_index(index)

        for item in range(self.module.INTERMEDIATE_STOPS_CACHE_MAXSIZE + 40):
            gtfs.get_intermediate_stops_with_coords(
                "Q", f"Origin {item}", f"Destination {item}"
            )

        info = gtfs.intermediate_stops_cache_info()
        self.assertEqual(info.currsize, self.module.INTERMEDIATE_STOPS_CACHE_MAXSIZE)
        calls_before = index.calls
        gtfs.get_intermediate_stops_with_coords(
            "Q", "Origin 551", "Destination 551"
        )
        self.assertEqual(index.calls, calls_before)
        self.assertGreater(gtfs.intermediate_stops_cache_info().hits, 0)

    def test_pattern_reload_clears_cached_values(self):
        gtfs = self.module.GTFSStaticData()
        gtfs.set_pattern_index(self.PatternIndex("before"))
        self.assertEqual(
            gtfs.get_intermediate_stops_with_coords("Q", "A", "B")[0]["name"],
            "before",
        )
        self.assertEqual(gtfs.intermediate_stops_cache_info().currsize, 1)

        gtfs.set_pattern_index(self.PatternIndex("after"))
        self.assertEqual(gtfs.intermediate_stops_cache_info().currsize, 0)
        self.assertEqual(
            gtfs.get_intermediate_stops_with_coords("Q", "A", "B")[0]["name"],
            "after",
        )


if __name__ == "__main__":
    unittest.main()
