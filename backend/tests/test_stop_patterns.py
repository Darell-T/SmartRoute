import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.stop_patterns import (  # noqa: E402
    StopPatternIndex,
    normalize_station_name,
)

# Small synthetic network with an express/local branch + a reverse direction.
#   Q local (nb):   A B C D E      (trip_count 100)
#   Q express (nb): A C E          (trip_count 50, skips B/D)
#   Q rev (sb):     E D C B A       (trip_count 90)
#   R:              B D             (trip_count 30)
FIXTURE = {
    "stops": {
        "A": {"name": "Alpha Av", "lat": 40.0, "lon": -73.0},
        "B": {"name": "Beta St", "lat": 40.1, "lon": -73.1},
        "C": {"name": "Gamma Sq", "lat": 40.2, "lon": -73.2},
        "D": {"name": "Delta Pkwy", "lat": 40.3, "lon": -73.3},
        "E": {"name": "Epsilon Ctr", "lat": 40.4, "lon": -73.4},
    },
    "patterns": [
        {"route_id": "Q", "route_short_name": "Q", "direction_id": 0,
         "trip_count": 100, "signature": "qlocal", "stop_ids": ["A", "B", "C", "D", "E"]},
        {"route_id": "Q", "route_short_name": "Q", "direction_id": 0,
         "trip_count": 50, "signature": "qexpress", "stop_ids": ["A", "C", "E"]},
        {"route_id": "Q", "route_short_name": "Q", "direction_id": 1,
         "trip_count": 90, "signature": "qrev", "stop_ids": ["E", "D", "C", "B", "A"]},
        {"route_id": "R", "route_short_name": "R", "direction_id": 0,
         "trip_count": 30, "signature": "r1", "stop_ids": ["B", "D"]},
    ],
}


class NormalizeTests(unittest.TestCase):
    def test_abbreviation_and_case_and_punct(self):
        self.assertEqual(normalize_station_name("Alpha Av"), normalize_station_name("Alpha Avenue"))
        self.assertEqual(normalize_station_name("Beta St"), "beta street")
        self.assertEqual(
            normalize_station_name("Atlantic Av-Barclays Ctr"),
            normalize_station_name("Atlantic Avenue - Barclays Center"),
        )


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.idx = StopPatternIndex(FIXTURE)

    def test_loader_builds_indexes(self):
        self.assertEqual(len(self.idx.route_patterns["Q"]), 3)
        self.assertEqual(len(self.idx.route_patterns["R"]), 1)
        self.assertEqual(self.idx.ids_for_name("Alpha Avenue"), frozenset({"A"}))
        # name normalization tolerates the Av/Avenue difference.
        self.assertEqual(self.idx.ids_for_name("Alpha Av"), self.idx.ids_for_name("Alpha Avenue"))

    def test_shortest_span_branch_selection(self):
        # A -> E: both local (span 4) and express (span 2) are valid; with no
        # coords the shorter span (express) wins.
        rows, meta = self.idx.get_intermediate_stops_with_coords("Q", "Alpha Av", "Epsilon Ctr")
        self.assertTrue(meta["hit"])
        self.assertEqual(meta["signature"], "qexpress")
        self.assertEqual([r["name"] for r in rows], ["Alpha Av", "Gamma Sq", "Epsilon Ctr"])
        self.assertEqual(meta["db_queries"], 0)

    def test_excludes_pattern_missing_a_stop(self):
        # A -> D: express has no D, so only the local pattern qualifies.
        rows, meta = self.idx.get_intermediate_stops_with_coords("Q", "Alpha Av", "Delta Pkwy")
        self.assertEqual(meta["signature"], "qlocal")
        self.assertEqual([r["name"] for r in rows], ["Alpha Av", "Beta St", "Gamma Sq", "Delta Pkwy"])

    def test_direction_order_uses_reverse_pattern(self):
        # E -> A is invalid order on the northbound patterns; the southbound
        # pattern (E D C B A) provides the correct ordered slice.
        rows, meta = self.idx.get_intermediate_stops_with_coords("Q", "Epsilon Ctr", "Alpha Av")
        self.assertEqual(meta["signature"], "qrev")
        self.assertEqual(
            [r["name"] for r in rows],
            ["Epsilon Ctr", "Delta Pkwy", "Gamma Sq", "Beta St", "Alpha Av"],
        )

    def test_route_filter(self):
        # B -> D on R uses R's pattern (no intermediate); on Q uses the local.
        r_rows, _ = self.idx.get_intermediate_stops_with_coords("R", "Beta St", "Delta Pkwy")
        self.assertEqual([r["name"] for r in r_rows], ["Beta St", "Delta Pkwy"])
        q_rows, _ = self.idx.get_intermediate_stops_with_coords("Q", "Beta St", "Delta Pkwy")
        self.assertEqual([r["name"] for r in q_rows], ["Beta St", "Gamma Sq", "Delta Pkwy"])

    def test_names_only_variant(self):
        self.assertEqual(
            self.idx.get_intermediate_stops("Q", "Alpha Av", "Delta Pkwy"),
            ["Alpha Av", "Beta St", "Gamma Sq", "Delta Pkwy"],
        )

    def test_stops_for_route_are_served_from_the_local_index(self):
        stops = self.idx.stops_for_routes({"q"})

        self.assertEqual(
            [stop["stop_id"] for stop in stops],
            ["A", "B", "C", "D", "E"],
        )
        self.assertTrue(all(stop["route_ids"] == ["Q"] for stop in stops))

    def test_route_segment_exposes_canonical_boarding_identifiers(self):
        segment = self.idx.resolve_route_segment(
            "q",
            "Alpha Avenue",
            "Delta Parkway",
        )

        self.assertEqual(
            segment,
            {
                "origin_stop_id": "A",
                "destination_stop_id": "D",
                "direction_id": 0,
            },
        )

    def test_miss_unknown_name(self):
        rows, meta = self.idx.get_intermediate_stops_with_coords("Q", "Nowhere", "Epsilon Ctr")
        self.assertFalse(meta["hit"])
        self.assertEqual(rows, [])

    def test_miss_unknown_route(self):
        rows, meta = self.idx.get_intermediate_stops_with_coords("Z", "Alpha Av", "Epsilon Ctr")
        self.assertFalse(meta["hit"])
        self.assertEqual(meta["patterns_considered"], 0)
        self.assertEqual(rows, [])

    def test_coords_do_not_break_match(self):
        rows, meta = self.idx.get_intermediate_stops_with_coords(
            "Q", "Alpha Av", "Delta Pkwy",
            {"latitude": 40.0, "longitude": -73.0},
            {"latitude": 40.3, "longitude": -73.3},
        )
        self.assertTrue(meta["hit"])
        self.assertEqual(rows[0]["name"], "Alpha Av")
        self.assertEqual(rows[-1]["name"], "Delta Pkwy")


class RealArtifactTests(unittest.TestCase):
    ARTIFACT = Path(__file__).resolve().parent.parent / "app" / "data" / "stop_patterns.json"

    @unittest.skipUnless(
        (Path(__file__).resolve().parent.parent / "app" / "data" / "stop_patterns.json").exists(),
        "stop_patterns.json not built",
    )
    def test_load_and_match_real_q_leg(self):
        idx = StopPatternIndex.load(self.ARTIFACT)
        self.assertGreater(len(idx.patterns), 50)
        self.assertGreater(len(idx.stops), 100)
        rows, meta = idx.get_intermediate_stops_with_coords("Q", "Canal St", "Church Av")
        self.assertTrue(meta["hit"])
        self.assertEqual(meta["db_queries"], 0)
        names = [r["name"] for r in rows]
        self.assertEqual(names[0], "Canal St")
        self.assertEqual(names[-1], "Church Av")
        # Q from Canal St to Church Av runs via DeKalb + the Brighton line.
        self.assertIn("DeKalb Av", names)
        self.assertTrue(all("lat" in r and "lng" in r for r in rows))

    @unittest.skipUnless(
        (Path(__file__).resolve().parent.parent / "app" / "data" / "stop_patterns.json").exists(),
        "stop_patterns.json not built",
    )
    def test_real_q_church_to_coney_segment_uses_route_specific_stop(self):
        idx = StopPatternIndex.load(self.ARTIFACT)

        segment = idx.resolve_route_segment(
            "Q",
            "Church Av",
            "Coney Island-Stillwell Av",
            {"latitude": 40.6505, "longitude": -73.9624},
            {"latitude": 40.5774, "longitude": -73.9812},
        )

        self.assertEqual(segment["origin_stop_id"], "D28")
        self.assertEqual(segment["destination_stop_id"], "D43")
        self.assertEqual(segment["direction_id"], 1)

    @unittest.skipUnless(
        (Path(__file__).resolve().parent.parent / "app" / "data" / "stop_patterns.json").exists(),
        "stop_patterns.json not built",
    )
    def test_load_classmethod_from_tempfile(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(FIXTURE, f)
            tmp = f.name
        try:
            idx = StopPatternIndex.load(tmp)
            self.assertEqual(len(idx.route_patterns["Q"]), 3)
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
