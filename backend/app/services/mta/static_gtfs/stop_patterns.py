"""In-memory GTFS stop-pattern index with no database lookups."""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.services.geography import distance_meters

_APP_DIR = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT = _APP_DIR / "data" / "stop_patterns.json"

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
    if isinstance(coords, (tuple, list)) and len(coords) == 2:
        coords = {"lat": coords[0], "lon": coords[1]}
    if not isinstance(coords, dict):
        return None
    lat = coords.get("latitude", coords.get("lat"))
    lon = coords.get("longitude", coords.get("lng", coords.get("lon")))
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return (float(lat), float(lon))
    return None


def _coord_record(info: dict | None) -> dict:
    info = info or {}
    return {"latitude": info.get("lat"), "longitude": info.get("lon")}


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
            for part in raw.split("-"):
                part_key = normalize_station_name(part)
                if part_key and part_key != key:
                    tmp.setdefault(part_key, set()).add(sid)
        self.name_index: dict[str, frozenset] = {k: frozenset(v) for k, v in tmp.items()}

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
    def load(cls, path=None) -> StopPatternIndex:
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

    def _stop_distance(self, stop_id, destination_coord, fallback=None):
        info = self.stops.get(_parent_stop_id(str(stop_id)))
        point = (_coord(info) if info else None) or fallback
        return distance_meters(*point, *destination_coord) if point else None

    def _members_by_complex(self) -> dict[str, set[str]]:
        members: dict[str, set[str]] = {}
        for stop_id, info in self.stops.items():
            complex_id = info.get("station_complex_id")
            if complex_id:
                members.setdefault(str(complex_id), set()).add(str(stop_id))
        return members

    def _best_destinations_after_stop(
        self,
        *,
        continuation,
        continuation_stops,
        continuation_transfer_pos,
        stop_id,
        seed,
        seed_route,
        boarding_pos,
        seed_alighting_id,
        transfer_id,
        complex_id,
        seed_distance,
        destination_coord,
        min_egress_improvement_meters,
        continuation_route,
        best,
    ):
        for destination_pos in range(
            continuation_transfer_pos + 1, len(continuation_stops)
        ):
            destination_stop_id = continuation_stops[destination_pos]
            continuation_distance = self._stop_distance(
                destination_stop_id, destination_coord
            )
            if continuation_distance is None:
                continue
            candidate = self._score_continuation_destination(
                seed_route=seed_route,
                seed=seed,
                boarding_pos=boarding_pos,
                seed_alighting_id=seed_alighting_id,
                transfer_id=transfer_id,
                complex_id=str(complex_id),
                stop_id=stop_id,
                destination_stop_id=destination_stop_id,
                seed_distance=seed_distance,
                continuation_distance=continuation_distance,
                min_egress_improvement_meters=min_egress_improvement_meters,
                continuation=continuation,
                continuation_route=continuation_route,
            )
            if candidate is not None and (best is None or candidate[0] < best[0]):
                best = candidate
        return best

    def _best_on_continuation(
        self,
        *,
        continuation,
        seed,
        seed_route,
        excluded,
        boarding_pos,
        seed_alighting_id,
        transfer_id,
        complex_id,
        members,
        seed_distance,
        destination_coord,
        min_egress_improvement_meters,
        best,
    ):
        continuation_route = str(
            continuation.get("route_short_name") or continuation.get("route_id") or ""
        ).strip().upper()
        if not continuation_route or continuation_route in {seed_route, *excluded}:
            return best
        continuation_stops = continuation.get("stop_ids") or []
        for continuation_transfer_pos, stop_id in enumerate(continuation_stops):
            if _parent_stop_id(str(stop_id)) not in members:
                continue
            best = self._best_destinations_after_stop(
                continuation=continuation,
                continuation_stops=continuation_stops,
                continuation_transfer_pos=continuation_transfer_pos,
                stop_id=stop_id,
                seed=seed,
                seed_route=seed_route,
                boarding_pos=boarding_pos,
                seed_alighting_id=seed_alighting_id,
                transfer_id=transfer_id,
                complex_id=complex_id,
                seed_distance=seed_distance,
                destination_coord=destination_coord,
                min_egress_improvement_meters=min_egress_improvement_meters,
                continuation_route=continuation_route,
                best=best,
            )
        return best

    def _best_on_seed(
        self,
        *,
        seed,
        seed_route,
        excluded,
        boarding_ids,
        alighting_ids,
        boarding_coord,
        alighting_coord,
        destination_coord,
        min_egress_improvement_meters,
        complex_members,
        best,
    ):
        positions = seed["pos"]
        boarding_pos = self._pick_pos(positions, boarding_ids, boarding_coord)
        alighting_pos = self._pick_pos(positions, alighting_ids, alighting_coord)
        if boarding_pos is None or alighting_pos is None or boarding_pos >= alighting_pos:
            return best
        seed_alighting_id = seed["stop_ids"][alighting_pos]
        seed_distance = (
            distance_meters(*alighting_coord, *destination_coord)
            if alighting_coord
            else self._stop_distance(seed_alighting_id, destination_coord)
        )
        if seed_distance is None:
            return best
        for transfer_pos in range(boarding_pos + 1, alighting_pos):
            transfer_id = seed["stop_ids"][transfer_pos]
            complex_id = self.identity_for_stop(transfer_id).get("station_complex_id")
            if not complex_id or complex_id not in complex_members:
                continue
            members = complex_members[complex_id]
            for continuation in self.patterns:
                best = self._best_on_continuation(
                    continuation=continuation,
                    seed=seed,
                    seed_route=seed_route,
                    excluded=excluded,
                    boarding_pos=boarding_pos,
                    seed_alighting_id=seed_alighting_id,
                    transfer_id=transfer_id,
                    complex_id=complex_id,
                    members=members,
                    seed_distance=seed_distance,
                    destination_coord=destination_coord,
                    min_egress_improvement_meters=min_egress_improvement_meters,
                    best=best,
                )
        return best

    def _score_continuation_destination(
        self,
        *,
        seed_route: str,
        seed: dict,
        boarding_pos: int,
        seed_alighting_id: str,
        transfer_id: str,
        complex_id: str,
        stop_id: str,
        destination_stop_id: str,
        seed_distance: float,
        continuation_distance: float,
        min_egress_improvement_meters: float,
        continuation: dict,
        continuation_route: str,
    ) -> tuple[tuple, dict] | None:
        improvement = seed_distance - continuation_distance
        if improvement < float(min_egress_improvement_meters):
            return None
        dest_info = self.stops.get(_parent_stop_id(str(destination_stop_id))) or {}
        transfer_info = self.stops.get(_parent_stop_id(str(transfer_id))) or {}
        continuation_info = (
            self.stops.get(str(stop_id))
            or self.stops.get(_parent_stop_id(str(stop_id)))
            or {}
        )
        return (
            (
                continuation_distance,
                -improvement,
                -int(continuation.get("trip_count") or 0),
            ),
            {
                "seed_route_id": seed_route,
                "seed_boarding_stop_id": str(seed["stop_ids"][boarding_pos]),
                "seed_alighting_stop_id": str(seed_alighting_id),
                "transfer_stop_id": str(transfer_id),
                "transfer_stop_name": transfer_info.get("name"),
                "transfer_stop_coords": _coord_record(transfer_info),
                "transfer_station_complex_id": str(complex_id),
                "continuation_transfer_stop_id": str(stop_id),
                "continuation_transfer_stop_coords": _coord_record(continuation_info),
                "continuation_route_id": continuation_route,
                "destination_stop_id": str(destination_stop_id),
                "destination_stop_name": dest_info.get("name"),
                "destination_stop_coords": _coord_record(dest_info),
                "seed_alighting_distance_meters": round(seed_distance, 1),
                "destination_distance_meters": round(continuation_distance, 1),
                "egress_distance_improvement_meters": round(improvement, 1),
            },
        )

    def suggest_one_transfer(
        self,
        route_id,
        boarding_stop,
        alighting_stop,
        destination_coords,
        *,
        boarding_coords=None,
        alighting_coords=None,
        excluded_route_ids=None,
        excluded_modes=None,
        allowed_modes=None,
        min_egress_improvement_meters=250.0,
    ) -> dict | None:
        """Return one validated continuation through a shared station complex."""

        if "SUBWAY" in {str(mode).upper() for mode in (excluded_modes or [])}:
            return None
        allowed = {str(mode).upper() for mode in (allowed_modes or [])}
        if allowed_modes is not None and "SUBWAY" not in allowed:
            return None
        seed_route = str(route_id or "").strip().upper()
        excluded = {str(value).strip().upper() for value in (excluded_route_ids or [])}
        if not seed_route or seed_route in excluded:
            return None
        destination_coord = _coord(destination_coords)
        if destination_coord is None:
            return None
        boarding_ids = self.ids_for_name(str(boarding_stop or ""))
        alighting_ids = self.ids_for_name(str(alighting_stop or ""))
        if not boarding_ids or not alighting_ids:
            return None
        boarding_coord = _coord(boarding_coords)
        alighting_coord = _coord(alighting_coords)
        complex_members = self._members_by_complex()
        best = None
        for seed in self.route_patterns.get(seed_route, []):
            best = self._best_on_seed(
                seed=seed,
                seed_route=seed_route,
                excluded=excluded,
                boarding_ids=boarding_ids,
                alighting_ids=alighting_ids,
                boarding_coord=boarding_coord,
                alighting_coord=alighting_coord,
                destination_coord=destination_coord,
                min_egress_improvement_meters=min_egress_improvement_meters,
                complex_members=complex_members,
                best=best,
            )
        return best[1] if best else None

    def get_intermediate_stops_with_coords(
        self, route_id, origin, dest, origin_coords=None, dest_coords=None
    ):
        """Return ordered stops plus bounded lookup metrics; misses never raise."""
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
        best = None
        for p in candidates:
            pos = p["pos"]
            oi = self._pick_pos(pos, origin_ids, oc)
            di = self._pick_pos(pos, dest_ids, dc)
            if oi is None or di is None or oi >= di:
                continue
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
