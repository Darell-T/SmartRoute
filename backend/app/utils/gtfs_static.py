import csv
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import httpx
import sqlite3


_backend_dir = Path(__file__).parent.parent.parent
_supplemented_dir = _backend_dir / "data" / "gtfs_supplemented"
_static_dir = _backend_dir / "data" / "gtfs_static"
_DB_CURRENT = _backend_dir / "data" / "gtfs_current.db"
_DB_NEXT    = _backend_dir / "data" / "gtfs_next.db"

SUPPLEMENTED_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip"
_REFRESH_INTERVAL = 3600  # 1 hour in seconds
_download_lock = threading.Lock()
_local = threading.local()
_db_epoch = 0
_db_epoch_lock = threading.Lock()


def _active_data_dir() -> Path:
    """Return supplemented dir if complete, otherwise fall back to static."""
    st = _supplemented_dir / "stop_times.txt"
    if _supplemented_dir.exists() and (_supplemented_dir / "stops.txt").exists() and st.exists():
        return _supplemented_dir
    return _static_dir


def download_supplemented_gtfs() -> bool:
    """Download the MTA supplemented GTFS zip if local files are stale (>1h).

    Returns True if new data was downloaded, False otherwise.
    Uses a threading lock to prevent concurrent downloads.
    """
    if not _download_lock.acquire(blocking=False):
        print("[gtfs] download already in progress, skipping")
        return False

    # Check freshness — skip if files are recent enough (stop_times is the large required file)
    marker = _supplemented_dir / "stop_times.txt"
    if marker.exists():
        age = time.time() - marker.stat().st_mtime
        size_mb = marker.stat().st_size / (1024 * 1024)
        if age < _REFRESH_INTERVAL:
            print(f"[gtfs] supplemented data is {age:.0f}s old (fresh), stop_times={size_mb:.1f}MB — skipping download")
            _download_lock.release()
            return False
        print(f"[gtfs] supplemented data is {age:.0f}s old (stale) — re-downloading")
    else:
        print(f"[gtfs] no local supplemented data found at {marker} — cold start, must download")

    t_dl = time.time()
    try:
        print(f"[gtfs] starting download from {SUPPLEMENTED_URL}")
        _supplemented_dir.mkdir(parents=True, exist_ok=True)
        temp_zip_path = None
        bytes_written = 0

        with httpx.stream("GET", SUPPLEMENTED_URL, timeout=60, follow_redirects=True) as resp:
            resp.raise_for_status()
            content_length = resp.headers.get("content-length", "unknown")
            print(f"[gtfs] download started — content-length={content_length} bytes")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp_zip:
                temp_zip_path = Path(temp_zip.name)
                for chunk in resp.iter_bytes():
                    temp_zip.write(chunk)
                    bytes_written += len(chunk)

        dl_secs = time.time() - t_dl
        print(f"[gtfs] download complete — {bytes_written/1024/1024:.1f}MB in {dl_secs:.1f}s")

        t_ex = time.time()
        with zipfile.ZipFile(temp_zip_path) as zf:
            names = zf.namelist()
            print(f"[gtfs] zip contains {len(names)} files: {names}")
            zf.extractall(_supplemented_dir)
        print(f"[gtfs] extraction complete in {time.time()-t_ex:.1f}s → {_supplemented_dir}")

        for fname in ["stop_times.txt", "stops.txt", "trips.txt", "routes.txt"]:
            p = _supplemented_dir / fname
            if p.exists():
                print(f"[gtfs]   {fname}: {p.stat().st_size/1024/1024:.1f}MB")

        return True

    except Exception as exc:
        print(f"[gtfs] download failed after {time.time()-t_dl:.1f}s, keeping existing files: {exc}")
        return False
    finally:
        if "temp_zip_path" in locals() and temp_zip_path and temp_zip_path.exists():
            try:
                temp_zip_path.unlink()
            except OSError:
                pass
        _download_lock.release()


class GTFSStaticData:

    def __init__(self):
        self.db: sqlite3.Connection | None = None
        self._data_dir = _active_data_dir()

        if _DB_CURRENT.exists():
            print("[gtfs] persisted DB found — skipping rebuild, opening existing DB")
            self._open_current()
        else:
            print("[gtfs] no persisted DB found — cold start, building now")
            self._build_db(_DB_CURRENT)
            self._open_current()

    def _open_current(self) -> None:
        self.db = sqlite3.connect(
            f"file:{_DB_CURRENT}?mode=ro&cache=shared",
            uri=True,
            check_same_thread=False,
        )
        self.db.row_factory = sqlite3.Row

    def _build_db(self, path: Path) -> None:
        """Build a SQLite database at path from CSVs, then close it."""
        if path.exists():
            path.unlink()

        db = sqlite3.connect(str(path), check_same_thread=False)
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.execute("PRAGMA cache_size = -64000")
        self.db = db

        self._load_tables()

        db.commit()
        db.close()

    def _load_tables(self):
        """Load all GTFS CSV data into the current self.db connection."""
        print(f"[gtfs] _load_tables: reading from {self._data_dir}")

        for fname in ["stops.txt", "routes.txt", "transfers.txt", "trips.txt", "stop_times.txt"]:
            p = self._data_dir / fname
            if p.exists():
                print(f"[gtfs]   {fname}: {p.stat().st_size/1024/1024:.2f}MB")
            else:
                print(f"[gtfs]   {fname}: MISSING")

        t0 = time.time()

        cur = self.db.cursor()

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

        t1 = time.time()
        cur.executemany(
            "INSERT OR IGNORE INTO stops VALUES (?,?,?,?,?,?)",
            self._read_cols("stops.txt",
                            ["stop_id", "stop_name", "stop_lat", "stop_lon",
                             "parent_station", "location_type"]),
        )
        print(f"[gtfs]   stops loaded in {time.time()-t1:.2f}s ({cur.rowcount} rows)")

        t1 = time.time()
        cur.executemany(
            "INSERT OR IGNORE INTO routes VALUES (?,?,?,?,?,?)",
            self._read_cols("routes.txt",
                            ["route_id", "route_short_name", "route_long_name",
                             "route_type", "route_color", "route_text_color"]),
        )
        print(f"[gtfs]   routes loaded in {time.time()-t1:.2f}s ({cur.rowcount} rows)")

        t1 = time.time()
        cur.executemany(
            "INSERT INTO transfers VALUES (?,?,?,?)",
            self._read_cols("transfers.txt",
                            ["from_stop_id", "to_stop_id", "transfer_type",
                             "min_transfer_time"]),
        )
        print(f"[gtfs]   transfers loaded in {time.time()-t1:.2f}s ({cur.rowcount} rows)")

        t1 = time.time()
        cur.executemany(
            "INSERT OR IGNORE INTO trips VALUES (?,?)",
            self._read_cols("trips.txt", ["trip_id", "route_id"]),
        )
        print(f"[gtfs]   trips loaded in {time.time()-t1:.2f}s ({cur.rowcount} rows)")

        t1 = time.time()
        cur.executemany(
            "INSERT INTO stop_times VALUES (?,?,?)",
            self._read_cols("stop_times.txt",
                            ["trip_id", "stop_id", "stop_sequence"]),
        )
        print(f"[gtfs]   stop_times loaded in {time.time()-t1:.2f}s ({cur.rowcount} rows)")

        t1 = time.time()
        cur.executescript("""
            CREATE INDEX idx_stops_name ON stops(stop_name);
            CREATE INDEX idx_stops_parent ON stops(parent_station);
            CREATE INDEX idx_transfers_from ON transfers(from_stop_id);
            CREATE INDEX idx_trips_route ON trips(route_id);
            CREATE INDEX idx_stop_times_trip ON stop_times(trip_id);
            CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
        """)
        print(f"[gtfs]   indexes built in {time.time()-t1:.2f}s")

        print(f"[gtfs] SQLite ready — total load time {time.time() - t0:.2f}s")

    def _read_cols(self, filename: str, columns: list[str]):
        """Yield tuples of the requested columns from a GTFS CSV file."""
        path = self._data_dir / filename
        if not path.exists():
            return
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield tuple(row.get(c, "") for c in columns)

    def reload(self) -> None:
        """Build a new DB in the background, then atomically swap it in."""
        self._data_dir = _active_data_dir()
        print("[gtfs] reload: building gtfs_next.db")

        prev_serving_db = self.db
        self._build_db(_DB_NEXT)

        # Atomic rename — single syscall, readers on the old file are unaffected
        _DB_NEXT.replace(_DB_CURRENT)
        print("[gtfs] reload: swapped gtfs_next → gtfs_current")

        self._open_current()
        if prev_serving_db is not None:
            prev_serving_db.close()

        # Advance DB epoch so all worker threads lazily reconnect on next query.
        global _db_epoch
        with _db_epoch_lock:
            _db_epoch += 1

        # Invalidate the current thread's local connection immediately.
        local_db = getattr(_local, "db", None)
        if local_db is not None:
            local_db.close()
        _local.db = None
        _local.db_epoch = _db_epoch
        print("[gtfs] reload: complete")

    def _get_conn(self) -> sqlite3.Connection:
        local_db = getattr(_local, "db", None)
        local_epoch = getattr(_local, "db_epoch", -1)
        if local_db is None or local_epoch != _db_epoch:
            if local_db is not None:
                local_db.close()
            _local.db = sqlite3.connect(
                f"file:{_DB_CURRENT}?mode=ro&cache=shared",
                uri=True,
                check_same_thread=False,
            )
            _local.db.row_factory = sqlite3.Row
            _local.db_epoch = _db_epoch
        return _local.db

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_stop(self, stop_id: str) -> dict | None:
        row = self._get_conn().execute("SELECT * FROM stops WHERE stop_id = ?", (stop_id,)).fetchone()
        return dict(row) if row else None

    def get_all_parent_stops(self) -> list[dict]:
        """Return all stops where location_type = '1' (stations)."""
        rows = self._get_conn().execute(
            "SELECT stop_id, stop_name, stop_lat, stop_lon, location_type "
            "FROM stops WHERE location_type = '1'"
        ).fetchall()
        return [dict(r) for r in rows]

    def route_exists(self, route_id: str) -> bool:
        row = self._get_conn().execute("SELECT 1 FROM routes WHERE route_id = ?", (route_id,)).fetchone()
        return row is not None

    def get_stop_by_name(self, name: str) -> list:
        rows = self._get_conn().execute(
            "SELECT stop_id FROM stops WHERE stop_name = ?", (name,)
        ).fetchall()
        return [r[0] for r in rows]

    def get_routes_for_stops(self, stop_id):
        rows = self._get_conn().execute("""
            SELECT DISTINCT t.route_id
            FROM stops s
            JOIN stop_times st ON st.stop_id = s.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            WHERE s.parent_station = ?
        """, (stop_id,)).fetchall()
        return {r[0] for r in rows}

    def get_transfers(self, stop_id):
        rows = self._get_conn().execute(
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

        conn = self._get_conn()

        trip_rows = conn.execute(
            "SELECT trip_id FROM trips WHERE route_id = ?", (route_id,)
        ).fetchall()

        for (tid,) in trip_rows:
            stops = conn.execute(
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
                    row = conn.execute(
                        "SELECT stop_name FROM stops WHERE stop_id = ?", (sid,)
                    ).fetchone()
                    names.append(row[0] if row else sid)
                return names

        return []
