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
        for p in self.patterns:
            aug = {**p, "pos": {sid: i for i, sid in enumerate(p["stop_ids"])}}
            for key in {p.get("route_id"), p.get("route_short_name")}:
                if key:
                    self.route_patterns.setdefault(str(key), []).append(aug)

    @classmethod
    def load(cls, path=None) -> "StopPatternIndex":
        path = Path(path) if path else DEFAULT_ARTIFACT
        return cls(json.loads(Path(path).read_text()))

    def ids_for_name(self, name: str) -> frozenset:
        return self.name_index.get(normalize_station_name(name), frozenset())

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
