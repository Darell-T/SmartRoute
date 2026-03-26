import csv
import os
import tempfile
import time
import zipfile
from pathlib import Path

import httpx


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
        self.stops_by_id = {}
        self.routes_by_id = {}
        self.transfers_by_stop = {}
        self.routes_by_stop = {}
        self.trips_to_routes = {}
        self.stops_by_trip = {}
        self._load_all()

    def _load_all(self):
        """(Re)load all CSV data from the active data directory."""
        self._data_dir = _active_data_dir()
        print(f"[gtfs] loading data from {self._data_dir}")

        # Stream CSVs into in-memory indexes to avoid large temporary lists.
        stops_by_id: dict = {}
        for row in self._iter_csv("stops.txt"):
            stop_id = row.get("stop_id")
            if stop_id:
                stops_by_id[stop_id] = row

        routes_by_id: dict = {}
        for row in self._iter_csv("routes.txt"):
            route_id = row.get("route_id")
            if route_id:
                routes_by_id[route_id] = row

        transfers_by_stop: dict = {}
        for row in self._iter_csv("transfers.txt"):
            from_stop_id = row.get("from_stop_id")
            if from_stop_id:
                transfers_by_stop.setdefault(from_stop_id, []).append(row)

        trips_to_routes: dict = {}
        for row in self._iter_csv("trips.txt"):
            trip_id = row.get("trip_id")
            route_id = row.get("route_id")
            if trip_id and route_id:
                trips_to_routes[trip_id] = route_id

        # build routes by stop
        routes_by_stop: dict = {}
        stops_by_trip: dict = {}
        for data in self._iter_csv("stop_times.txt"):
            trip_id = data.get("trip_id")
            stop_id = data.get("stop_id")
            if not trip_id or not stop_id:
                continue

            route = trips_to_routes.get(trip_id)
            if route is None:
                continue

            stop_sequence = data.get("stop_sequence")
            try:
                seq = int(stop_sequence) if stop_sequence is not None else None
            except ValueError:
                continue
            if seq is None:
                continue

            routes_by_stop.setdefault(stop_id, set()).add(route)
            stops_by_trip.setdefault(trip_id, []).append((seq, stop_id))

        self.stops_by_id = stops_by_id
        self.routes_by_id = routes_by_id
        self.transfers_by_stop = transfers_by_stop
        self.trips_to_routes = trips_to_routes
        self.routes_by_stop = routes_by_stop
        self.stops_by_trip = stops_by_trip

    def _iter_csv(self, filename: str):
        with open(self._data_dir / filename, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield row

    def reload(self):
        """Re-read CSV files and rebuild all dictionaries in place."""
        self._load_all()

    def _load_csv(self, filename: str) -> list:
        with open(self._data_dir / filename, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def _buildIdxDict(self, data: list, key: str) -> dict:
        result = {}
        for dataset in data:
            result[dataset[key]] = dataset
        return result

    def _buildGroupDict(self, data: list, key: str) -> dict:
        result = {}
        for dataset in data:
            result.setdefault(dataset[key], []).append(dataset)
        return result

    def get_stop_by_name(self, name: str) -> list:
        stops = []
        for stop in self.stops_by_id.values():
            if stop["stop_name"] == name:
                stops.append(stop["stop_id"])
        return stops

    def get_routes_for_stops(self, stop_id):
        routes = set()
        for stop in self.stops_by_id.values():
            if stop["parent_station"] == stop_id:
                child_id = stop["stop_id"]
                routes.update(self.routes_by_stop.get(child_id, set()))
        return routes

    def get_transfers(self, stop_id):
        return self.transfers_by_stop.get(stop_id, [])
    
    def get_intermediate_stops(self, route_id: str, origin: str, dest: str) -> list:
        origin_ids = {oid.rstrip("NS") for oid in self.get_stop_by_name(origin)}
        dest_ids = {did.rstrip("NS") for did in self.get_stop_by_name(dest)}

        for tid, rid in self.trips_to_routes.items():
            if rid != route_id:
                continue

            stop_id_list = [sid for _, sid in sorted(self.stops_by_trip[tid])]

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
                return [self.stops_by_id[sid]["stop_name"] for sid in route_stop_ids]

        return []
