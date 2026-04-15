import os
import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, os.getenv("DATABASE_URL"))



class GTFSStaticData:

    # ------------------------------------------------------------------
    # Simplified my over engineered support for concurrent users
    # ------------------------------------------------------------------

    def _query(self, sql, params=None):
        conn = _pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql,params)
                return cur.fetchall()
        finally:
            _pool.putconn(conn)


    def get_intermediate_stops(self, route_id: str, origin: str, dest: str) -> list:
        origin_rows = self._query("SELECT stop_id FROM stops WHERE stop_name = %s", (origin,))
        origin_ids = {r["stop_id"].rstrip("NS") for r in origin_rows}

        #get destination stops
        dest_rows= self._query("SELECT stop_id FROM stops WHERE stop_name = %s", (dest,))
        dest_ids = {r["stop_id"].rstrip("NS") for r in dest_rows}

        if not origin_ids or not dest_ids:
            return []
        
        trip_rows = self._query("SELECT trip_id FROM trips WHERE route_id = %s", (route_id,))

        for trip in trip_rows:
            stops = self._query("SELECT stop_id FROM stop_times WHERE trip_id = %s ORDER BY stop_sequence", (trip["trip_id"],))
            stop_id_list = [r["stop_id"] for r in stops]

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
                name_rows = self._query(
                    "SELECT stop_id, stop_name FROM stops WHERE stop_id = ANY(%s)", (route_stop_ids,))
                name_map = {r["stop_id"]: r["stop_name"] for r in name_rows}
                return [name_map.get(sid, sid) for sid in route_stop_ids]

        return []
    
    def get_all_parent_stops(self):
        return self._query(
        "SELECT stop_id, stop_name, stop_lat, stop_long FROM stops WHERE location_type = %s",
        (1,)
    )

    #need to get the trains that serve the nearest stops
    def get_trains_for_feeds(self, nearest_stops: dict):
        routes = []
        for stop in nearest_stops:
            routes.extend(self._query(
                """
                SELECT DISTINCT t.route_id, s.stop_name
                FROM stops s
                JOIN stop_times st ON st.stop_id = s.stop_id
                JOIN trips t ON t.trip_id = st.trip_id
                WHERE s.parent_station = %s
                """, (stop["stop_id"],)
            ))
        
        return routes
       


