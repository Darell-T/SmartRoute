"""Canonical itinerary normalizer.

Turns already-parsed Google route steps into one immutable dict with
seconds-based totals. Pure functions only: no network, no LLM.

Google Routes remains the path engine; this module only normalizes.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.trips.itinerary_leg_builder import (
    TIMEZONE_NAME,
    TRANSIT_MODES,
    build_legs,
)

# Server-owned multi-stop pickup buffer when the rider does not specify.
DEFAULT_DWELL_MINUTES = 25


def build_canonical_itinerary(
    steps: list[dict] | None,
    *,
    origin: Any,
    destination: Any,
    planning_mode: str = "leave_now",
    requested_departure: str | None = None,
    requested_arrival: str | None = None,
    generated_at: str | None = None,
    data_basis: str = "mixed",
    reasons: list[object] | None = None,
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
    legs = build_legs(step_list, data_basis=data_basis)

    total_walk = sum(int(leg["street_walking_seconds"]) for leg in legs)
    total_wait = sum(int(leg["wait_seconds"]) for leg in legs)
    total_in_vehicle = sum(int(leg["ride_seconds"]) for leg in legs)
    total_transfer = sum(int(leg["transfer_seconds"]) for leg in legs)
    total_street_walking = sum(int(leg["street_walking_seconds"]) for leg in legs)
    total_in_station_transfer = sum(
        int(leg["in_station_transfer_seconds"]) for leg in legs
    )
    # Single OD has no waypoint dwell; multi-stop uses build_chained_itinerary.
    total_dwell = 0

    # Prefer provider door-to-door total when available. Component sums may
    # not equal this (walks estimated, waits often unknown without walk ISO).
    route_total_seconds = _first_route_total_seconds(step_list)
    route_total_minutes = _first_route_total_minutes(step_list)
    if route_total_seconds is not None:
        total_duration_seconds = route_total_seconds
    elif route_total_minutes is not None:
        # Compatibility for older parsed routes. New directions parsing emits
        # route_total_seconds so provider precision is retained end to end.
        total_duration_seconds = max(0, int(round(route_total_minutes * 60)))
    else:
        total_duration_seconds = (
            total_walk + total_wait + total_in_vehicle + total_transfer + total_dwell
        )

    transit_count = sum(1 for leg in legs if leg["mode"] in TRANSIT_MODES)
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
        "requested_arrival": requested_arrival,
        "generated_at": generated_at,
        "data_basis": data_basis,
        # Freshness is the planning snapshot time when provided; not invented.
        "data_freshness": generated_at,
        "departure_at": departure_at,
        "arrival_at": arrival_at,
        "total_duration_seconds": total_duration_seconds,
        "total_walk_seconds": total_walk,
        "total_street_walking_seconds": total_street_walking,
        "total_in_station_transfer_seconds": total_in_station_transfer,
        "total_transfer_seconds": total_transfer,
        "total_wait_seconds": total_wait,
        "total_in_vehicle_seconds": total_in_vehicle,
        "total_dwell_seconds": total_dwell,
        "transfer_count": transfer_count,
        "legs": legs,
        "structured_recommendation_reasons": list(reasons or []),
    }


def build_chained_itinerary(
    segments: list[dict] | None,
    *,
    origin: Any,
    final_destination: Any,
    planning_mode: str = "leave_now",
    requested_departure: str | None = None,
    requested_arrival: str | None = None,
    generated_at: str | None = None,
    data_basis: str = "mixed",
    reasons: list[object] | None = None,
    itinerary_id: str | None = None,
) -> dict:
    """Merge ordered OD segments into one multi-stop CanonicalItinerary.

    Each ``segments`` item is::

        {
          "steps": list[dict],           # parsed Google route steps for this OD
          "origin_place": str|dict?,     # optional explicit segment origin
          "destination_place": str|dict, # intermediate stop or final place
          "dwell_minutes": int|float?,   # optional; default 25 for intermediates
          "dwell_source": "default"|"user"?,  # optional; inferred if omitted
        }

    Intermediate destinations (all but the last segment) become ``waypoints``
    with server-owned dwell. Totals:

    - ``total_dwell_seconds`` = sum of intermediate dwells * 60
    - ``total_duration_seconds`` = sum of segment totals + total dwell
    - ``legs`` = concatenation of each segment's canonical legs
    - ``departure_at`` / ``arrival_at`` from first / last segment clocks

    Does not invent FE-side dwell. Pure function: no network, no LLM.

    Later wiring: after N ``plan_trip`` OD plans (or a multi-stop tool), pass
    each plan's parsed steps + place + optional rider dwell into this helper
    and emit one chained itinerary / card instead of FE multi-card merge.
    """
    segment_list = list(segments or [])
    if not segment_list:
        raise ValueError("build_chained_itinerary requires at least one segment")

    built: list[dict] = []
    waypoints: list[dict] = []
    total_dwell_seconds = 0
    previous_destination: Any = origin

    for index, raw in enumerate(segment_list):
        if not isinstance(raw, dict):
            raise TypeError(
                f"segment[{index}] must be a dict with steps/destination_place"
            )
        steps = raw.get("steps")
        place = raw.get("destination_place")
        is_last = index == len(segment_list) - 1
        segment_origin = raw.get("origin_place", previous_destination)
        segment_destination = final_destination if is_last else place

        segment_itinerary = build_canonical_itinerary(
            steps if isinstance(steps, list) else list(steps or []),
            origin=segment_origin,
            destination=segment_destination,
            planning_mode=planning_mode,
            requested_departure=requested_departure if index == 0 else None,
            requested_arrival=requested_arrival if index == len(segment_list) - 1 else None,
            generated_at=generated_at,
            data_basis=data_basis,
            itinerary_id=None,
        )
        built.append(segment_itinerary)
        previous_destination = segment_destination

        if not is_last:
            dwell_minutes, dwell_source = _resolve_dwell(raw)
            waypoint = _place_fields(place)
            waypoint["dwell_minutes"] = dwell_minutes
            waypoint["dwell_source"] = dwell_source
            waypoints.append(waypoint)
            total_dwell_seconds += max(0, int(dwell_minutes)) * 60

    # Keep both the legacy flat leg sequence and the canonical segment
    # boundaries. Existing direct-route consumers can continue reading
    # ``legs``; modern map/card/rail consumers must read ``segments`` so an
    # intermediate destination is never mistaken for an ordinary transfer.
    all_legs: list[dict] = []
    canonical_segments: list[dict] = []
    dwell_events: list[dict] = []
    for index, itin in enumerate(built):
        segment_legs = [
            {**leg, "segment_index": index}
            for leg in list(itin.get("legs") or [])
        ]
        all_legs.extend(segment_legs)
        segment_origin = (
            origin
            if index == 0
            else _place_fields(segment_list[index - 1].get("destination_place"))
        )
        segment_destination = (
            final_destination
            if index == len(built) - 1
            else _place_fields(segment_list[index].get("destination_place"))
        )
        canonical_segments.append(
            {
                "segment_index": index,
                "origin": segment_origin,
                "destination": segment_destination,
                "legs": segment_legs,
                "duration_seconds": int(itin["total_duration_seconds"]),
            }
        )
        if index < len(waypoints):
            waypoint = waypoints[index]
            dwell_events.append(
                {
                    "event_type": "dwell",
                    "after_segment_index": index,
                    "waypoint": waypoint,
                    "duration_seconds": int(waypoint["dwell_minutes"]) * 60,
                    "source": waypoint["dwell_source"],
                }
            )

    total_walk = sum(int(itin["total_walk_seconds"]) for itin in built)
    total_street_walking = sum(
        int(itin.get("total_street_walking_seconds") or 0) for itin in built
    )
    total_in_station_transfer = sum(
        int(itin.get("total_in_station_transfer_seconds") or 0) for itin in built
    )
    total_wait = sum(int(itin["total_wait_seconds"]) for itin in built)
    total_in_vehicle = sum(int(itin["total_in_vehicle_seconds"]) for itin in built)
    # Dwell is not a transfer. However, changing services between two
    # separately planned OD segments still is. Count every transit boarding
    # across the complete canonical journey so B35 -> B37 is one transfer,
    # not a misleading zero.
    transit_count = sum(
        1
        for leg in all_legs
        if str(leg.get("mode") or "").upper() in TRANSIT_MODES
    )
    transfer_count = max(0, transit_count - 1)
    total_duration_seconds = (
        sum(int(itin["total_duration_seconds"]) for itin in built) + total_dwell_seconds
    )

    departure_at = built[0].get("departure_at")
    arrival_at = built[-1].get("arrival_at")

    return {
        "itinerary_id": itinerary_id or str(uuid4()),
        "origin": origin,
        "waypoints": waypoints,
        "destination": final_destination,
        "timezone": TIMEZONE_NAME,
        "planning_mode": planning_mode,
        "requested_departure": requested_departure,
        "requested_arrival": requested_arrival,
        "generated_at": generated_at,
        "data_basis": data_basis,
        "data_freshness": generated_at,
        "departure_at": departure_at,
        "arrival_at": arrival_at,
        "total_duration_seconds": total_duration_seconds,
        "total_walk_seconds": total_walk,
        "total_street_walking_seconds": total_street_walking,
        "total_in_station_transfer_seconds": total_in_station_transfer,
        "total_transfer_seconds": sum(
            int(itin.get("total_transfer_seconds") or 0) for itin in built
        ),
        "total_wait_seconds": total_wait,
        "total_in_vehicle_seconds": total_in_vehicle,
        "total_dwell_seconds": total_dwell_seconds,
        "transfer_count": transfer_count,
        "legs": all_legs,
        "segments": canonical_segments,
        "dwell_events": dwell_events,
        "structured_recommendation_reasons": list(reasons or []),
    }


def _resolve_dwell(segment: dict) -> tuple[int, str]:
    """Return (dwell_minutes, dwell_source) for an intermediate segment."""
    raw_source = segment.get("dwell_source")
    if "dwell_minutes" in segment and segment.get("dwell_minutes") is not None:
        try:
            minutes = int(round(float(segment["dwell_minutes"])))
        except (TypeError, ValueError):
            minutes = DEFAULT_DWELL_MINUTES
            source = "default"
        else:
            minutes = max(0, minutes)
            if raw_source in ("default", "user"):
                source = str(raw_source)
            else:
                source = "user"
            return minutes, source
    else:
        minutes = DEFAULT_DWELL_MINUTES
        source = "default"

    if raw_source in ("default", "user"):
        source = str(raw_source)
    return minutes, source


def _place_fields(place: Any) -> dict:
    """Normalize a place string or dict into waypoint base fields."""
    if isinstance(place, dict):
        lat = place.get("lat")
        if lat is None:
            lat = place.get("latitude")
        lng = place.get("lng")
        if lng is None:
            lng = place.get("longitude")
        if lng is None:
            lng = place.get("lon")
        display = (
            place.get("display_name")
            or place.get("label")
            or place.get("name")
            or place.get("address")
        )
        return {
            "place_id": place.get("place_id"),
            "display_name": display,
            "address": place.get("address"),
            "lat": lat,
            "lng": lng,
        }
    return {
        "place_id": None,
        "display_name": str(place) if place is not None else None,
        "address": None,
        "lat": None,
        "lng": None,
    }


def _first_route_total_minutes(steps: list[dict]) -> float | None:
    for step in steps:
        value = step.get("route_total_minutes")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _first_route_total_seconds(steps: list[dict]) -> int | None:
    for step in steps:
        value = step.get("route_total_seconds")
        if isinstance(value, (int, float)) and value >= 0:
            return int(round(value))
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
