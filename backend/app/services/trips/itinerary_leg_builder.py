"""Build canonical legs from provider-normalized route steps.

This module owns step-level timing, walking, and semantic-transfer
normalization. It has no provider or network dependencies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.utils import geo

TIMEZONE_NAME = "America/New_York"
_ET = ZoneInfo(TIMEZONE_NAME)
_WALK_SPEED_MPS = 1.4
TRANSIT_MODES = frozenset(
    {"SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL", "TRAM"}
)


def build_legs(steps: list[dict], *, data_basis: str) -> list[dict]:
    legs: list[dict] = []
    prev_arrival_dt: datetime | None = None
    prev_mode: str | None = None
    prev_semantic_transfer = False

    for step in steps:
        mode = str(step.get("type") or "").strip().upper() or "UNKNOWN"
        if mode == "WALK" and step.get("semantic_transfer_fragment") is True:
            continue
        dep_iso = step.get("departure_time_iso")
        arr_iso = step.get("arrival_time_iso")
        dep_dt = _parse_iso(dep_iso)
        arr_dt = _parse_iso(arr_iso)

        walk_seconds = 0
        wait_seconds = 0
        ride_seconds = 0
        transfer_seconds = 0
        semantic: dict[str, Any] | None = None

        if mode == "WALK":
            raw_semantic = step.get("transfer_semantics")
            semantic = raw_semantic if isinstance(raw_semantic, dict) else None
            if isinstance(semantic, dict):
                walk_seconds = int(semantic.get("street_walking_seconds") or 0)
                transfer_seconds = int(
                    semantic.get("in_station_transfer_seconds") or 0
                )
            else:
                walk_seconds = _walk_seconds_for_step(step, dep_dt, arr_dt)
        elif mode in TRANSIT_MODES:
            if dep_dt is not None and arr_dt is not None:
                ride_seconds = _seconds_between(dep_dt, arr_dt)
            if (
                prev_mode in TRANSIT_MODES
                and prev_arrival_dt is not None
                and dep_dt is not None
            ):
                transfer_seconds = _seconds_between(prev_arrival_dt, dep_dt)
            elif (
                prev_mode is not None
                and prev_mode not in TRANSIT_MODES
                and prev_arrival_dt is not None
                and dep_dt is not None
                and not prev_semantic_transfer
            ):
                wait_seconds = _seconds_between(prev_arrival_dt, dep_dt)

        board = step.get("departure_stop")
        alight = step.get("arrival_stop")
        service_id = (
            str(step.get("route_id") or step.get("train_line") or "").strip() or None
        )
        if mode == "WALK":
            service_id = None

        stops = _canonical_stops_for_step(step)
        raw_stop_count = step.get("stop_count")
        if isinstance(raw_stop_count, (int, float)) and not isinstance(
            raw_stop_count, bool
        ):
            stop_count = max(0, int(round(raw_stop_count)))
        else:
            stop_count = None

        leg = {
            "mode": mode,
            "service_id": service_id,
            "board": board,
            "alight": alight,
            "stop_count": stop_count,
            "stops": stops,
            "departure_at": _iso_or_none(dep_iso, dep_dt),
            "arrival_at": _iso_or_none(arr_iso, arr_dt),
            "walk_seconds": int(walk_seconds),
            "street_walking_seconds": int(walk_seconds),
            "in_station_transfer_seconds": (
                int(semantic.get("in_station_transfer_seconds") or 0)
                if isinstance(semantic, dict)
                else 0
            ),
            "wait_seconds": int(wait_seconds),
            "ride_seconds": int(ride_seconds),
            "transfer_seconds": int(transfer_seconds),
            "transfer_kind": (
                semantic.get("kind") if isinstance(semantic, dict) else None
            ),
            "transfer_semantics": (
                dict(semantic) if isinstance(semantic, dict) else None
            ),
            "accessibility": (
                semantic.get("accessibility") if isinstance(semantic, dict) else None
            ),
            "geometry": step.get("polyline"),
            "service_data_basis": data_basis,
        }
        legs.append(leg)

        if arr_dt is not None:
            prev_arrival_dt = arr_dt
        elif mode == "WALK":
            prev_arrival_dt = None
        prev_mode = mode
        prev_semantic_transfer = isinstance(semantic, dict) and mode == "WALK"

    return legs


def _canonical_stops_for_step(step: dict) -> list[dict]:
    """Preserve an enriched leg's ordered stops without fabricating stations."""

    located = step.get("intermediate_stop_locations")
    if isinstance(located, list) and located:
        stops: list[dict] = []
        for value in located:
            if not isinstance(value, dict):
                continue
            name = str(value.get("name") or "").strip()
            if not name:
                continue
            stop: dict[str, Any] = {"name": name}
            lat, lng = _lat_lon(value)
            if lat is not None and lng is not None:
                stop["lat"] = lat
                stop["lng"] = lng
            stops.append(stop)
        if stops:
            return stops

    names = step.get("intermediate_stops")
    if not isinstance(names, list):
        return []
    return [
        {"name": str(value).strip()}
        for value in names
        if isinstance(value, str) and value.strip()
    ]


def _walk_seconds_for_step(
    step: dict,
    dep_dt: datetime | None,
    arr_dt: datetime | None,
) -> int:
    if dep_dt is not None and arr_dt is not None:
        return _seconds_between(dep_dt, arr_dt)

    start = step.get("start_point") or {}
    end = step.get("end_point") or {}
    lat1, lon1 = _lat_lon(start)
    lat2, lon2 = _lat_lon(end)
    if None in (lat1, lon1, lat2, lon2):
        return 0
    meters = geo.distance_meters(float(lat1), float(lon1), float(lat2), float(lon2))
    return max(0, int(round(meters / _WALK_SPEED_MPS)))


def _lat_lon(point: dict) -> tuple[float | None, float | None]:
    if not isinstance(point, dict):
        return None, None
    lat = point.get("latitude")
    if lat is None:
        lat = point.get("lat")
    lon = point.get("longitude")
    if lon is None:
        lon = point.get("lng")
    if lon is None:
        lon = point.get("lon")
    try:
        return (
            float(lat) if lat is not None else None,
            float(lon) if lon is not None else None,
        )
    except (TypeError, ValueError):
        return None, None


def _parse_iso(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_ET)
        return dt
    except (TypeError, ValueError):
        return None


def _seconds_between(start: datetime, end: datetime) -> int:
    return max(0, int(round((end - start).total_seconds())))


def _iso_or_none(raw: Any, parsed: datetime | None) -> str | None:
    if raw is not None and str(raw).strip():
        return str(raw)
    if parsed is not None:
        return parsed.isoformat()
    return None
