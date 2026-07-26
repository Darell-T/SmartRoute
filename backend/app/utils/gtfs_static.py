import os
import threading
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

from app.utils.stop_patterns import normalize_station_name

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


# MTA GTFS and the routing provider abbreviate station names but not always
# identically ("Church Av" vs "Church Avenue", case, punctuation). The
# name -> stop_id lookup normalizes through this map (applied to both sides) so
# the fast primary match resolves instead of falling through to the slow
# coordinate snap.
_STATION_TOKEN_MAP = {
    "av": "avenue",
    "ave": "avenue",
    "blvd": "boulevard",
    "ctr": "center",
    "ft": "fort",
    "hts": "heights",
    "hwy": "highway",
    "pkwy": "parkway",
    "pl": "place",
    "plz": "plaza",
    "rd": "road",
    "sq": "square",
    "st": "street",
}


def _normalize_station_name(value) -> str:
    if not isinstance(value, str):
        return ""
    text = value.casefold()
    for ch in "&,-/.":
        text = text.replace(ch, " ")
    return " ".join(
        _STATION_TOKEN_MAP.get(token, token)
        for token in text.split()
        if token
    )


def init_pool():
    # Called from the FastAPI lifespan so an unreachable database surfaces
    # as a startup error instead of hanging at import time.
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


class GTFSStaticData:

    def load_scheduled_arrivals(self, path: str | Path | None = None) -> bool:
        """Load the optional preprocessed full-GTFS schedule once at startup."""

        from app.services.agent.tools.scheduled_arrivals import ScheduledArrivalIndex

        candidate = Path(
            path
            or os.getenv("GTFS_SCHEDULE_ARTIFACT", "")
            or Path(__file__).resolve().parent.parent / "data" / "scheduled_arrivals.json"
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

    # ------------------------------------------------------------------
    # Simplified my over engineered support for concurrent users
    # ------------------------------------------------------------------

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
        """Lazy in-memory {normalized stop_name -> {stripped stop_id}} index.
        GTFS stops are static and small (~1500 rows), so loading them once and
        resolving names in memory removes two remote round-trips per enriched
        leg and tolerates Av/Avenue-style spelling differences between the
        routing provider and GTFS."""
        idx = self.__dict__.get("_name_index_cache")
        if idx is None:
            idx = {}
            try:
                rows = self._query("SELECT stop_id, stop_name FROM stops")
            except Exception:
                rows = []
            for r in rows:
                key = _normalize_station_name(r.get("stop_name"))
                if key:
                    idx.setdefault(key, set()).add(r["stop_id"].rstrip("NS"))
            # Only memoize a populated index; a transient load failure should be
            # retried next call, not cached as permanently empty.
            if idx:
                self.__dict__["_name_index_cache"] = idx
        return idx

    def _ids_for_name(self, name: str) -> set:
        idx = self._name_index()
        if idx:
            return set(idx.get(_normalize_station_name(name), set()))
        # Index could not load (DB hiccup): fall back to a direct exact match.
        rows = self._query("SELECT stop_id FROM stops WHERE stop_name = %s", (name,))
        return {r["stop_id"].rstrip("NS") for r in rows}

    def _route_stop_ids_near(self, route_id: str, coords) -> set:
        """Stripped id of the single stop on route_id nearest to a {latitude,
        longitude} point. Fallback for when a provider station name does not
        resolve to a stop on the route's own trips -- the routing provider
        sometimes names a leg's endpoint as the transfer station, which is not
        a stop on this line."""
        if not coords:
            return set()
        lat = coords.get("latitude")
        lng = coords.get("longitude")
        if lat is None or lng is None:
            return set()
        # Snap against the stops of ONE representative trip on the route -- a
        # bounded ~30-row query. A DISTINCT join over every trip of the route
        # is far heavier and, on the synchronous trip path, risks tripping the
        # database statement timeout and failing the whole request.
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
        """Ordered stop rows between any origin id and any dest id on the first
        trip of route_id that traverses them in order. Each row:
        {stop_id, stop_name, stop_lat, stop_lon}. Empty if no trip qualifies."""
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
        """Ordered stop rows between origin and dest (matched by station name)
        on the first qualifying trip of route_id. Empty if no trip qualifies."""
        return self._trip_stops_between(
            route_id, self._ids_for_name(origin), self._ids_for_name(dest)
        )

    def _db_fallback_enabled(self) -> bool:
        """The remote-DB enrichment path is OFF by default (Fix B). It only runs
        as an explicit debug fallback: GTFS_DB_FALLBACK=1 in the environment, or
        an instance opting in via _allow_db_fallback (used by the DB-path tests)."""
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
        fallback (see _db_fallback_enabled). Results are memoized per instance."""
        cache = self.__dict__.setdefault("_intermediate_stops_cache", {})
        cache_key = (route_id, origin, dest)
        if cache_key in cache:
            self.__dict__["_cache_hits"] = self.__dict__.get("_cache_hits", 0) + 1
            return cache[cache_key]
        self.__dict__["_cache_misses"] = self.__dict__.get("_cache_misses", 0) + 1

        idx = self.__dict__.get("_pattern_index")
        if idx is not None:
            rows, meta = idx.get_intermediate_stops_with_coords(
                route_id, origin, dest, origin_coords, dest_coords
            )
            counter = "_static_hits" if meta["hit"] else "_static_misses"
            self.__dict__[counter] = self.__dict__.get(counter, 0) + 1
            # A static MISS means a station name didn't resolve -> unlabeled
            # dots, worth surfacing. A hit is the normal path (silent).
            if not meta["hit"]:
                print(
                    f"[gtfs] static MISS route={route_id} origin={origin!r} dest={dest!r} "
                    f"norm_origin={normalize_station_name(origin)!r} "
                    f"norm_dest={normalize_station_name(dest)!r} "
                    f"patterns={meta['patterns_considered']}"
                )
            cache[cache_key] = rows
            return rows

        # No static index loaded. Do NOT touch the remote DB on the hot path
        # unless the debug fallback is explicitly enabled.
        if not self._db_fallback_enabled():
            print(
                f"[gtfs] no static pattern index and DB fallback disabled; "
                f"returning empty for route={route_id} {origin!r}->{dest!r}"
            )
            cache[cache_key] = []
            return []
        rows = self._find_trip_stop_rows(route_id, origin, dest)
        if not rows and (origin_coords or dest_coords):
            origin_ids = self._route_stop_ids_near(route_id, origin_coords) or self._ids_for_name(origin)
            dest_ids = self._route_stop_ids_near(route_id, dest_coords) or self._ids_for_name(dest)
            rows = self._trip_stops_between(route_id, origin_ids, dest_ids)
        result = [
            {"name": r["stop_name"], "lat": r["stop_lat"], "lng": r["stop_lon"]}
            for r in rows
            if r.get("stop_lat") is not None and r.get("stop_lon") is not None
        ]
        cache[cache_key] = result
        return result
    
    def get_all_parent_stops(self):
        # location_type column is TEXT (raw GTFS value, "1" for parent stations).
        # Column is stop_lon (REAL) per the migration — not stop_long.
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
        if index is not None and parent_stop_id in index.stops:
            return [f"{parent_stop_id}N", f"{parent_stop_id}S"]
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

    # location_type column is TEXT (the migration writes the raw GTFS string
    # value, which is "1" for parent stations). Two queries: parents first,
    # then a single grouped pull of route_ids per parent so we don't fall
    # into N+1.
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
   

