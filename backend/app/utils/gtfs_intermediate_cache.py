"""Bounded memoization for GTFS route-segment lookups."""

from __future__ import annotations

from functools import lru_cache

from app.utils.stop_patterns import normalize_station_name

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
