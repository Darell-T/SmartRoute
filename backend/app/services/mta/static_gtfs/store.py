"""Static GTFS store with its bounded route-segment lookup cache.

The cache remains here intentionally: it is private to this store and shares
the same static-pattern lifecycle, so a separate generic utility module would
only add an import seam without another owner.
"""

import os
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

from app.services.mta.static_gtfs.scheduled_arrivals import ScheduledArrivalIndex
from app.services.mta.static_gtfs.stop_patterns import normalize_station_name

# DATABASE_URL points at a remote Postgres; without a connect timeout and TCP
# keepalives a dead peer leaves connections blocked indefinitely.
_CONNECT_KWARGS = {
    "connect_timeout": 10,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "options": (
        f"-c statement_timeout={os.getenv('DATABASE_STATEMENT_TIMEOUT_MS', '2500')} "
        "-c idle_in_transaction_session_timeout=5000"
    ),
}

_pool = None
_pool_lock = threading.Lock()


def init_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                1, 10, os.getenv("DATABASE_URL"), **_CONNECT_KWARGS
            )
    return _pool


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


def _get_pool():
    if _pool is None:
        return init_pool()
    return _pool


INTERMEDIATE_STOPS_CACHE_MAXSIZE = 512


def _coordinate_key(value) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lat = value.get("latitude", value.get("lat"))
    lng = value.get("longitude", value.get("lng", value.get("lon")))
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None


def _coordinate_payload(value: tuple[float, float] | None) -> dict | None:
    if value is None:
        return None
    return {"latitude": value[0], "longitude": value[1]}


def _freeze_rows(rows) -> tuple[tuple[str, float, float], ...]:
    return tuple(
        (str(row["name"]), float(row["lat"]), float(row["lng"]))
        for row in rows
        if row.get("lat") is not None and row.get("lng") is not None
    )


class BoundedIntermediateStopsCache:
    def __init__(self, gtfs) -> None:
        self._gtfs = gtfs
        self._lookup = lru_cache(maxsize=INTERMEDIATE_STOPS_CACHE_MAXSIZE)(
            self._resolve
        )

    def get(self, route_id, origin, destination, origin_coords, destination_coords):
        rows = self._lookup(
            route_id,
            origin,
            destination,
            _coordinate_key(origin_coords),
            _coordinate_key(destination_coords),
        )
        return [
            {"name": name, "lat": lat, "lng": lng}
            for name, lat, lng in rows
        ]

    def clear(self) -> None:
        self._lookup.cache_clear()

    def info(self):
        return self._lookup.cache_info()

    def _resolve(
        self,
        route_id: str,
        origin: str,
        destination: str,
        origin_coords: tuple[float, float] | None,
        destination_coords: tuple[float, float] | None,
    ) -> tuple[tuple[str, float, float], ...]:
        gtfs = self._gtfs
        origin_payload = _coordinate_payload(origin_coords)
        destination_payload = _coordinate_payload(destination_coords)
        index = gtfs.__dict__.get("_pattern_index")
        if index is not None:
            rows, metadata = index.get_intermediate_stops_with_coords(
                route_id,
                origin,
                destination,
                origin_payload,
                destination_payload,
            )
            counter = "_static_hits" if metadata["hit"] else "_static_misses"
            gtfs.__dict__[counter] = gtfs.__dict__.get(counter, 0) + 1
            if not metadata["hit"]:
                print(
                    f"[gtfs] static MISS route={route_id} origin={origin!r} "
                    f"dest={destination!r} "
                    f"norm_origin={normalize_station_name(origin)!r} "
                    f"norm_dest={normalize_station_name(destination)!r} "
                    f"patterns={metadata['patterns_considered']}"
                )
            return _freeze_rows(rows)

        if not gtfs._db_fallback_enabled():
            print(
                "[gtfs] no static pattern index and DB fallback disabled; "
                f"returning empty for route={route_id} "
                f"{origin!r}->{destination!r}"
            )
            return ()

        rows = gtfs._find_trip_stop_rows(route_id, origin, destination)
        if not rows and (origin_payload or destination_payload):
            origin_ids = (
                gtfs._route_stop_ids_near(route_id, origin_payload)
                or gtfs._ids_for_name(origin)
            )
            destination_ids = (
                gtfs._route_stop_ids_near(route_id, destination_payload)
                or gtfs._ids_for_name(destination)
            )
            rows = gtfs._trip_stops_between(route_id, origin_ids, destination_ids)
        return _freeze_rows([
            {"name": row["stop_name"], "lat": row["stop_lat"], "lng": row["stop_lon"]}
            for row in rows
            if row.get("stop_lat") is not None and row.get("stop_lon") is not None
        ])


class GTFSStaticData:

    def set_pattern_index(self, pattern_index) -> None:
        self.__dict__["_pattern_index"] = pattern_index
        cache = self.__dict__.get("_intermediate_stops_cache_instance")
        if cache is not None:
            cache.clear()

    def intermediate_stops_cache_info(self):
        return self._intermediate_stops_cache().info()

    def _intermediate_stops_cache(self):
        cache = self.__dict__.get("_intermediate_stops_cache_instance")
        if cache is None:
            cache = BoundedIntermediateStopsCache(self)
            self.__dict__["_intermediate_stops_cache_instance"] = cache
        return cache

    def load_scheduled_arrivals(self, path: str | Path | None = None) -> bool:
        """Load the optional preprocessed full-GTFS schedule once at startup."""

        candidate = Path(
            path
            or os.getenv("GTFS_SCHEDULE_ARTIFACT", "")
            or Path(__file__).resolve().parents[3] / "data" / "scheduled_arrivals.json"
        )
        if not candidate.is_file():
            return False
        self.__dict__["_scheduled_arrival_index"] = ScheduledArrivalIndex.load(candidate)
        return True

    def get_scheduled_arrivals(
        self,
        *,
        route_id: str,
        stop_ids,
        direction: str | None,
        now: datetime,
        limit: int,
    ):
        index = self.__dict__.get("_scheduled_arrival_index")
        if index is None:
            return {"status": "unavailable", "predictions": []}
        return index.lookup(
            route_id=route_id,
            stop_ids=stop_ids,
            direction=direction,
            now=now,
            limit=limit,
        )

    def _query(self, sql, params=None):
        self.__dict__["_query_count"] = self.__dict__.get("_query_count", 0) + 1
        pool = _get_pool()
        for attempt in (1, 2):
            conn = pool.getconn()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
            except psycopg2.OperationalError as exc:
                pool.putconn(conn, close=True)
                conn = None
                # SQLSTATE 57014 = statement_timeout fired. Retrying would just
                # wait the full timeout again and, on the synchronous trip path,
                # pile uncancellable query threads onto the pool. Fail fast.
                if getattr(exc, "pgcode", None) == "57014":
                    raise
                # Otherwise a stale pooled connection (server closed it while
                # idle): retry once on a fresh connection.
                if attempt == 2:
                    raise
            except psycopg2.InterfaceError:
                pool.putconn(conn, close=True)
                conn = None
                if attempt == 2:
                    raise
            finally:
                if conn is not None:
                    pool.putconn(conn)


    def _name_index(self) -> dict:
        idx = self.__dict__.get("_name_index_cache")
        if idx is None:
            idx = {}
            try:
                rows = self._query("SELECT stop_id, stop_name FROM stops")
            except Exception:  # noqa: BLE001 name-index query faults fall back to per-name lookup
                rows = []
            for r in rows:
                key = normalize_station_name(r.get("stop_name"))
                if key:
                    idx.setdefault(key, set()).add(r["stop_id"].rstrip("NS"))
            if idx:
                self.__dict__["_name_index_cache"] = idx
        return idx

    def _ids_for_name(self, name: str) -> set:
        idx = self._name_index()
        if idx:
            return set(idx.get(normalize_station_name(name), set()))
        rows = self._query("SELECT stop_id FROM stops WHERE stop_name = %s", (name,))
        return {r["stop_id"].rstrip("NS") for r in rows}

    def _route_stop_ids_near(self, route_id: str, coords) -> set:
        if not coords:
            return set()
        lat = coords.get("latitude")
        lng = coords.get("longitude")
        if lat is None or lng is None:
            return set()
        rows = self._query(
            """
            SELECT s.stop_id, s.stop_lat, s.stop_lon
            FROM stop_times st
            JOIN stops s ON s.stop_id = st.stop_id
            WHERE st.trip_id = (SELECT trip_id FROM trips WHERE route_id = %s LIMIT 1)
              AND s.stop_lat IS NOT NULL AND s.stop_lon IS NOT NULL
            """,
            (route_id,),
        )
        best_id = None
        best_d = None
        for r in rows:
            d = (r["stop_lat"] - lat) ** 2 + (r["stop_lon"] - lng) ** 2
            if best_d is None or d < best_d:
                best_d = d
                best_id = r["stop_id"]
        return {best_id.rstrip("NS")} if best_id else set()

    def _trip_stops_between(self, route_id: str, origin_ids: set, dest_ids: set) -> list:
        if not origin_ids or not dest_ids:
            return []

        origin_list = sorted(origin_ids)
        dest_list = sorted(dest_ids)
        return self._query(
            """
            WITH matching_trips AS (
                SELECT
                    st.trip_id,
                    MIN(st.stop_sequence) FILTER (
                        WHERE rtrim(st.stop_id, 'NS') = ANY(%s)
                    ) AS origin_seq,
                    MIN(st.stop_sequence) FILTER (
                        WHERE rtrim(st.stop_id, 'NS') = ANY(%s)
                    ) AS dest_seq
                FROM trips t
                JOIN stop_times st ON st.trip_id = t.trip_id
                WHERE t.route_id = %s
                GROUP BY st.trip_id
            ),
            chosen_trip AS (
                SELECT trip_id, origin_seq, dest_seq
                FROM matching_trips
                WHERE origin_seq IS NOT NULL
                  AND dest_seq IS NOT NULL
                  AND origin_seq < dest_seq
                ORDER BY (dest_seq - origin_seq), trip_id
                LIMIT 1
            )
            SELECT
                st.stop_id,
                COALESCE(s.stop_name, st.stop_id) AS stop_name,
                s.stop_lat,
                s.stop_lon
            FROM chosen_trip ct
            JOIN stop_times st
              ON st.trip_id = ct.trip_id
             AND st.stop_sequence BETWEEN ct.origin_seq AND ct.dest_seq
            LEFT JOIN stops s ON s.stop_id = st.stop_id
            ORDER BY st.stop_sequence
            """,
            (origin_list, dest_list, route_id),
        )

    def _find_trip_stop_rows(self, route_id: str, origin: str, dest: str) -> list:
        return self._trip_stops_between(
            route_id, self._ids_for_name(origin), self._ids_for_name(dest)
        )

    def _db_fallback_enabled(self) -> bool:
        return (
            os.getenv("GTFS_DB_FALLBACK", "0") == "1"
            or bool(self.__dict__.get("_allow_db_fallback"))
        )

    def get_intermediate_stops(self, route_id: str, origin: str, dest: str) -> list:
        idx = self.__dict__.get("_pattern_index")
        if idx is not None:
            return idx.get_intermediate_stops(route_id, origin, dest)
        if self._db_fallback_enabled():
            return [r["stop_name"] for r in self._find_trip_stop_rows(route_id, origin, dest)]
        return []

    def get_intermediate_stops_with_coords(
        self,
        route_id: str,
        origin: str,
        dest: str,
        origin_coords=None,
        dest_coords=None,
    ) -> list:
        """Ordered stops origin..dest inclusive as [{name, lat, lng}] for the map.

        Fix B: resolved entirely from the in-memory static stop-pattern index
        (_pattern_index, loaded once at startup) -- NO remote-DB query on the
        trip hot path. The legacy DB path remains only as an explicit debug
        fallback (see _db_fallback_enabled). Results are memoized per instance
        with an explicit bound."""
        return self._intermediate_stops_cache().get(
            route_id,
            origin,
            dest,
            origin_coords,
            dest_coords,
        )

    def get_all_parent_stops(self):
        index = self.__dict__.get("_pattern_index")
        if index is not None:
            return index.all_parent_stops()
        return self._query(
            "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops WHERE location_type = '1'"
        )

    def get_unique_routes_for_stops(self, nearest_stops):
        seen_routes = set()
        result = {}

        for stop in nearest_stops:
            rows = self._query(
                """
                SELECT DISTINCT t.route_id
                FROM stops s
                JOIN stop_times st ON st.stop_id = s.stop_id
                JOIN trips t ON t.trip_id = st.trip_id
                WHERE s.parent_station = %s
                """, (stop["stop_id"],)
            )
            routes = [r["route_id"] for r in rows]
            new_routes = [r for r in routes if r not in seen_routes]

            if new_routes:
                result[stop["stop_name"]] = new_routes
                seen_routes.update(new_routes)

        return result

    def get_route_ids_for_parent_stop(self, parent_stop_id: str):
        index = self.__dict__.get("_pattern_index")
        if index is not None:
            return index.route_ids_for_parent_stop(parent_stop_id)
        rows = self._query(
            """
            SELECT DISTINCT t.route_id
            FROM stops s
            JOIN stop_times st ON st.stop_id = s.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            WHERE s.parent_station = %s
            ORDER BY t.route_id
            """,
            (parent_stop_id,),
        )
        return [r["route_id"] for r in rows]

    def get_child_stop_ids(self, parent_stop_id: str):
        index = self.__dict__.get("_pattern_index")
        if index is not None:
            return (
                [f"{parent_stop_id}N", f"{parent_stop_id}S"]
                if parent_stop_id in index.stops else []
            )
        rows = self._query(
            "SELECT stop_id FROM stops WHERE parent_station = %s",
            (parent_stop_id,),
        )
        return [r["stop_id"] for r in rows]

    def get_stop_names(self, stop_ids: list[str]):
        if not stop_ids:
            return {}
        rows = self._query(
            "SELECT stop_id, stop_name FROM stops WHERE stop_id = ANY(%s)",
            (stop_ids,),
        )
        return {r["stop_id"]: r["stop_name"] for r in rows}

    def get_stop_locations(self, stop_ids: list[str]):
        if not stop_ids:
            return {}

        index = self.__dict__.get("_pattern_index")
        if index is not None:
            return index.stop_locations(stop_ids)

        candidate_ids = set()
        for stop_id in stop_ids:
            if not stop_id:
                continue
            candidate_ids.add(stop_id)
            candidate_ids.add(stop_id.rstrip("NS"))

        rows = self._query(
            """
            SELECT stop_id, stop_name, stop_lat, stop_lon, parent_station
            FROM stops
            WHERE stop_id = ANY(%s)
            """,
            (list(candidate_ids),),
        )
        return {
            r["stop_id"]: {
                "stop_name": r["stop_name"],
                "lat": r["stop_lat"],
                "lng": r["stop_lon"],
                "parent_station": r.get("parent_station"),
            }
            for r in rows
        }

    def get_trip_stop_context(self, trip_ids: list[str]):
        if not trip_ids:
            return {}

        if self.__dict__.get("_pattern_index") is not None:
            return {}
        rows = self._query(
            """
            SELECT
                st.trip_id,
                st.stop_id,
                st.stop_sequence,
                s.stop_name,
                s.stop_lat,
                s.stop_lon,
                s.parent_station
            FROM stop_times st
            JOIN stops s ON s.stop_id = st.stop_id
            WHERE st.trip_id = ANY(%s)
            ORDER BY st.trip_id, st.stop_sequence
            """,
            (list({trip_id for trip_id in trip_ids if trip_id}),),
        )

        context: dict[str, list[dict]] = {}
        for row in rows:
            context.setdefault(row["trip_id"], []).append({
                "stop_id": row["stop_id"],
                "stop_sequence": row["stop_sequence"],
                "stop_name": row["stop_name"],
                "lat": row["stop_lat"],
                "lng": row["stop_lon"],
                "parent_station": row.get("parent_station"),
            })
        return context

    def get_subway_stops_with_routes(self, route_id_whitelist: set[str] | None = None):
        index = self.__dict__.get("_pattern_index")
        if index is not None:
            return index.stops_for_routes(route_id_whitelist)
        parent_rows = self._query(
            "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops WHERE location_type = '1'"
        )
        if not parent_rows:
            return []

        parent_ids = [r["stop_id"] for r in parent_rows]
        route_rows = self._query(
            """
            SELECT s.parent_station AS parent_id, t.route_id
            FROM stops s
            JOIN stop_times st ON st.stop_id = s.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            WHERE s.parent_station = ANY(%s)
            GROUP BY s.parent_station, t.route_id
            """,
            (parent_ids,),
        )
        routes_by_parent: dict[str, set[str]] = {}
        for row in route_rows:
            rid = row["route_id"]
            if route_id_whitelist is not None and rid not in route_id_whitelist:
                continue
            routes_by_parent.setdefault(row["parent_id"], set()).add(rid)

        out = []
        for r in parent_rows:
            routes = routes_by_parent.get(r["stop_id"])
            if not routes:
                continue
            out.append({
                "stop_id": r["stop_id"],
                "stop_name": r["stop_name"],
                "stop_lat": r["stop_lat"],
                "stop_lon": r["stop_lon"],
                "route_ids": sorted(routes),
            })
        return out
