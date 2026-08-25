"""Focused no-network tests for the PostgreSQL GTFS migration wiring.

The migration rebuilds the transactional schema plus the authoritative
``transfers`` table, copies ``transfers.txt`` verbatim, and fails loudly
without committing when ``transfers.txt`` is missing or malformed. These
tests use a tiny local fixture zip and a fake psycopg2 connection; no live
PostgreSQL server, DATABASE_URL, or network access is required.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.services.mta.static_gtfs import migration as migrate_gtfs

STOPS_TXT = (
    "stop_id,stop_name,stop_lat,stop_lon,parent_station,location_type\n"
    "S1,Station,40.7,-74.0,,\n"
)
TRIPS_TXT = "trip_id,route_id\nT1,R1\n"
STOP_TIMES_TXT = "trip_id,stop_id,stop_sequence\nT1,S1,1\n"
TRANSFERS_TXT = (
    "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"
    "A01,A02,2,60\n"
    "B01,C01,0,\n"
    "D01,D02,1,90\n"
)


def _write_fixture_zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.copies: list[dict] = []
        self.events: list[tuple[str, str]] = []
        self.closed = False

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        self.events.append(("execute", sql))

    def copy_from(self, file, table, columns=None, **kwargs) -> None:
        file.seek(0)
        self.copies.append(
            {
                "table": table,
                "columns": tuple(columns) if columns else None,
                "data": file.read(),
            }
        )
        self.events.append(("copy", table))

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor | None = None) -> None:
        self._cursor = cursor if cursor is not None else _FakeCursor()
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


class _CursorFailureConnection(_FakeConnection):
    def cursor(self) -> _FakeCursor:
        raise RuntimeError("cursor creation failed")


class _CloseFailureCursor(_FakeCursor):
    def close(self) -> None:
        self.closed = True
        raise RuntimeError("cursor close failed")


def _run_migrate(zip_path: Path, fake_conn: _FakeConnection) -> None:
    with mock.patch.object(migrate_gtfs.psycopg2, "connect", return_value=fake_conn):
        with mock.patch.object(migrate_gtfs, "download_gtfs", return_value=zip_path):
            migrate_gtfs.migrate()


class MigrateGtfsTransfersTests(unittest.TestCase):
    def test_migrate_declares_copies_and_commits_transfers(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _write_fixture_zip(
                Path(tmp) / "gtfs_fixture.zip",
                {
                    "stops.txt": STOPS_TXT,
                    "trips.txt": TRIPS_TXT,
                    "stop_times.txt": STOP_TIMES_TXT,
                    "transfers.txt": TRANSFERS_TXT,
                },
            )
            fake_conn = _FakeConnection()
            _run_migrate(zip_path, fake_conn)
            # The downloaded temp input is removed after the migration.
            self.assertFalse(zip_path.exists())
            self.assertTrue(fake_conn.cursor().closed)
            self.assertTrue(fake_conn.closed)

        cur = fake_conn.cursor()

        # The transfers table is declared in the same DDL as the other tables.
        create_sql = cur.executed[0]
        self.assertIn("DROP TABLE IF EXISTS transfers;", create_sql)
        self.assertIn("CREATE TABLE transfers (", create_sql)
        for column in ("from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"):
            self.assertIn(f"{column} TEXT", create_sql)

        # transfers.txt is copied with exactly the four GTFS fields, values kept
        # verbatim (including a blank min_transfer_time).
        self.assertEqual(
            [copy["table"] for copy in cur.copies],
            ["stops", "trips", "stop_times", "transfers"],
        )
        transfers_copy = cur.copies[-1]
        self.assertEqual(
            transfers_copy["columns"],
            ("from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"),
        )
        self.assertEqual(
            transfers_copy["data"],
            "A01\tA02\t2\t60\nB01\tC01\t0\t\nD01\tD02\t1\t90\n",
        )

        # The transaction commits through the existing final SQL statement,
        # after the transfers copy, and the from_stop_id index is declared.
        commit_event = next(
            event
            for event in cur.events
            if event[0] == "execute" and "COMMIT;" in event[1]
        )
        self.assertLess(
            cur.events.index(("copy", "transfers")),
            cur.events.index(commit_event),
        )
        self.assertIn(
            "CREATE INDEX idx_transfers_from ON transfers(from_stop_id);",
            commit_event[1],
        )

    def test_missing_transfers_txt_fails_without_commit_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _write_fixture_zip(
                Path(tmp) / "gtfs_fixture.zip",
                {
                    "stops.txt": STOPS_TXT,
                    "trips.txt": TRIPS_TXT,
                    "stop_times.txt": STOP_TIMES_TXT,
                },
            )
            fake_conn = _FakeConnection()
            with self.assertRaises(KeyError):
                _run_migrate(zip_path, fake_conn)
            self.assertFalse(zip_path.exists())
            self.assertTrue(fake_conn.cursor().closed)
            self.assertTrue(fake_conn.closed)

        cur = fake_conn.cursor()
        self.assertFalse(any("COMMIT;" in sql for sql in cur.executed))

    def test_malformed_transfers_txt_fails_without_commit_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _write_fixture_zip(
                Path(tmp) / "gtfs_fixture.zip",
                {
                    "stops.txt": STOPS_TXT,
                    "trips.txt": TRIPS_TXT,
                    "stop_times.txt": STOP_TIMES_TXT,
                    "transfers.txt": "from,to,type,time\nA01,A02,2,60\n",
                },
            )
            fake_conn = _FakeConnection()
            with self.assertRaises(KeyError):
                _run_migrate(zip_path, fake_conn)
            self.assertFalse(zip_path.exists())
            self.assertTrue(fake_conn.cursor().closed)
            self.assertTrue(fake_conn.closed)

        cur = fake_conn.cursor()
        self.assertFalse(any("COMMIT;" in sql for sql in cur.executed))

    def test_cursor_creation_failure_closes_connection_and_unlinks_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _write_fixture_zip(
                Path(tmp) / "gtfs_fixture.zip",
                {
                    "stops.txt": STOPS_TXT,
                    "trips.txt": TRIPS_TXT,
                    "stop_times.txt": STOP_TIMES_TXT,
                    "transfers.txt": TRANSFERS_TXT,
                },
            )
            fake_conn = _CursorFailureConnection()
            with self.assertRaisesRegex(RuntimeError, "cursor creation failed"):
                _run_migrate(zip_path, fake_conn)
            # A created connection is still closed when cursor creation fails.
            self.assertTrue(fake_conn.closed)
            self.assertFalse(zip_path.exists())

    def test_cursor_close_failure_still_closes_connection_and_unlinks_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = _write_fixture_zip(
                Path(tmp) / "gtfs_fixture.zip",
                {
                    "stops.txt": STOPS_TXT,
                    "trips.txt": TRIPS_TXT,
                    "stop_times.txt": STOP_TIMES_TXT,
                    "transfers.txt": TRANSFERS_TXT,
                },
            )
            fake_conn = _FakeConnection(cursor=_CloseFailureCursor())
            with self.assertRaisesRegex(RuntimeError, "cursor close failed"):
                _run_migrate(zip_path, fake_conn)
            # Cursor close raising must not skip connection close.
            self.assertTrue(fake_conn.cursor().closed)
            self.assertTrue(fake_conn.closed)
            self.assertFalse(zip_path.exists())


if __name__ == "__main__":
    unittest.main()
