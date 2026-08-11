"""In-memory GTFS stop-pattern index (Fix B).

Loads the precomputed artifact (scripts/build_stop_patterns.py) once and resolves
the ordered stops between an origin and destination on a route WITHOUT any
database query. Replaces the remote-Postgres `_trip_stops_between` /
`_ids_for_name` path on the trip hot path.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# This module lives at backend/app/utils/, so parent.parent == backend/app.
_APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ARTIFACT = _APP_DIR / "data" / "stop_patterns.json"

# MTA GTFS and the routing provider abbreviate station names inconsistently
# ("Church Av" vs "Church Avenue", case, punctuation). Normalize both sides
# through this map so the in-memory name lookup resolves.
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


def _parent_stop_id(stop_id: str) -> str:
    """Platform stop id -> parent station id (e.g. 'Q05N' -> 'Q05')."""
    return (stop_id or "").rstrip("NS")


def normalize_station_name(value) -> str:
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


def _coord(coords):
    if not isinstance(coords, dict):
        return None
    lat = coords.get("latitude", coords.get("lat"))
    lon = coords.get("longitude", coords.get("lng", coords.get("lon")))
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return (float(lat), float(lon))
    return None


class StopPatternIndex:
    def __init__(self, artifact: dict):
        self.stops: dict[str, dict] = artifact.get("stops", {})
        self.patterns: list[dict] = artifact.get("patterns", [])

        tmp: dict[str, set] = {}
        for sid, info in self.stops.items():
            raw = info.get("name", "")
            key = normalize_station_name(raw)
            if key:
                tmp.setdefault(key, set()).add(sid)
            # GTFS uses compound names ("34 St-Herald Sq", "Grand Central-42 St").
            # Google Routes often sends just one component ("Herald Sq", "Grand
            # Central"). Index each dash-separated component so partial lookups
            # resolve without a second pass.
            for part in raw.split("-"):
                part_key = normalize_station_name(part)
                if part_key and part_key != key:
                    tmp.setdefault(part_key, set()).add(sid)
        self.name_index: dict[str, frozenset] = {k: frozenset(v) for k, v in tmp.items()}

        # Index patterns by BOTH route_id and route_short_name; pre-build a
        # {stop_id: position} map per pattern for O(1) lookups.
        self.route_patterns: dict[str, list] = {}
        routes_by_stop: dict[str, set[str]] = {}
        for p in self.patterns:
            aug = {**p, "pos": {sid: i for i, sid in enumerate(p["stop_ids"])}}
            public_route = str(
                p.get("route_short_name") or p.get("route_id") or ""
            ).upper()
            if public_route:
                for stop_id in p.get("stop_ids") or []:
                    routes_by_stop.setdefault(str(stop_id), set()).add(public_route)
            for key in {p.get("route_id"), p.get("route_short_name")}:
                if key:
                    self.route_patterns.setdefault(str(key), []).append(aug)
        self.routes_by_stop = {
            stop_id: frozenset(routes)
            for stop_id, routes in routes_by_stop.items()
        }
        self._routes_by_parent_stop = {
            stop_id: sorted(routes)
            for stop_id, routes in routes_by_stop.items()
        }
        self._all_parent_stops = [
            {
                "stop_id": stop_id,
                "stop_name": info.get("name"),
                "stop_lat": info.get("lat"),
                "stop_lon": info.get("lon"),
            }
            for stop_id, info in sorted(self.stops.items())
        ]

    @classmethod
    def load(cls, path=None) -> "StopPatternIndex":
        path = Path(path) if path else DEFAULT_ARTIFACT
        return cls(json.loads(Path(path).read_text()))

    def ids_for_name(self, name: str) -> frozenset:
        return self.name_index.get(normalize_station_name(name), frozenset())

    def identity_for_stop(self, stop_id) -> dict:
        if not isinstance(stop_id, str) or not stop_id:
            return {
                "parent_station": None,
                "station_complex_id": None,
                "is_platform": False,
            }
        parent = _parent_stop_id(stop_id)
        info = self.stops.get(parent)
        if info is None:
            return {
                "parent_station": None,
                "station_complex_id": None,
                "is_platform": parent != stop_id,
            }
        return {
            "parent_station": parent,
            "station_complex_id": info.get("station_complex_id"),
            "is_platform": parent != stop_id,
        }

    def stops_for_routes(self, route_ids=None) -> list[dict]:
        """Return parent stops served by the requested routes from memory."""

        if route_ids is None:
            routes_by_stop = self.routes_by_stop
            return self._stop_rows(routes_by_stop)
        requested = (
            {str(route_id).strip().upper() for route_id in route_ids}
            if route_ids is not None
            else set()
        )
        routes_by_stop: dict[str, set[str]] = {}
        for route_id in sorted(requested):
            for pattern in self.route_patterns.get(route_id, []):
                public_route = str(
                    pattern.get("route_short_name") or pattern.get("route_id") or route_id
                ).upper()
                for stop_id in pattern.get("stop_ids") or []:
                    routes_by_stop.setdefault(str(stop_id), set()).add(public_route)

        return self._stop_rows(routes_by_stop)

    def _stop_rows(self, routes_by_stop) -> list[dict]:
        """Project cached route ownership into the existing stop-row contract."""

        return [
            {
                "stop_id": stop_id,
                "stop_name": self.stops[stop_id]["name"],
                "stop_lat": self.stops[stop_id]["lat"],
                "stop_lon": self.stops[stop_id]["lon"],
                "route_ids": sorted(routes),
            }
            for stop_id, routes in sorted(routes_by_stop.items())
            if stop_id in self.stops
        ]

    def all_parent_stops(self) -> list[dict]:
        return self._all_parent_stops

    def route_ids_for_parent_stop(self, parent_stop_id: str) -> list[str]:
        return self._routes_by_parent_stop.get(str(parent_stop_id).strip(), [])

    def stop_locations(self, stop_ids) -> dict[str, dict]:
        if not stop_ids:
            return {}
        locations: dict[str, dict] = {}
        for raw_stop_id in stop_ids:
            stop_id = str(raw_stop_id or "").strip()
            if not stop_id:
                continue
            parent = _parent_stop_id(stop_id)
            info = self.stops.get(parent)
            if info is None:
                continue
            parent_record = {
                "stop_name": info.get("name"),
                "lat": info.get("lat"),
                "lng": info.get("lon"),
                "parent_station": "",
            }
            locations.setdefault(parent, parent_record)
            if parent != stop_id:
                locations[stop_id] = {
                    "stop_name": info.get("name"),
                    "lat": info.get("lat"),
                    "lng": info.get("lon"),
                    "parent_station": parent,
                }
        return locations

    def routes_for_stop(self, stop_id: str) -> list[str]:
        key = str(stop_id or "").strip()
        if key not in self.stops and key[-1:] in {"N", "S"}:
            key = key[:-1]
        return sorted(self.routes_by_stop.get(key, ()))

    def stop_location(self, stop_id: str) -> tuple[str, dict] | None:
        key = str(stop_id or "").strip()
        if key not in self.stops and key[-1:] in {"N", "S"}:
            key = key[:-1]
        info = self.stops.get(key)
        return (key, info) if info else None

    def locations_for_stops(self, stop_ids) -> dict[str, dict]:
        locations = {}
        for raw_stop_id in stop_ids:
            resolved = self.stop_location(raw_stop_id)
            if resolved is None:
                continue
            parent_id, info = resolved
            record = {
                "stop_name": info["name"], "lat": info["lat"],
                "lng": info["lon"], "parent_station": parent_id,
            }
            locations[str(raw_stop_id)] = record
            locations.setdefault(parent_id, record)
        return locations

    def resolve_route_segment(
        self,
        route_id,
        origin,
        destination,
        origin_coords=None,
        destination_coords=None,
    ) -> dict | None:
        """Resolve route-specific endpoint IDs and direction without I/O."""

        origin_ids = self.ids_for_name(origin)
        destination_ids = self.ids_for_name(destination)
        origin_coord = _coord(origin_coords)
        destination_coord = _coord(destination_coords)
        best = None
        for pattern in self.route_patterns.get(str(route_id).strip().upper(), []):
            positions = pattern["pos"]
            origin_position = self._pick_pos(positions, origin_ids, origin_coord)
            destination_position = self._pick_pos(
                positions, destination_ids, destination_coord
            )
            if (
                origin_position is None
                or destination_position is None
                or origin_position >= destination_position
            ):
                continue
            origin_stop_id = pattern["stop_ids"][origin_position]
            destination_stop_id = pattern["stop_ids"][destination_position]
            score = (
                self._proximity(
                    origin_stop_id,
                    destination_stop_id,
                    origin_coord,
                    destination_coord,
                ),
                destination_position - origin_position,
                -pattern.get("trip_count", 0),
            )
            if best is None or score < best[0]:
                best = (
                    score,
                    origin_stop_id,
                    destination_stop_id,
                    pattern.get("direction_id"),
                )
        if best is None:
            return None
        return {
            "origin_stop_id": best[1],
            "destination_stop_id": best[2],
            "direction_id": best[3],
        }

    def get_intermediate_stops_with_coords(
        self, route_id, origin, dest, origin_coords=None, dest_coords=None
    ):
        """Return (rows, meta). rows = ordered [{name, lat, lng}] from origin..dest
        inclusive; meta = metrics dict. Never raises; returns ([], meta) on a miss
        (no candidate patterns, or origin/dest not both present in valid order)."""
        t0 = time.monotonic()
        meta = {
            "hit": False, "signature": None, "stop_count": 0,
            "patterns_considered": 0, "duration_ms": 0.0, "db_queries": 0,
        }

        def _finish(rows):
            meta["duration_ms"] = round((time.monotonic() - t0) * 1000, 3)
            return rows, meta

        origin_ids = self.ids_for_name(origin)
        dest_ids = self.ids_for_name(dest)
        candidates = self.route_patterns.get(str(route_id), [])
        meta["patterns_considered"] = len(candidates)
        if not origin_ids or not dest_ids or not candidates:
            return _finish([])

        oc = _coord(origin_coords)
        dc = _coord(dest_coords)
        best = None  # (score, pattern, oi, di)
        for p in candidates:
            pos = p["pos"]
            # Resolve each endpoint to the position NEAREST its Google coord, not
            # the earliest. Ambiguous names (e.g. "7 Av" exists 4x in NYC, and a
            # northbound B passes Brooklyn 7 Av before Manhattan 7 Av) otherwise
            # truncate the span to the wrong occurrence, dropping every stop past
            # it to unlabeled dots.
            oi = self._pick_pos(pos, origin_ids, oc)
            di = self._pick_pos(pos, dest_ids, dc)
            if oi is None or di is None or oi >= di:
                continue
            # Score: coord proximity to Google's leg endpoints first (most
            # reliable for picking the right express/local branch), then shortest
            # stop span, then pattern frequency as the tie-breaker.
            prox = self._proximity(p["stop_ids"][oi], p["stop_ids"][di], oc, dc)
            score = (prox, di - oi, -p.get("trip_count", 0))
            if best is None or score < best[0]:
                best = (score, p, oi, di)
        if best is None:
            return _finish([])

        _score, p, oi, di = best
        rows = []
        for sid in p["stop_ids"][oi:di + 1]:
            info = self.stops.get(sid)
            if info:
                rows.append({"name": info["name"], "lat": info["lat"], "lng": info["lon"]})
        meta.update(hit=True, signature=p.get("signature"), stop_count=len(rows))
        return _finish(rows)

    def get_intermediate_stops(self, route_id, origin, dest):
        rows, _meta = self.get_intermediate_stops_with_coords(route_id, origin, dest)
        return [r["name"] for r in rows]

    def _pick_pos(self, pos: dict, id_set, coord) -> int | None:
        """Position in the pattern of the id_set stop nearest `coord`. Falls back
        to the earliest occurrence when no coord is given. Disambiguates names
        that repeat across the system (and even within one pattern)."""
        candidates = [(sid, pos[sid]) for sid in id_set if sid in pos]
        if not candidates:
            return None
        if coord is None:
            return min(p for _sid, p in candidates)
        lat, lon = coord

        def _dist(sid):
            info = self.stops.get(sid)
            if not info:
                return float("inf")
            return (info["lat"] - lat) ** 2 + (info["lon"] - lon) ** 2

        return min(candidates, key=lambda c: _dist(c[0]))[1]

    def _proximity(self, origin_sid, dest_sid, oc, dc) -> float:
        if oc is None and dc is None:
            return 0.0
        pen = 0.0
        if oc is not None:
            info = self.stops.get(origin_sid)
            if info:
                pen += (info["lat"] - oc[0]) ** 2 + (info["lon"] - oc[1]) ** 2
        if dc is not None:
            info = self.stops.get(dest_sid)
            if info:
                pen += (info["lat"] - dc[0]) ** 2 + (info["lon"] - dc[1]) ** 2
        return pen
