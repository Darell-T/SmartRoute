"""Focused no-network tests for the stop-pattern builder's transfer
component projection (backend/scripts/build_stop_patterns.py).

Covers the pure component derivation plus both supported source adapters
(tiny local SQLite transfers table and tiny zip transfers.txt); no download,
no live provider, no database server.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "build_stop_patterns.py"
)


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_stop_patterns", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BUILDER = _load_builder()

STOPS = {
    "P1": {"name": "One St", "lat": 40.1, "lon": -73.1},
    "P2": {"name": "Two St", "lat": 40.2, "lon": -73.2},
    "P3": {"name": "Three St", "lat": 40.3, "lon": -73.3},
    "P4": {"name": "Four St", "lat": 40.4, "lon": -73.4},
}


class DeriveTransferComponentsTests(unittest.TestCase):
    def test_symmetric_and_transitive_component_grouping(self):
        rows = [
            ("P1", "P2"),
            ("P2", "P1"),  # symmetric duplicate is one undirected edge
            ("P2", "P3"),  # transitive link
            ("P4", "P4"),  # self row ignored
            ("P1", "UNKNOWN"),  # unknown endpoint ignored
        ]
        components = _BUILDER.derive_transfer_components(STOPS, rows)
        self.assertEqual(
            components,
            {
                "P1": "gtfs_transfer:P1",
                "P2": "gtfs_transfer:P1",
                "P3": "gtfs_transfer:P1",
            },
        )
        self.assertNotIn("P4", components)

    def test_derivation_is_deterministic_regardless_of_row_order(self):
        rows_a = [("P3", "P2"), ("P1", "P2")]
        rows_b = [("P1", "P2"), ("P2", "P3")]
        self.assertEqual(
            _BUILDER.derive_transfer_components(STOPS, rows_a),
            _BUILDER.derive_transfer_components(STOPS, rows_b),
        )

    def test_self_rows_alone_create_no_component(self):
        components = _BUILDER.derive_transfer_components(
            STOPS, [("P1", "P1"), ("P2", "P2")]
        )
        self.assertEqual(components, {})

    def test_unlinked_same_name_or_nearby_stops_remain_separate(self):
        same_name = {
            "X1": {"name": "Same St", "lat": 40.5, "lon": -73.5},
            "X2": {"name": "Same St", "lat": 40.5001, "lon": -73.5},
        }
        self.assertEqual(
            _BUILDER.derive_transfer_components(same_name, []),
            {},
        )
        # Even an explicit non-transfer self row must not link them.
        self.assertEqual(
            _BUILDER.derive_transfer_components(
                same_name, [("X1", "X1"), ("X2", "X2")]
            ),
            {},
        )

    def test_unknown_endpoints_are_ignored(self):
        components = _BUILDER.derive_transfer_components(
            STOPS, [("P1", "ZZ9"), ("ZZ8", "P2")]
        )
        self.assertEqual(components, {})

    def test_identity_is_source_qualified_and_lowest_member(self):
        components = _BUILDER.derive_transfer_components(
            STOPS, [("P3", "P2"), ("P2", "P4")]
        )
        self.assertEqual(components["P4"], "gtfs_transfer:P2")
        self.assertTrue(components["P2"].startswith("gtfs_transfer:"))

    def test_platform_endpoint_ids_are_stripped_to_parents(self):
        components = _BUILDER.derive_transfer_components(
            STOPS, [("P1N", "P2S"), ("P2", "P3N")]
        )
        self.assertEqual(
            components,
            {"P1": "gtfs_transfer:P1", "P2": "gtfs_transfer:P1", "P3": "gtfs_transfer:P1"},
        )


class BuildPatternsTransferAnnotationTests(unittest.TestCase):
    def _base_inputs(self):
        routes_short = {"R1": "R"}
        trip_meta = {"T1": ("R1", 0)}
        trip_sequences = {
            "T1": ["P1N", "P2N", "P3N", "P4N"],
        }
        return routes_short, trip_meta, trip_sequences

    def test_annotation_preserves_patterns_and_only_adds_component_fields(self):
        routes_short, trip_meta, trip_sequences = self._base_inputs()
        plain = _BUILDER.build_patterns(
            STOPS, routes_short, trip_meta, trip_sequences
        )
        components = _BUILDER.derive_transfer_components(
            STOPS, [("P1", "P2"), ("P2", "P3")]
        )
        annotated = _BUILDER.build_patterns(
            STOPS,
            routes_short,
            trip_meta,
            trip_sequences,
            transfer_components=components,
        )

        self.assertEqual(plain["patterns"], annotated["patterns"])
        self.assertEqual(plain["pattern_count"], annotated["pattern_count"])
        self.assertEqual(plain["stop_count"], annotated["stop_count"])
        for sid in STOPS:
            self.assertEqual(plain["stops"][sid]["name"], annotated["stops"][sid]["name"])
        self.assertEqual(annotated["stops"]["P1"]["station_complex_id"], "gtfs_transfer:P1")
        self.assertEqual(annotated["stops"]["P3"]["station_complex_id"], "gtfs_transfer:P1")
        self.assertNotIn("station_complex_id", annotated["stops"]["P4"])
        self.assertEqual(
            annotated["transfer_components"],
            {
                "count": 1,
                "member_stop_count": 3,
                "identity_prefix": "gtfs_transfer",
                "source": "transfers",
            },
        )

    def test_no_components_emits_no_metadata(self):
        routes_short, trip_meta, trip_sequences = self._base_inputs()
        plain = _BUILDER.build_patterns(STOPS, routes_short, trip_meta, trip_sequences)
        self.assertNotIn("transfer_components", plain)
        self.assertNotIn("station_complex_id", plain["stops"]["P1"])


class SqliteAdapterTests(unittest.TestCase):
    def _fixture_path(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        con = sqlite3.connect(path)
        try:
            cur = con.cursor()
            cur.execute(
                "CREATE TABLE stops (stop_id TEXT, stop_name TEXT, stop_lat REAL, stop_lon REAL)"
            )
            cur.execute("CREATE TABLE routes (route_id TEXT, route_short_name TEXT)")
            cur.execute("CREATE TABLE trips (trip_id TEXT, route_id TEXT)")
            cur.execute(
                "CREATE TABLE stop_times (trip_id TEXT, stop_id TEXT, stop_sequence INTEGER)"
            )
            cur.execute(
                "CREATE TABLE transfers (from_stop_id TEXT, to_stop_id TEXT, "
                "transfer_type TEXT, min_transfer_time TEXT)"
            )
            for sid, info in STOPS.items():
                cur.execute(
                    "INSERT INTO stops VALUES (?, ?, ?, ?)",
                    (sid, info["name"], info["lat"], info["lon"]),
                )
            cur.execute("INSERT INTO routes VALUES ('R1', 'R')")
            cur.execute("INSERT INTO trips VALUES ('T1', 'R1')")
            for i, sid in enumerate(("P1N", "P2N", "P3N", "P4N"), start=1):
                cur.execute(
                    "INSERT INTO stop_times VALUES (?, ?, ?)", ("T1", sid, i)
                )
            for row in (
                ("P1", "P2", "2", "180"),
                ("P2", "P3", "2", "180"),
                ("P4", "P4", "2", "180"),
                ("P1", "UNKNOWN", "2", "180"),
            ):
                cur.execute("INSERT INTO transfers VALUES (?, ?, ?, ?)", row)
            con.commit()
        finally:
            con.close()
        return path

    def test_transfer_rows_reach_the_component_builder(self):
        path = self._fixture_path()
        try:
            stops, routes, trips, seqs, transfer_rows = _BUILDER.load_from_sqlite(
                path, None
            )
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(stops, STOPS)
        self.assertEqual(len(transfer_rows), 4)
        components = _BUILDER.derive_transfer_components(stops, transfer_rows)
        self.assertEqual(components["P1"], "gtfs_transfer:P1")
        self.assertEqual(components["P3"], "gtfs_transfer:P1")
        self.assertNotIn("P4", components)
        artifact = _BUILDER.build_patterns(
            stops, routes, trips, seqs, transfer_components=components
        )
        self.assertEqual(artifact["pattern_count"], 1)
        self.assertEqual(artifact["stops"]["P2"]["station_complex_id"], "gtfs_transfer:P1")


class ZipAdapterTests(unittest.TestCase):
    def _fixture(self, *, include_transfers=True):
        entries = {
            "stops.txt": (
                "stop_id,stop_name,stop_lat,stop_lon\n"
                + "".join(
                    f"{sid},{info['name']},{info['lat']},{info['lon']}\n"
                    for sid, info in STOPS.items()
                )
            ),
            "routes.txt": "route_id,route_short_name\nR1,R\n",
            "trips.txt": "trip_id,route_id,direction_id\nT1,R1,0\n",
            "stop_times.txt": (
                "trip_id,stop_id,stop_sequence\n"
                "T1,P1N,1\nT1,P2N,2\nT1,P3N,3\nT1,P4N,4\n"
            ),
        }
        if include_transfers:
            entries["transfers.txt"] = (
                "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"
                "P1,P2,2,180\n"
                "P2,P3,2,180\n"
                "P4,P4,2,180\n"
                "P1,UNKNOWN,2,180\n"
            )
        return entries

    def test_transfers_txt_reaches_the_component_builder(self):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            zip_path = Path(f.name)
        try:
            with zipfile.ZipFile(zip_path, "w") as zf:
                for name, content in self._fixture().items():
                    zf.writestr(name, content)
            stops, routes, trips, seqs, transfer_rows = _BUILDER.load_from_zip(zip_path)
        finally:
            zip_path.unlink(missing_ok=True)
        self.assertEqual(stops, STOPS)
        self.assertEqual(len(transfer_rows), 4)
        components = _BUILDER.derive_transfer_components(stops, transfer_rows)
        self.assertEqual(components["P1"], "gtfs_transfer:P1")
        self.assertEqual(components["P3"], "gtfs_transfer:P1")
        self.assertNotIn("P4", components)
        artifact = _BUILDER.build_patterns(
            stops, routes, trips, seqs, transfer_components=components
        )
        self.assertEqual(artifact["stops"]["P2"]["station_complex_id"], "gtfs_transfer:P1")

    def test_zip_without_transfers_txt_fails_loudly(self):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            zip_path = Path(f.name)
        try:
            with zipfile.ZipFile(zip_path, "w") as zf:
                for name, content in self._fixture(include_transfers=False).items():
                    zf.writestr(name, content)
            # transfers.txt is canonical input: a zip missing it must fail
            # loudly instead of silently building an artifact with no complex
            # metadata.
            with self.assertRaises(KeyError):
                _BUILDER.load_from_zip(zip_path)
        finally:
            zip_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
