"""Validated input and provider-request helpers for plan_trip."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services.agent.tools._location import ResolvedPlace, canonical_display_name
from app.services.trips import text


def point_label(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value or value.lower() == "user":
        return "your location"
    return text._safe_text(canonical_display_name(value), 80)


def summary_eta_minutes(route: list[dict], total_duration_seconds: int) -> int:
    """Return card/digest ETA from canonical itinerary seconds only.

    An empty route returns 0 (no trip). Otherwise max(1, round(seconds/60))
    ensures a sub-minute non-empty trip still shows as 1 minute.
    """
    if not route:
        return 0
    return max(1, round(int(total_duration_seconds) / 60))


def parse_rfc3339(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be RFC3339 with a timezone offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


async def route_with_recovery(
    *,
    directions_service,
    origin: ResolvedPlace,
    destination: ResolvedPlace,
    destination_query: str,
    allowed_modes: list[str],
    routing_preference: str,
    departure_time: str | None,
) -> dict:
    """Try the rider's resolved label first, then privately recover by coords."""
    try:
        return await directions_service.get_transit_route(
            (origin.latitude, origin.longitude),
            destination_query,
            None,
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
    except directions_service.GoogleRoutesError:
        # This is an internal provider recovery, not a second rider operation.
        # Keep the named destination attached to the resulting itinerary.
        return await directions_service.get_transit_route(
            (origin.latitude, origin.longitude),
            destination_query,
            (destination.latitude, destination.longitude),
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )


async def derive_arrive_by_departure(
    *,
    directions_service,
    origin: ResolvedPlace,
    destination: ResolvedPlace,
    destination_query: str,
    arrival_by: str,
    allowed_modes: list[str],
    routing_preference: str,
) -> str:
    """Estimate a provider-supported departure for an explicit arrival target.

    Google Routes' transit endpoint takes departure time, not arrival time.
    The probe is an internal estimate only; the actual planning request below
    is still made at the derived departure and its provider timestamps remain
    canonical.
    """
    target = parse_rfc3339(arrival_by, field="arrival_by")
    probe = await route_with_recovery(
        directions_service=directions_service,
        origin=origin,
        destination=destination,
        destination_query=destination_query,
        allowed_modes=allowed_modes,
        routing_preference=routing_preference,
        departure_time=target.isoformat(),
    )
    parsed = directions_service.parse_response(probe)
    if not parsed:
        raise directions_service.GoogleRoutesError(
            "no_route",
            "no route available to estimate arrive-by departure",
        )
    durations = [
        int(step["route_total_seconds"])
        for route in parsed
        for step in route
        if isinstance(step.get("route_total_seconds"), (int, float))
    ]
    if not durations:
        raise directions_service.GoogleRoutesError(
            "no_duration",
            "provider did not return a route duration for arrive-by planning",
        )
    return (target - timedelta(seconds=min(durations))).isoformat()


def validated_waypoints(
    value: object,
    *,
    max_waypoints: int,
    max_waypoint_chars: int,
) -> tuple[list[str], str | None]:
    if not isinstance(value, list):
        return [], None
    if len(value) > max_waypoints:
        return [], f"a trip can include at most {max_waypoints} waypoints"
    seen: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            return [], "each waypoint must be a location name"
        if len(raw) > max_waypoint_chars:
            return [], f"each waypoint must be at most {max_waypoint_chars} characters"
        place = raw.strip()
        if not place:
            return [], "each waypoint must be a location name"
        if place not in seen:
            seen.append(place)
    return seen, None
