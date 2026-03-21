import csv
import io
import os
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
    """Return supplemented dir if it has data, otherwise fall back to static."""
    if _supplemented_dir.exists() and (_supplemented_dir / "stops.txt").exists():
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

    # Check freshness — skip if files are recent enough
    marker = _supplemented_dir / "stops.txt"
    if marker.exists():
        age = time.time() - marker.stat().st_mtime
        if age < _REFRESH_INTERVAL:
            print(f"[gtfs] supplemented data is {age:.0f}s old, still fresh")
            return False

    _downloading = True
    try:
        print("[gtfs] downloading supplemented GTFS...")
        resp = httpx.get(SUPPLEMENTED_URL, timeout=60, follow_redirects=True)
        resp.raise_for_status()

        _supplemented_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(_supplemented_dir)

        print(f"[gtfs] supplemented GTFS extracted to {_supplemented_dir}")
        return True

    except Exception as exc:
        print(f"[gtfs] download failed, keeping existing files: {exc}")
        return False
    finally:
        _downloading = False


class GTFSStaticData:

    def __init__(self):
        self.stops_by_id = {}
        self.routes_by_id = {}
        self.transfers_by_stop = {}
        self.routes_by_stop = {}
        self.trips_to_routes = {}
        self._load_all()

    def _load_all(self):
        """(Re)load all CSV data from the active data directory."""
        self._data_dir = _active_data_dir()
        print(f"[gtfs] loading data from {self._data_dir}")

        # load all data
        stops = self._load_csv("stops.txt")
        routes = self._load_csv("routes.txt")
        transfers = self._load_csv("transfers.txt")
        trips = self._load_csv("trips.txt")
        stop_times = self._load_csv("stop_times.txt")

        # insert data into dictionaries
        self.stops_by_id = self._buildIdxDict(stops, "stop_id")
        self.routes_by_id = self._buildIdxDict(routes, "route_id")
        self.transfers_by_stop = self._buildGroupDict(transfers, "from_stop_id")
        self.trips_to_routes = {trip["trip_id"]: trip["route_id"] for trip in trips}

        # build routes by stop
        routes_by_stop: dict = {}
        for data in stop_times:
            trip_id = data["trip_id"]
            stop_id = data["stop_id"]
            route = self.trips_to_routes[trip_id]
            routes_by_stop.setdefault(stop_id, set()).add(route)
        self.routes_by_stop = routes_by_stop

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


# this is for testing only will clean up later
if __name__ == "__main__":
    download_supplemented_gtfs()
    test = GTFSStaticData()

    print("=== Stop Lookup ===")
    print("Times Sq stop IDs:", test.get_stop_by_name("Times Sq-42 St"))

    print("\n=== Routes for Stations ===")
    print("Routes at Van Cortlandt (101):", test.get_routes_for_stops("101"))
    print("Routes at Times Sq (725):", test.get_routes_for_stops("725"))
    print("Routes at Times Sq (127):", test.get_routes_for_stops("127"))

    print("\n=== Transfers ===")
    print("Transfers from 101:", test.get_transfers("101"))

    print("\n=== Stats ===")
    print("Total stops:", len(test.stops_by_id))
    print("Total routes:", len(test.routes_by_id))
    print("Total stops with routes:", len(test.routes_by_stop))
