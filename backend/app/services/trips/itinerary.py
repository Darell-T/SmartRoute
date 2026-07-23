"""Canonical itinerary normalizer.

Turns already-parsed Google route steps into one immutable dict with
seconds-based totals. Pure functions only: no network, no LLM.

Google Routes remains the path engine; this module only normalizes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.utils import geo

TIMEZONE_NAME = "America/New_York"
_ET = ZoneInfo(TIMEZONE_NAME)

# Match app.utils.geo.walking_time_minutes default. Prefer seconds from
# meters / speed rather than round-trip through 0.1-minute rounding.
_WALK_SPEED_MPS = 1.4

_TRANSIT_MODES = frozenset({"SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL"})


def build_canonical_itinerary(
    steps: list[dict] | None,
    *,
    origin: Any,
    destination: Any,
    planning_mode: str = "leave_now",
    requested_departure: str | None = None,
    generated_at: str | None = None,
    data_basis: str = "mixed",
    reasons: list[str] | None = None,
    itinerary_id: str | None = None,
) -> dict:
    """Normalize parsed route steps into a canonical itinerary dict.

    Timing rules (binding):
    - Prefer absolute ISO deltas for leg lengths.
    - NEVER treat ``minutes_until_arrival`` / ``minutes_until_train_arrives``
      as leg duration (those are relative clocks from parse-time "now").
    - NEVER invent 1-minute transfer filler.
    - Whole-trip total: prefer Google ``route_total_minutes * 60`` when
      present on any step; else sum of measured leg components.
    - Walk without ISO: haversine / 1.4 m/s when endpoints exist; else 0
      (unknown) rather than inventing a magic 4-minute walk.
    """
    step_list = list(steps or [])
    legs = _build_legs(step_list, data_basis=data_basis)

    total_walk = sum(int(leg["walk_seconds"]) for leg in legs)
    total_wait = sum(int(leg["wait_seconds"]) for leg in legs)
    total_in_vehicle = sum(int(leg["ride_seconds"]) for leg in legs)
    total_transfer = sum(int(leg["transfer_seconds"]) for leg in legs)
    # Single OD has no waypoint dwell; multi-stop chain is a later task.
    total_dwell = 0

    # Prefer provider door-to-door total when available. Component sums may
    # not equal this (walks estimated, waits often unknown without walk ISO).
    route_total_minutes = _first_route_total_minutes(step_list)
    if route_total_minutes is not None:
        total_duration_seconds = max(0, int(round(route_total_minutes * 60)))
    else:
        total_duration_seconds = (
            total_walk + total_wait + total_in_vehicle + total_transfer + total_dwell
        )

    transit_count = sum(1 for leg in legs if leg["mode"] in _TRANSIT_MODES)
    transfer_count = max(0, transit_count - 1)

    departure_at, arrival_at = _trip_clocks(legs, step_list)

    return {
        "itinerary_id": itinerary_id or str(uuid4()),
        "origin": origin,
        "waypoints": [],
        "destination": destination,
        "timezone": TIMEZONE_NAME,
        "planning_mode": planning_mode,
        "requested_departure": requested_departure,
        "generated_at": generated_at,
        "data_basis": data_basis,
        # Freshness is the planning snapshot time when provided; not invented.
        "data_freshness": generated_at,
        "departure_at": departure_at,
        "arrival_at": arrival_at,
        "total_duration_seconds": total_duration_seconds,
        "total_walk_seconds": total_walk,
        "total_wait_seconds": total_wait,
        "total_in_vehicle_seconds": total_in_vehicle,
        "total_dwell_seconds": total_dwell,
        "transfer_count": transfer_count,
        "legs": legs,
        "structured_recommendation_reasons": list(reasons or []),
    }


def _build_legs(steps: list[dict], *, data_basis: str) -> list[dict]:
    legs: list[dict] = []
    prev_arrival_dt: datetime | None = None
    prev_mode: str | None = None

    for step in steps:
        mode = str(step.get("type") or "").strip().upper() or "UNKNOWN"
        dep_iso = step.get("departure_time_iso")
        arr_iso = step.get("arrival_time_iso")
        dep_dt = _parse_iso(dep_iso)
        arr_dt = _parse_iso(arr_iso)

        walk_seconds = 0
        wait_seconds = 0
        ride_seconds = 0
        transfer_seconds = 0

        if mode == "WALK":
            walk_seconds = _walk_seconds_for_step(step, dep_dt, arr_dt)
        elif mode in _TRANSIT_MODES:
            # Ride length from absolute ISO only — never minutes_until_*.
            if dep_dt is not None and arr_dt is not None:
                ride_seconds = _seconds_between(dep_dt, arr_dt)
            # Transfer: measurable ISO gap after previous transit (no filler).
            if (
                prev_mode in _TRANSIT_MODES
                and prev_arrival_dt is not None
                and dep_dt is not None
            ):
                transfer_seconds = _seconds_between(prev_arrival_dt, dep_dt)
            # Wait at board after a non-transit leg with known arrival ISO.
            elif (
                prev_mode is not None
                and prev_mode not in _TRANSIT_MODES
                and prev_arrival_dt is not None
                and dep_dt is not None
            ):
                wait_seconds = _seconds_between(prev_arrival_dt, dep_dt)

        board = step.get("departure_stop")
        alight = step.get("arrival_stop")
        service_id = (
            str(step.get("route_id") or step.get("train_line") or "").strip() or None
        )
        if mode == "WALK":
            service_id = None

        geometry = step.get("polyline")
        if geometry is None:
            geometry = None

        leg = {
            "mode": mode,
            "service_id": service_id,
            "board": board,
            "alight": alight,
            "departure_at": _iso_or_none(dep_iso, dep_dt),
            "arrival_at": _iso_or_none(arr_iso, arr_dt),
            "walk_seconds": int(walk_seconds),
            "wait_seconds": int(wait_seconds),
            "ride_seconds": int(ride_seconds),
            "transfer_seconds": int(transfer_seconds),
            "geometry": geometry,
            "service_data_basis": data_basis,
        }
        legs.append(leg)

        # Chain clocks for next wait/transfer measurement.
        if arr_dt is not None:
            prev_arrival_dt = arr_dt
        elif mode == "WALK":
            # Unknown walk end — do not invent a chain time.
            prev_arrival_dt = None
        prev_mode = mode

    return legs


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
        # Unknown rather than inventing magic 4 min / 240 s.
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


def _first_route_total_minutes(steps: list[dict]) -> float | None:
    for step in steps:
        value = step.get("route_total_minutes")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _trip_clocks(
    legs: list[dict],
    steps: list[dict],
) -> tuple[str | None, str | None]:
    """First/last absolute times from legs, falling back to raw step ISOs."""
    departure_at = None
    arrival_at = None
    for leg in legs:
        if departure_at is None and leg.get("departure_at"):
            departure_at = leg["departure_at"]
        if leg.get("arrival_at"):
            arrival_at = leg["arrival_at"]
        if leg.get("departure_at") and departure_at is None:
            departure_at = leg["departure_at"]
    if departure_at is None or arrival_at is None:
        for step in steps:
            if departure_at is None and step.get("departure_time_iso"):
                departure_at = step["departure_time_iso"]
            if step.get("arrival_time_iso"):
                arrival_at = step["arrival_time_iso"]
    return departure_at, arrival_at


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
