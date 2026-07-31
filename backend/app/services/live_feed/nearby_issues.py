"""Canonical, relevance-bounded issues for the live-feed home module."""

from __future__ import annotations

from datetime import datetime, timezone
import re

MAX_NEARBY_STATION_HOPS = 3
_STALL_PATTERN = re.compile(
    r"\b(stalled|stopped|disabled|held)\b.{0,28}\btrain\b"
    r"|\btrain\b.{0,28}\b(stalled|stopped|disabled|held)\b",
    re.IGNORECASE,
)


def _normalized_ids(values) -> set[str]:
    return {
        str(value).strip().rstrip("NS")
        for value in (values or [])
        if str(value).strip()
    }


def _normalized_routes(values) -> set[str]:
    return {
        str(value).strip().upper()
        for value in (values or [])
        if str(value).strip()
    }


def _route_stop_match(index, route_id: str, origin_ids: set[str], issue_ids: set[str]):
    best = None
    for pattern in index.route_patterns.get(route_id, []):
        positions = {
            str(stop_id).rstrip("NS"): position
            for stop_id, position in pattern.get("pos", {}).items()
        }
        origin_positions = [positions[value] for value in origin_ids if value in positions]
        issue_positions = [positions[value] for value in issue_ids if value in positions]
        if not origin_positions or not issue_positions:
            continue
        for origin_position in origin_positions:
            for issue_position in issue_positions:
                hops = abs(issue_position - origin_position)
                if best is None or hops < best["hops"]:
                    stop_id = str(pattern["stop_ids"][issue_position]).rstrip("NS")
                    stop = index.stops.get(stop_id, {})
                    best = {
                        "hops": hops,
                        "stop_id": stop_id,
                        "stop_name": stop.get("name") or stop_id,
                    }
    return best


def _summary(route_id: str, match: dict, nearby_stop_name: str) -> str:
    issue_station = match["stop_name"]
    hops = match["hops"]
    if hops == 0:
        return f"{route_id} train stalled at {issue_station}"
    stop_word = "stop" if hops == 1 else "stops"
    return (
        f"{route_id} train stalled near {issue_station} "
        f"· {hops} {stop_word} from {nearby_stop_name}"
    )


def build_nearby_transit_issues(
    *,
    gtfs,
    alerts: list[dict],
    nearby_stop_id: str | None,
    nearby_stop_name: str | None,
    nearby_route_ids,
    selected_route_ids=(),
    observed_at: int,
) -> list[dict]:
    """Return at most one confirmed issue with canonical station-hop facts.

    Raw vehicle age is deliberately excluded: an old GTFS-RT timestamp is stale
    telemetry, not evidence that a train is stalled. Strong inference can enter
    this contract only after a separate repeated-observation service has
    corroborated it.
    """

    index = gtfs.__dict__.get("_pattern_index")
    origin_ids = _normalized_ids([nearby_stop_id])
    nearby_routes = _normalized_routes(nearby_route_ids)
    selected_routes = _normalized_routes(selected_route_ids)
    if index is None or not origin_ids or not nearby_routes:
        return []

    candidates = []
    for alert in alerts:
        text = " ".join(
            str(alert.get(field) or "") for field in ("header", "description")
        )
        if not _STALL_PATTERN.search(text):
            continue
        issue_stop_ids = _normalized_ids(alert.get("stop_ids"))
        alert_routes = _normalized_routes(alert.get("route_ids"))
        if not issue_stop_ids:
            continue

        for route_id in sorted(alert_routes & nearby_routes):
            match = _route_stop_match(index, route_id, origin_ids, issue_stop_ids)
            if match is None or match["hops"] > MAX_NEARBY_STATION_HOPS:
                continue
            relevance = (
                "planned_route" if route_id in selected_routes else "nearby_line"
            )
            candidates.append(
                {
                    "id": str(alert.get("alert_id") or f"mta-{route_id}-{match['stop_id']}"),
                    "route_ids": [route_id],
                    "station_id": match["stop_id"],
                    "station_name": match["stop_name"],
                    "stops_away": match["hops"],
                    "confidence": "confirmed",
                    "status": "stalled",
                    "summary": _summary(
                        route_id,
                        match,
                        nearby_stop_name or str(nearby_stop_id),
                    ),
                    "source_types": ["mta_service_alert"],
                    "observed_at": datetime.fromtimestamp(
                        observed_at, tz=timezone.utc
                    ).isoformat(),
                    "relevance": relevance,
                }
            )

    candidates.sort(
        key=lambda issue: (
            issue["stops_away"],
            issue["route_ids"][0],
            issue["id"],
        )
    )
    return candidates[:1]
