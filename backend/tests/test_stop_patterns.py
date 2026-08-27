import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.mta.static_gtfs.stop_patterns import (
    StopPatternIndex,
    normalize_station_name,
)
from app.services.mta.static_gtfs.store import GTFSStaticData

# Small synthetic network with an express/local branch + a reverse direction.
#   Q local (nb):   A B C D E      (trip_count 100)
#   Q express (nb): A C E          (trip_count 50, skips B/D)
#   Q rev (sb):     E D C B A       (trip_count 90)
#   R:              B D             (trip_count 30)
FIXTURE = {
    "stops": {
        "A": {
            "name": "Alpha Av",
            "lat": 40.0,
            "lon": -73.0,
            "station_complex_id": "gtfs_transfer:A",
        },
        "B": {
            "name": "Beta St",
            "lat": 40.1,
            "lon": -73.1,
            "station_complex_id": "gtfs_transfer:A",
        },
        "C": {"name": "Gamma Sq", "lat": 40.2, "lon": -73.2},
        "D": {"name": "Delta Pkwy", "lat": 40.3, "lon": -73.3},
        "E": {"name": "Epsilon Ctr", "lat": 40.4, "lon": -73.4},
    },
    "patterns": [
        {
            "route_id": "Q",
            "route_short_name": "Q",
            "direction_id": 0,
            "trip_count": 100,
            "signature": "qlocal",
            "stop_ids": ["A", "B", "C", "D", "E"],
        },
        {
            "route_id": "Q",
            "route_short_name": "Q",
            "direction_id": 0,
            "trip_count": 50,
            "signature": "qexpress",
            "stop_ids": ["A", "C", "E"],
        },
        {
            "route_id": "Q",
            "route_short_name": "Q",
            "direction_id": 1,
            "trip_count": 90,
            "signature": "qrev",
            "stop_ids": ["E", "D", "C", "B", "A"],
        },
        {
            "route_id": "R",
            "route_short_name": "R",
            "direction_id": 0,
            "trip_count": 30,
            "signature": "r1",
            "stop_ids": ["B", "D"],
        },
    ],
}

DISTINCT_TRANSFER_FIXTURE = json.loads(json.dumps(FIXTURE))
DISTINCT_TRANSFER_FIXTURE["stops"]["BN"] = {
    "name": "Beta St",
    "lat": 40.1005,
    "lon": -73.1005,
    "station_complex_id": "gtfs_transfer:A",
}
DISTINCT_TRANSFER_FIXTURE["patterns"].insert(
    3,
    {
        "route_id": "R",
        "route_short_name": "R",
        "direction_id": 0,
        "trip_count": 40,
        "signature": "r-distinct-transfer",
        "stop_ids": ["BN", "D"],
    },
)


class NormalizeTests(unittest.TestCase):
    def test_abbreviation_and_case_and_punct(self):
        assert normalize_station_name("Alpha Av") == normalize_station_name(
            "Alpha Avenue"
        )
        assert normalize_station_name("Beta St") == "beta street"
        assert normalize_station_name(
            "Atlantic Av-Barclays Ctr"
        ) == normalize_station_name("Atlantic Avenue - Barclays Center")


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.idx = StopPatternIndex(FIXTURE)

    def test_loader_builds_indexes(self):
        assert len(self.idx.route_patterns["Q"]) == 3
        assert len(self.idx.route_patterns["R"]) == 1
        assert self.idx.ids_for_name("Alpha Avenue") == frozenset({"A"})
        # name normalization tolerates the Av/Avenue difference.
        assert self.idx.ids_for_name("Alpha Av") == self.idx.ids_for_name(
            "Alpha Avenue"
        )

    def test_shortest_span_branch_selection(self):
        # A -> E: both local (span 4) and express (span 2) are valid; with no
        # coords the shorter span (express) wins.
        rows, meta = self.idx.get_intermediate_stops_with_coords(
            "Q", "Alpha Av", "Epsilon Ctr"
        )
        assert meta["hit"]
        assert meta["signature"] == "qexpress"
        assert [r["name"] for r in rows] == ["Alpha Av", "Gamma Sq", "Epsilon Ctr"]
        assert meta["db_queries"] == 0

    def test_excludes_pattern_missing_a_stop(self):
        # A -> D: express has no D, so only the local pattern qualifies.
        rows, meta = self.idx.get_intermediate_stops_with_coords(
            "Q", "Alpha Av", "Delta Pkwy"
        )
        assert meta["signature"] == "qlocal"
        assert [r["name"] for r in rows] == [
            "Alpha Av",
            "Beta St",
            "Gamma Sq",
            "Delta Pkwy",
        ]

    def test_direction_order_uses_reverse_pattern(self):
        # E -> A is invalid order on the northbound patterns; the southbound
        # pattern (E D C B A) provides the correct ordered slice.
        rows, meta = self.idx.get_intermediate_stops_with_coords(
            "Q", "Epsilon Ctr", "Alpha Av"
        )
        assert meta["signature"] == "qrev"
        assert [r["name"] for r in rows] == [
            "Epsilon Ctr",
            "Delta Pkwy",
            "Gamma Sq",
            "Beta St",
            "Alpha Av",
        ]

    def test_route_filter(self):
        # B -> D on R uses R's pattern (no intermediate); on Q uses the local.
        r_rows, _ = self.idx.get_intermediate_stops_with_coords(
            "R", "Beta St", "Delta Pkwy"
        )
        assert [r["name"] for r in r_rows] == ["Beta St", "Delta Pkwy"]
        q_rows, _ = self.idx.get_intermediate_stops_with_coords(
            "Q", "Beta St", "Delta Pkwy"
        )
        assert [r["name"] for r in q_rows] == ["Beta St", "Gamma Sq", "Delta Pkwy"]

    def test_names_only_variant(self):
        assert self.idx.get_intermediate_stops("Q", "Alpha Av", "Delta Pkwy") == [
            "Alpha Av",
            "Beta St",
            "Gamma Sq",
            "Delta Pkwy",
        ]

    def test_stops_for_route_are_served_from_the_local_index(self):
        stops = self.idx.stops_for_routes({"q"})

        assert [stop["stop_id"] for stop in stops] == ["A", "B", "C", "D", "E"]
        assert all(stop["route_ids"] == ["Q"] for stop in stops)

    def test_route_and_location_indexes_resolve_directional_stop_ids(self):
        assert self.idx.routes_for_stop("BN") == ["Q", "R"]
        locations = self.idx.locations_for_stops(["BN", "missing"])
        assert locations["BN"]["stop_name"] == "Beta St"
        assert locations["BN"]["parent_station"] == "B"
        assert "missing" not in locations

    def test_route_segment_exposes_canonical_boarding_identifiers(self):
        segment = self.idx.resolve_route_segment(
            "q",
            "Alpha Avenue",
            "Delta Parkway",
        )

        assert segment == {
            "origin_stop_id": "A",
            "destination_stop_id": "D",
            "direction_id": 0,
        }

    def test_suggest_one_transfer_validates_shared_complex_and_egress_gain(self):
        suggestion = self.idx.suggest_one_transfer(
            "Q",
            "Alpha Av",
            "Epsilon Ctr",
            {"lat": 40.3, "lon": -73.3},
            allowed_modes=["SUBWAY", "BUS"],
        )

        assert suggestion is not None
        assert suggestion["continuation_route_id"] == "R"
        assert suggestion["transfer_stop_id"] == "B"
        assert suggestion["continuation_transfer_stop_id"] == "B"
        assert suggestion["transfer_station_complex_id"] == "gtfs_transfer:A"
        assert suggestion["egress_distance_improvement_meters"] > 250

    def test_suggest_one_transfer_fails_closed_for_order_complex_gain_and_exclusions(
        self,
    ):
        assert (
            self.idx.suggest_one_transfer(
                "Q", "Alpha Av", "Beta St", {"lat": 40.3, "lon": -73.3}
            )
            is None
        )
        no_complex = json.loads(json.dumps(FIXTURE))
        no_complex["stops"]["B"].pop("station_complex_id")
        assert (
            StopPatternIndex(no_complex).suggest_one_transfer(
                "Q", "Alpha Av", "Epsilon Ctr", {"lat": 40.3, "lon": -73.3}
            )
            is None
        )
        assert (
            self.idx.suggest_one_transfer(
                "Q", "Alpha Av", "Epsilon Ctr", {"lat": 40.4, "lon": -73.4}
            )
            is None
        )
        assert (
            self.idx.suggest_one_transfer(
                "Q",
                "Alpha Av",
                "Epsilon Ctr",
                {"lat": 40.3, "lon": -73.3},
                excluded_route_ids={"R"},
            )
            is None
        )
        assert (
            self.idx.suggest_one_transfer(
                "Q",
                "Alpha Av",
                "Epsilon Ctr",
                {"lat": 40.3, "lon": -73.3},
                excluded_modes={"SUBWAY"},
            )
            is None
        )

    def test_suggest_one_transfer_returns_continuation_member_coordinates(self):
        suggestion = StopPatternIndex(DISTINCT_TRANSFER_FIXTURE).suggest_one_transfer(
            "Q", "Alpha Av", "Epsilon Ctr", {"lat": 40.3, "lon": -73.3}
        )
        assert suggestion["transfer_stop_id"] == "B"
        assert suggestion["continuation_transfer_stop_id"] == "BN"
        assert (
            suggestion["transfer_stop_coords"]
            != suggestion["continuation_transfer_stop_coords"]
        )

    def test_identity_for_stop_parent_and_directional_child(self):
        # A canonical parent id is not a platform; its directional child is.
        assert self.idx.identity_for_stop("A") == {
            "parent_station": "A",
            "station_complex_id": "gtfs_transfer:A",
            "is_platform": False,
        }
        assert self.idx.identity_for_stop("AN") == {
            "parent_station": "A",
            "station_complex_id": "gtfs_transfer:A",
            "is_platform": True,
        }
        assert self.idx.identity_for_stop("AS") == self.idx.identity_for_stop("AN")

    def test_identity_for_stop_unknown_and_empty(self):
        # Unknown ids degrade to unknown identity; no I/O and no fabrication.
        assert self.idx.identity_for_stop("Q05") == {
            "parent_station": None,
            "station_complex_id": None,
            "is_platform": False,
        }
        assert self.idx.identity_for_stop("Q05N") == {
            "parent_station": None,
            "station_complex_id": None,
            "is_platform": True,
        }
        assert self.idx.identity_for_stop(None)["parent_station"] is None
        assert self.idx.identity_for_stop("")["parent_station"] is None
        assert not self.idx.identity_for_stop(123)["is_platform"]

    def test_identity_for_stop_singleton_has_no_component(self):
        assert self.idx.identity_for_stop("C")["station_complex_id"] is None

    def test_all_parent_stops_contract(self):
        parents = self.idx.all_parent_stops()
        assert [stop["stop_id"] for stop in parents] == ["A", "B", "C", "D", "E"]
        assert parents[0] == {
            "stop_id": "A",
            "stop_name": "Alpha Av",
            "stop_lat": 40.0,
            "stop_lon": -73.0,
        }

    def test_route_ids_for_parent_stop_synthetic(self):
        assert self.idx.route_ids_for_parent_stop("A") == ["Q"]
        assert self.idx.route_ids_for_parent_stop("B") == ["Q", "R"]
        assert self.idx.route_ids_for_parent_stop("unknown") == []
        # Deterministic across calls.
        assert self.idx.route_ids_for_parent_stop(
            "B"
        ) == self.idx.route_ids_for_parent_stop("B")

    def test_stop_locations_parent_and_directional(self):
        locations = self.idx.stop_locations(["AN", "A", "unknown", None, ""])
        assert locations["AN"] == {
            "stop_name": "Alpha Av",
            "lat": 40.0,
            "lng": -73.0,
            "parent_station": "A",
        }
        assert locations["A"] == {
            "stop_name": "Alpha Av",
            "lat": 40.0,
            "lng": -73.0,
            "parent_station": "",
        }
        assert "unknown" not in locations
        assert self.idx.stop_locations([]) == {}

    def test_miss_unknown_name(self):
        rows, meta = self.idx.get_intermediate_stops_with_coords(
            "Q", "Nowhere", "Epsilon Ctr"
        )
        assert not meta["hit"]
        assert rows == []

    def test_miss_unknown_route(self):
        rows, meta = self.idx.get_intermediate_stops_with_coords(
            "Z", "Alpha Av", "Epsilon Ctr"
        )
        assert not meta["hit"]
        assert meta["patterns_considered"] == 0
        assert rows == []

    def test_coords_do_not_break_match(self):
        rows, meta = self.idx.get_intermediate_stops_with_coords(
            "Q",
            "Alpha Av",
            "Delta Pkwy",
            {"latitude": 40.0, "longitude": -73.0},
            {"latitude": 40.3, "longitude": -73.3},
        )
        assert meta["hit"]
        assert rows[0]["name"] == "Alpha Av"
        assert rows[-1]["name"] == "Delta Pkwy"


class GTFSStaticMemoryTests(unittest.TestCase):
    def setUp(self):
        self.gtfs = GTFSStaticData()
        self.gtfs.set_pattern_index(StopPatternIndex(FIXTURE))
        self.gtfs._query = Mock(side_effect=AssertionError("unexpected Postgres query"))

    def test_live_feed_static_lookups_never_query_postgres(self):
        parents = self.gtfs.get_all_parent_stops()
        routes = self.gtfs.get_route_ids_for_parent_stop("B")
        children = self.gtfs.get_child_stop_ids("B")
        locations = self.gtfs.get_stop_locations(["BN", "missing"])
        trip_context = self.gtfs.get_trip_stop_context(["realtime-trip"])

        assert [row["stop_id"] for row in parents] == ["A", "B", "C", "D", "E"]
        assert routes == ["Q", "R"]
        assert children == ["BN", "BS"]
        assert locations["BN"]["lat"] == 40.1
        assert trip_context == {}
        self.gtfs._query.assert_not_called()

    def test_unknown_indexed_stops_do_not_fall_back_to_postgres(self):
        assert self.gtfs.get_route_ids_for_parent_stop("missing") == []
        assert self.gtfs.get_child_stop_ids("missing") == []
        assert self.gtfs.get_stop_locations(["missing"]) == {}
        self.gtfs._query.assert_not_called()


class RealArtifactTests(unittest.TestCase):
    ARTIFACT = (
        Path(__file__).resolve().parent.parent / "app" / "data" / "stop_patterns.json"
    )

    @unittest.skipUnless(
        (
            Path(__file__).resolve().parent.parent
            / "app"
            / "data"
            / "stop_patterns.json"
        ).exists(),
        "stop_patterns.json not built",
    )
    def test_load_and_match_real_q_leg(self):
        idx = StopPatternIndex.load(self.ARTIFACT)
        assert len(idx.patterns) > 50
        assert len(idx.stops) > 100
        rows, meta = idx.get_intermediate_stops_with_coords(
            "Q", "Canal St", "Church Av"
        )
        assert meta["hit"]
        assert meta["db_queries"] == 0
        names = [r["name"] for r in rows]
        assert names[0] == "Canal St"
        assert names[-1] == "Church Av"
        # Q from Canal St to Church Av runs via DeKalb + the Brighton line.
        assert "DeKalb Av" in names
        assert all("lat" in r and "lng" in r for r in rows)

    @unittest.skipUnless(
        (
            Path(__file__).resolve().parent.parent
            / "app"
            / "data"
            / "stop_patterns.json"
        ).exists(),
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

        assert segment["origin_stop_id"] == "D28"
        assert segment["destination_stop_id"] == "D43"
        assert segment["direction_id"] == 1

    @unittest.skipUnless(
        (
            Path(__file__).resolve().parent.parent
            / "app"
            / "data"
            / "stop_patterns.json"
        ).exists(),
        "stop_patterns.json not built",
    )
    def test_real_artifact_links_explicit_transfer_components(self):
        idx = StopPatternIndex.load(self.ARTIFACT)
        # 14 St-Union Sq parents (635/L03/R20) share one explicit component.
        for member in ("635", "L03", "R20"):
            assert (
                idx.identity_for_stop(member)["station_complex_id"]
                == "gtfs_transfer:635"
            )
        # Times Sq-42 St / Port Authority parents share one explicit component.
        for member in ("127", "725", "902", "A27", "R16"):
            assert (
                idx.identity_for_stop(member)["station_complex_id"]
                == "gtfs_transfer:127"
            )
        # 34 St-Penn Station parents 128 and A28 share a NAME but have no
        # explicit transfer relationship: they must NOT share an identity.
        assert idx.identity_for_stop("128")["station_complex_id"] is None
        assert idx.identity_for_stop("A28")["station_complex_id"] is None

    @unittest.skipUnless(
        (
            Path(__file__).resolve().parent.parent
            / "app"
            / "data"
            / "stop_patterns.json"
        ).exists(),
        "stop_patterns.json not built",
    )
    def test_real_artifact_transfer_component_metadata(self):
        artifact = json.loads(Path(self.ARTIFACT).read_text())
        meta = artifact["transfer_components"]
        assert meta["count"] == 35
        assert meta["member_stop_count"] == 87
        assert meta["identity_prefix"] == "gtfs_transfer"
        assert meta["source"] == "transfers"

if __name__ == "__main__":
    unittest.main()
