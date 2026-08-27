"""Neutral route-request validation and provider-recovery helpers."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any

from app.services.mta.static_gtfs.stop_patterns import normalize_station_name
from app.services.trips import candidates, text
from app.services.trips.location import ResolvedPlace, canonical_display_name

MAX_ROUTE_ID_LENGTH = 12
MAX_NORMALIZED_ROUTE_IDS = 16
_ROUTE_ID_SHAPE_RE = re.compile(r"^[A-Z0-9]{1,8}(?:-SBS|\+)?$")


def normalize_route_id(value: object) -> str | None:
    """Return one bounded canonical transit route ID, or ``None`` for junk."""

    text_value = str(value or "").strip().upper()
    if not text_value or len(text_value) > MAX_ROUTE_ID_LENGTH:
        return None
    return text_value if _ROUTE_ID_SHAPE_RE.fullmatch(text_value) else None


def normalize_route_ids(values: object) -> tuple[str, ...]:
    """Normalize, de-duplicate, and bound an array-like route-ID collection."""

    if not isinstance(values, (list, tuple, set, frozenset)):
        return ()
    ordered = (
        sorted(values, key=lambda value: str(value or "").strip().upper())
        if isinstance(values, (set, frozenset))
        else values
    )
    route_ids: list[str] = []
    seen: set[str] = set()
    for value in ordered:
        route_id = normalize_route_id(value)
        if route_id is None or route_id in seen:
            continue
        seen.add(route_id)
        route_ids.append(route_id)
        if len(route_ids) >= MAX_NORMALIZED_ROUTE_IDS:
            break
    return tuple(route_ids)


def point_label(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value or value.lower() == "user":
        return "your location"
    return text._safe_text(canonical_display_name(value), 80)


def summary_eta_minutes(route: list[dict], total_duration_seconds: int) -> int:
    """Return card/digest ETA from canonical itinerary seconds only."""
    if not route:
        return 0
    return max(1, round(int(total_duration_seconds) / 60))


def parse_rfc3339(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be RFC3339 with a timezone offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def _address_resolution_failure(error) -> bool:
    """Only explicit destination-resolution failures may retry by coords."""
    code = str(getattr(error, "code", "") or "")
    if code not in {"http_400", "http_404", "request_failed"}:
        return False
    summary = str(getattr(error, "provider_summary", "") or str(error)).lower()
    return any(marker in summary for marker in ("address", "destination", "geocod"))


async def route_with_recovery(
    *,
    directions_service,
    origin: ResolvedPlace,
    destination: ResolvedPlace,
    destination_query: str,
    allowed_modes: list[str],
    routing_preference: str,
    departure_time: str | None,
    prefer_coordinates: bool = False,
) -> list:
    """Try the rider's resolved label first, then privately recover by coords."""
    if prefer_coordinates:
        response = await directions_service.get_transit_route(
            (origin.latitude, origin.longitude),
            destination_query,
            (destination.latitude, destination.longitude),
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
        return directions_service.parse_response(response)
    try:
        response = await directions_service.get_transit_route(
            (origin.latitude, origin.longitude),
            destination_query,
            None,
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
        )
    except directions_service.GoogleRoutesError as exc:
        if not _address_resolution_failure(exc):
            raise
    else:
        routes = directions_service.parse_response(response)
        if routes:
            return routes
    response = await directions_service.get_transit_route(
        (origin.latitude, origin.longitude),
        destination_query,
        (destination.latitude, destination.longitude),
        allowed_travel_modes=allowed_modes,
        routing_preference=routing_preference,
        departure_time=departure_time,
    )
    return directions_service.parse_response(response)


async def recover_structural_route(
    *,
    directions_service,
    pattern_index,
    primary_routes: list[list[dict]],
    origin: ResolvedPlace,
    destination: ResolvedPlace,
    destination_query: str,
    departure_time: str | None,
    allowed_modes: list[str],
    routing_preference: str,
    excluded_route_ids: set[str],
    excluded_modes: set[str],
    telemetry: dict[str, Any] | None = None,
) -> list[list[dict]]:
    """Recover one provider-backed transfer family from static topology."""

    diagnostics = telemetry if isinstance(telemetry, dict) else {}
    diagnostics.update(
        recovery_attempted=False,
        recovery_succeeded=False,
        recovered_structural_signature=None,
        recovered_service_chain=None,
        added_provider_latency_ms=0.0,
    )
    if pattern_index is None or not primary_routes or destination is None:
        return []

    suggestion = None
    seed_step = None
    seed_route = None
    for route in primary_routes:
        for step in route or []:
            if str(step.get("type") or "").upper() != "SUBWAY":
                continue
            route_id = str(step.get("route_id") or step.get("train_line") or "").strip()
            if not route_id:
                continue
            candidate = pattern_index.suggest_one_transfer(
                route_id,
                step.get("departure_stop"),
                step.get("arrival_stop"),
                {"lat": destination.latitude, "lon": destination.longitude},
                boarding_coords=step.get("departure_coords"),
                alighting_coords=step.get("arrival_coords"),
                excluded_route_ids=excluded_route_ids,
                excluded_modes=excluded_modes,
                allowed_modes=allowed_modes,
            )
            if candidate is not None:
                suggestion = candidate
                seed_step = step
                seed_route = route_id.upper()
                break
        if suggestion is not None:
            break
    if suggestion is None:
        return []

    continuation_route = str(suggestion.get("continuation_route_id") or "").upper()
    if not suggestion.get("continuation_transfer_stop_id"):
        return []
    expected_signature = (
        (
            "SUBWAY",
            seed_route,
            normalize_station_name(str(seed_step.get("departure_stop") or "")),
            normalize_station_name(str(suggestion.get("transfer_stop_name") or "")),
        ),
        (
            "SUBWAY",
            continuation_route,
            normalize_station_name(str(suggestion.get("transfer_stop_name") or "")),
            normalize_station_name(str(suggestion.get("destination_stop_name") or "")),
        ),
    )
    if any(
        candidates.route_family_signature(route) == expected_signature
        for route in primary_routes
    ):
        return []

    diagnostics["recovery_attempted"] = True
    started = time.monotonic()
    transfer_coords = suggestion.get("transfer_stop_coords") or {}
    try:
        transfer = ResolvedPlace(
            name=str(suggestion.get("transfer_stop_name") or ""),
            latitude=float(transfer_coords["latitude"]),
            longitude=float(transfer_coords["longitude"]),
            source="gtfs",
        )
    except (KeyError, TypeError, ValueError):
        return []
    if not transfer.name:
        return []
    try:
        combined = await directions_service.get_transfer_route_pair(
            origin_coords=(origin.latitude, origin.longitude),
            transfer_name=transfer.name,
            transfer_coords=(transfer.latitude, transfer.longitude),
            continuation_transfer_coords=suggestion.get("continuation_transfer_stop_coords") or {},
            destination_query=destination_query,
            destination_coords=(destination.latitude, destination.longitude),
            allowed_travel_modes=allowed_modes,
            routing_preference=routing_preference,
            departure_time=departure_time,
            first_service=seed_route,
            second_service=continuation_route,
        )
        if not combined:
            return []
    except Exception:
        return []
    finally:
        diagnostics["added_provider_latency_ms"] = (time.monotonic() - started) * 1000

    signature = candidates.route_family_signature(combined)
    diagnostics.update(
        {
            "recovery_succeeded": True,
            "recovered_structural_signature": signature,
            "recovered_service_chain": [
                row[1] for row in signature if row[1]
            ],
        }
    )
    return [combined]


async def prepare_structural_candidates(
    primary_routes,
    *,
    tool_input,
    ctx,
    dependencies,
    origin,
    destination,
    destination_query,
    departure_time,
    allowed_modes,
    routing_preference,
    telemetry,
    timings,
):
    """Dedupe primary routes and recover one bounded structural family."""
    try:
        max_candidates = max(1, int(tool_input.get("max_candidates") or len(primary_routes)))
    except (TypeError, ValueError):
        max_candidates = len(primary_routes)
    arrival_by = tool_input.get("arrival_by")
    pattern_index = getattr(ctx.gtfs, "_pattern_index", None)
    directions_service = dependencies.directions_service
    excluded_route_ids = {
        str(value).strip().upper() for value in tool_input.get("excluded_route_ids") or []
    }
    excluded_modes = {str(value).strip().upper() for value in tool_input.get("exclude_modes") or []}
    primary_count = len(primary_routes)
    routes = candidates.dedupe_route_families(primary_routes)
    diagnostics = {
        "primary_provider_candidate_count": primary_count,
        "primary_structurally_unique_candidate_count": len(routes),
        "duplicate_candidate_count": primary_count - len(routes),
        "recovery_attempted": False,
        "recovery_succeeded": False,
        "recovered_structural_signature": None,
        "recovered_service_chain": None,
        "added_provider_latency_ms": 0.0,
    }
    if (
        not arrival_by
        and max_candidates > 1
        and len(routes) < max_candidates
        and callable(getattr(pattern_index, "suggest_one_transfer", None))
    ):
        routes.extend(await recover_structural_route(
            directions_service=directions_service,
            pattern_index=pattern_index,
            primary_routes=routes,
            origin=origin,
            destination=destination,
            destination_query=destination_query,
            departure_time=departure_time,
            allowed_modes=allowed_modes,
            routing_preference=routing_preference,
            excluded_route_ids=excluded_route_ids,
            excluded_modes=excluded_modes,
            telemetry=diagnostics,
        ))
    routes = candidates.dedupe_route_families(routes)
    diagnostics["final_structurally_unique_candidate_count"] = len(routes)
    if isinstance(telemetry, dict):
        telemetry.update(diagnostics)
    timings["route_family_recovery_ms"] = diagnostics.get(
        "added_provider_latency_ms", 0.0
    )
    return routes[:max_candidates]


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
    """Estimate a provider-supported departure for an explicit arrival target."""
    target = parse_rfc3339(arrival_by, field="arrival_by")
    parsed = await route_with_recovery(
        directions_service=directions_service,
        origin=origin,
        destination=destination,
        destination_query=destination_query,
        allowed_modes=allowed_modes,
        routing_preference=routing_preference,
        departure_time=target.isoformat(),
    )
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
