import csv
import tempfile
import time
import zipfile
from pathlib import Path

import httpx
import sqlite3


_backend_dir = Path(__file__).parent.parent.parent
_supplemented_dir = _backend_dir / "data" / "gtfs_supplemented"
_static_dir = _backend_dir / "data" / "gtfs_static"

SUPPLEMENTED_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip"
_REFRESH_INTERVAL = 3600  # 1 hour in seconds
_downloading = False  # simple flag to prevent concurrent downloads


def _active_data_dir() -> Path:
    """Return supplemented dir if complete, otherwise fall back to static."""
    st = _supplemented_dir / "stop_times.txt"
    if _supplemented_dir.exists() and (_supplemented_dir / "stops.txt").exists() and st.exists():
        return _supplemented_dir
    return _static_dir


def download_supplemented_gtfs() -> bool:
    """Download the MTA supplemented GTFS zip if local files are stale (>1h).

    Returns True if new data was downloaded, False otherwise.
    Uses a simple flag to prevent concurrent downloads.
    """
    global _downloading
    if _downloading:
        print("[gtfs] download already in progress, skipping")
        return False

    # Check freshness — skip if files are recent enough (stop_times is the large required file)
    marker = _supplemented_dir / "stop_times.txt"
    if marker.exists():
        age = time.time() - marker.stat().st_mtime
        if age < _REFRESH_INTERVAL:
            print(f"[gtfs] supplemented data is {age:.0f}s old, still fresh")
            return False

    _downloading = True
    try:
        print("[gtfs] downloading supplemented GTFS...")
        _supplemented_dir.mkdir(parents=True, exist_ok=True)
        temp_zip_path = None

        with httpx.stream("GET", SUPPLEMENTED_URL, timeout=60, follow_redirects=True) as resp:
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp_zip:
                temp_zip_path = Path(temp_zip.name)
                for chunk in resp.iter_bytes():
                    temp_zip.write(chunk)

        with zipfile.ZipFile(temp_zip_path) as zf:
            zf.extractall(_supplemented_dir)

        print(f"[gtfs] supplemented GTFS extracted to {_supplemented_dir}")
        return True

    except Exception as exc:
        print(f"[gtfs] download failed, keeping existing files: {exc}")
        return False
    finally:
        if "temp_zip_path" in locals() and temp_zip_path and temp_zip_path.exists():
            try:
                temp_zip_path.unlink()
            except OSError:
                pass
        _downloading = False


class GTFSStaticData:

    def __init__(self):
        self.db: sqlite3.Connection | None = None
        self._data_dir = _active_data_dir()
        self._load_tables()

    def _load_tables(self):
        """Load all GTFS CSV data into an in-memory SQLite database."""
        print(f"[gtfs] loading data from {self._data_dir}")
        t0 = time.time()

        if self.db:
            self.db.close()

        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = sqlite3.Row
        cur = db.cursor()

        # Create tables
        cur.executescript("""
            CREATE TABLE stops (
                stop_id TEXT PRIMARY KEY,
                stop_name TEXT,
                stop_lat REAL,
                stop_lon REAL,
                parent_station TEXT,
                location_type TEXT
            );
            CREATE TABLE routes (
                route_id TEXT PRIMARY KEY,
                route_short_name TEXT,
                route_long_name TEXT,
                route_type TEXT,
                route_color TEXT,
                route_text_color TEXT
            );
            CREATE TABLE transfers (
                from_stop_id TEXT,
                to_stop_id TEXT,
                transfer_type TEXT,
                min_transfer_time TEXT
            );
            CREATE TABLE trips (
                trip_id TEXT PRIMARY KEY,
                route_id TEXT
            );
            CREATE TABLE stop_times (
                trip_id TEXT,
                stop_id TEXT,
                stop_sequence INTEGER
            );
        """)

        # Bulk-load stops
        cur.executemany(
            "INSERT OR IGNORE INTO stops VALUES (?,?,?,?,?,?)",
            self._read_cols("stops.txt",
                            ["stop_id", "stop_name", "stop_lat", "stop_lon",
                             "parent_station", "location_type"]),
        )

        # Bulk-load routes
        cur.executemany(
            "INSERT OR IGNORE INTO routes VALUES (?,?,?,?,?,?)",
            self._read_cols("routes.txt",
                            ["route_id", "route_short_name", "route_long_name",
                             "route_type", "route_color", "route_text_color"]),
        )

        # Bulk-load transfers
        cur.executemany(
            "INSERT INTO transfers VALUES (?,?,?,?)",
            self._read_cols("transfers.txt",
                            ["from_stop_id", "to_stop_id", "transfer_type",
                             "min_transfer_time"]),
        )

        # Bulk-load trips (only need trip_id and route_id)
        cur.executemany(
            "INSERT OR IGNORE INTO trips VALUES (?,?)",
            self._read_cols("trips.txt", ["trip_id", "route_id"]),
        )

        # Bulk-load stop_times — the big one (~900k rows)
        cur.executemany(
            "INSERT INTO stop_times VALUES (?,?,?)",
            self._read_cols("stop_times.txt",
                            ["trip_id", "stop_id", "stop_sequence"]),
        )

        # Create indexes AFTER bulk insert (faster than indexing during insert)
        cur.executescript("""
            CREATE INDEX idx_stops_name ON stops(stop_name);
            CREATE INDEX idx_stops_parent ON stops(parent_station);
            CREATE INDEX idx_transfers_from ON transfers(from_stop_id);
            CREATE INDEX idx_trips_route ON trips(route_id);
            CREATE INDEX idx_stop_times_trip ON stop_times(trip_id);
            CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
        """)

        db.commit()
        self.db = db
        print(f"[gtfs] SQLite loaded in {time.time() - t0:.2f}s")

    def _read_cols(self, filename: str, columns: list[str]):
        """Yield tuples of the requested columns from a GTFS CSV file."""
        path = self._data_dir / filename
        if not path.exists():
            return
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield tuple(row.get(c, "") for c in columns)

    def reload(self):
        """Re-read CSV files and rebuild SQLite tables."""
        self._data_dir = _active_data_dir()
        self._load_tables()

    # ------------------------------------------------------------------
    # Query helpers (replace former dict lookups)
    # ------------------------------------------------------------------

    def get_stop(self, stop_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM stops WHERE stop_id = ?", (stop_id,)).fetchone()
        return dict(row) if row else None

    def get_all_parent_stops(self) -> list[dict]:
        """Return all stops where location_type = '1' (stations)."""
        rows = self.db.execute(
            "SELECT stop_id, stop_name, stop_lat, stop_lon, location_type "
            "FROM stops WHERE location_type = '1'"
        ).fetchall()
        return [dict(r) for r in rows]

    def route_exists(self, route_id: str) -> bool:
        row = self.db.execute("SELECT 1 FROM routes WHERE route_id = ?", (route_id,)).fetchone()
        return row is not None

    def get_stop_by_name(self, name: str) -> list:
        rows = self.db.execute(
            "SELECT stop_id FROM stops WHERE stop_name = ?", (name,)
        ).fetchall()
        return [r[0] for r in rows]

    def get_routes_for_stops(self, stop_id):
        rows = self.db.execute("""
            SELECT DISTINCT t.route_id
            FROM stops s
            JOIN stop_times st ON st.stop_id = s.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            WHERE s.parent_station = ?
        """, (stop_id,)).fetchall()
        return {r[0] for r in rows}

    def get_transfers(self, stop_id):
        rows = self.db.execute(
            "SELECT * FROM transfers WHERE from_stop_id = ?", (stop_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_intermediate_stops(self, route_id: str, origin: str, dest: str) -> list:
        if not self.route_exists(route_id):
            return []

        origin_ids = {oid.rstrip("NS") for oid in self.get_stop_by_name(origin)}
        dest_ids = {did.rstrip("NS") for did in self.get_stop_by_name(dest)}
        if not origin_ids or not dest_ids:
            return []

        # Get one trip for this route
        trip_rows = self.db.execute(
            "SELECT trip_id FROM trips WHERE route_id = ?", (route_id,)
        ).fetchall()

        for (tid,) in trip_rows:
            stops = self.db.execute(
                "SELECT stop_id FROM stop_times WHERE trip_id = ? ORDER BY stop_sequence",
                (tid,)
            ).fetchall()
            stop_id_list = [r[0] for r in stops]

            origin_idx = None
            dest_idx = None
            for i, sid in enumerate(stop_id_list):
                stripped = sid.rstrip("NS")
                if origin_idx is None and stripped in origin_ids:
                    origin_idx = i
                if stripped in dest_ids:
                    dest_idx = i
                    if origin_idx is not None:
                        break

            if origin_idx is not None and dest_idx is not None and origin_idx < dest_idx:
                route_stop_ids = stop_id_list[origin_idx:dest_idx + 1]
                names = []
                for sid in route_stop_ids:
                    row = self.db.execute(
                        "SELECT stop_name FROM stops WHERE stop_id = ?", (sid,)
                    ).fetchone()
                    names.append(row[0] if row else sid)
                return names

        return []
