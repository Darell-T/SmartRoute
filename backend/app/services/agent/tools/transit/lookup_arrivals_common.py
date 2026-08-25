"""Shared normalization and payload shaping for arrival providers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from app.services.agent.tools.transit.direction import normalize_direction, resolve_direction
from app.services.agent.tools.location_resolution import parse_coordinates
from app.services.agent.tools._types import ToolContext
from app.services.evidence import evidence_envelope

ARRIVAL_LIMIT_DEFAULT = 3
ARRIVAL_LIMIT_MAX = 5
FEED_STALE_AFTER_S = 120
BOARDING_BUFFER_MINUTES = 2

_STATION_ALIASES = {
    "newkirk avenue": "Newkirk Plaza",
    "newkirk av": "Newkirk Plaza",
    "atlantic avenue": "Atlantic Av-Barclays Ctr",
    "atlantic terminal": "Atlantic Av-Barclays Ctr",
    "barclays center": "Atlantic Av-Barclays Ctr",
    "penn station": "34 St-Penn Station",
    "grand central": "Grand Central-42 St",
}


def _normalized_name(value: object) -> str:
    normalized = " ".join(
        str(value or "").casefold().replace("–", "-").replace("—", "-").split()
    )
    normalized = re.sub(r"\b(?:station|stop)\b", "", normalized)
    return " ".join(normalized.split())


def canonical_station_query(value: object) -> str:
    normalized = _normalized_name(value)
    return _STATION_ALIASES.get(normalized, str(value or "").strip())


def _active_boarding(ctx: ToolContext, route_id: str) -> dict | None:
    active = (ctx.session or {}).get("active_trip") or {}
    boarding = active.get("first_boarding") if isinstance(active, dict) else None
    if not isinstance(boarding, dict):
        return None
    active_route = str(boarding.get("route_id") or "").strip().upper()
    if route_id and active_route and active_route != route_id:
        return None
    return boarding


def _location(
    tool_input: dict, ctx: ToolContext, boarding: dict | None
) -> tuple[float, float] | None:
    explicit = parse_coordinates(tool_input.get("user_location"))
    if explicit is not None:
        return explicit
    queried = parse_coordinates(tool_input.get("stop_query"))
    if queried is not None:
        return queried
    if boarding:
        coords = parse_coordinates(boarding.get("coordinates"))
        if coords is not None:
            return coords
    return parse_coordinates(ctx.origin or {})


def _normalize_direction(value: object) -> str | None:
    """Canonicalize exact semantic values, retaining unknown labels."""

    normalized = _normalized_name(value)
    if not normalized:
        return None
    return normalize_direction(normalized) or normalized


def _direction_from_boarding(boarding: dict | None) -> str | None:
    if not boarding:
        return None
    for key in (
        "canonical_direction",
        "semantic_direction",
        "direction",
        "direction_label",
        "headsign",
    ):
        canonical = normalize_direction(boarding.get(key))
        if canonical:
            return canonical
    label = boarding.get("headsign") or boarding.get("direction_label")
    resolved = resolve_direction(label, [boarding]) if label else None
    if resolved and resolved.resolved:
        return resolved.resolved
    return _normalize_direction(label)


def _direction_value_matches(requested: str, candidate: object) -> bool:
    candidate_value = _normalize_direction(candidate)
    if not candidate_value:
        return False
    requested_canonical = normalize_direction(requested)
    candidate_canonical = normalize_direction(candidate_value)
    if requested_canonical and candidate_canonical:
        return requested_canonical == candidate_canonical
    return _normalized_name(requested) == _normalized_name(candidate_value)


def _dedupe_predictions(
    values: Iterable[dict], *, limit: int, now: int
) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    result: list[dict] = []
    for value in sorted(values, key=lambda row: int(row.get("arrival_time") or 0)):
        arrival_time = int(value.get("arrival_time") or 0)
        direction = str(value.get("direction") or "unknown")
        key = (direction, arrival_time)
        if arrival_time <= now or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def assess_catchability(
    arrival_minutes: Iterable[int],
    *,
    walking_minutes: int,
    boarding_buffer_minutes: int = BOARDING_BUFFER_MINUTES,
) -> dict:
    values = sorted({int(value) for value in arrival_minutes if int(value) > 0})
    threshold = max(0, int(walking_minutes)) + max(0, int(boarding_buffer_minutes))
    catchable = next((value for value in values if value >= threshold), None)
    return {
        "walking_minutes": max(0, int(walking_minutes)),
        "boarding_buffer_minutes": max(0, int(boarding_buffer_minutes)),
        "arrival_minutes": values,
        "catchable_arrival_minutes": catchable,
        "confidence": 0.9 if values else 0.0,
    }


def _empty_payload(
    route_id: str,
    status: str,
    *,
    now: int,
    stop_name: str = "Transit stop",
    ambiguity: list[dict] | None = None,
) -> dict:
    observed = datetime.fromtimestamp(now, timezone.utc)
    return {
        "route_id": route_id,
        "stop": {"id": "", "name": stop_name},
        "directions": [],
        "updated_at": observed.isoformat(),
        "source_status": status,
        "evidence": evidence_envelope(
            "mta_arrivals",
            {"directions": []},
            observed_at=observed,
            available=False,
        ).to_dict(observed),
        **({"ambiguity": ambiguity} if ambiguity is not None else {}),
    }


def _arrival_payload(
    *,
    route_id: str,
    stop: dict,
    grouped: dict[str, list[dict]],
    updated_at: int,
    now: int,
    status: str,
    walking_minutes: int | None = None,
    valid_until: object = None,
) -> dict:
    directions = []
    for direction, values in sorted(grouped.items()):
        arrivals = []
        for value in values:
            timestamp = int(value["arrival_time"])
            minutes = (timestamp - now + 59) // 60
            if minutes <= 0:
                continue
            arrivals.append(
                {
                    "expected_at": datetime.fromtimestamp(
                        timestamp, timezone.utc
                    ).isoformat(),
                    "minutes": minutes,
                    "realtime": status in {"live", "stale"},
                    "trip_id": value.get("trip_id"),
                    "vehicle_id": value.get("vehicle_id"),
                }
            )
        if not arrivals:
            continue
        label = str(values[0].get("direction_label") or direction).title()
        if direction == "uptown":
            label = "Uptown / Manhattan-bound"
        elif direction == "downtown":
            label = "Downtown / Brooklyn-bound"
        directions.append({"id": direction, "label": label, "arrivals": arrivals})
    all_minutes = [
        arrival["minutes"]
        for group in directions
        for arrival in group["arrivals"]
    ]
    payload = {
        "route_id": route_id,
        "stop": {
            "id": str(stop.get("stop_id") or ""),
            "name": str(stop.get("stop_name") or "Transit stop"),
            "distance_meters": stop.get("distance_m"),
            "latitude": stop.get("stop_lat"),
            "longitude": stop.get("stop_lon"),
        },
        "directions": directions,
        "updated_at": datetime.fromtimestamp(updated_at, timezone.utc).isoformat(),
        "source_status": status,
    }
    if walking_minutes is not None:
        payload["catchability"] = assess_catchability(
            all_minutes,
            walking_minutes=walking_minutes,
        )
    available = status not in {"provider_unavailable", "stop_not_resolved"}
    payload["evidence"] = evidence_envelope(
        "mta_gtfs_rt" if status != "scheduled" else "mta_static_gtfs",
        {"directions": directions},
        observed_at=datetime.fromtimestamp(updated_at, timezone.utc),
        ttl_seconds=FEED_STALE_AFTER_S if status != "scheduled" else None,
        valid_until=valid_until,
        available=available,
    ).to_dict(datetime.fromtimestamp(now, timezone.utc))
    return payload
