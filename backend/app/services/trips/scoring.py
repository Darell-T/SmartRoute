"""Route scoring + route-step accessors.

Pure functions over Google-parsed route step dicts. Depends only on ``text``
(for ``_safe_text``). ``_step_route_id`` lives here for route scoring and
shared candidate display helpers.
"""

from app.services.trips import event_crowd, text
from app.services.trips.itinerary import TRANSIT_MODES
from app.services.trips.transfer_semantics import (
    route_accessibility,
    route_walking_totals,
)


def _step_minutes(step: dict) -> int:
    if step.get("type") in TRANSIT_MODES:
        minutes = step.get("minutes_until_arrival")
        if isinstance(minutes, (int, float)):
            return max(1, round(minutes))
        return 8
    return 4

def _route_total_minutes(route: list[dict]) -> int:
    for step in route or []:
        route_total = step.get("route_total_minutes")
        if isinstance(route_total, (int, float)):
            return max(1, round(route_total))
    live_arrivals = [
        step.get("minutes_until_arrival")
        for step in route or []
        if step.get("type") in TRANSIT_MODES
        and isinstance(step.get("minutes_until_arrival"), (int, float))
    ]
    if live_arrivals:
        return max(1, round(max(live_arrivals)))
    return max(1, sum(_step_minutes(step) for step in route))

def _route_transfer_count(route: list[dict]) -> int:
    transit_steps = [step for step in route if step.get("type") in TRANSIT_MODES]
    return max(0, len(transit_steps) - 1)

def _step_route_id(step: dict) -> str:
    return str(step.get("route_id") or step.get("train_line") or "").strip().upper()

def _route_lines(route: list[dict]) -> list[str]:
    lines: list[str] = []
    for step in route or []:
        if step.get("type") not in TRANSIT_MODES:
            continue
        line = _step_route_id(step)
        if line and line not in lines:
            lines.append(line)
    return lines

def _route_alert_hits(route: list[dict], alerts: list[dict] | None) -> list[str]:
    route_lines = set(_route_lines(route))
    hits: list[str] = []
    for alert in alerts or []:
        alert_routes = {
            str(route_id or "").strip().upper()
            for route_id in alert.get("route_ids", [])
            if str(route_id or "").strip()
        }
        if route_lines & alert_routes:
            title = text._safe_text(alert.get("header") or "active alert", 80)
            if title and title not in hits:
                hits.append(title)
    return hits


def _component_score_total(
    *,
    total_minutes: int,
    transfers: int,
    alert_count: int,
    event_crowd_penalty: float,
    walking_penalty: int,
    preferred_mode_penalty: int,
) -> float:
    """Single authoritative route score formula.

    Shared by single-leg ``_route_score`` and the multi-stop aggregate rows in
    ``route_option_assembly`` so every score is exactly explainable from the
    component fields of the row that reports it.
    """
    return (
        total_minutes
        + transfers * 4
        + alert_count * 8
        + event_crowd_penalty
        + walking_penalty
        + preferred_mode_penalty
    )


def _route_score(
    route: list[dict],
    alerts: list[dict] | None,
    *,
    route_index: int = 0,
    ticketmaster_event_impacts: list[dict] | None = None,
    routing_preference: str = "FEWER_TRANSFERS",
    preferred_modes: list[str] | set[str] | None = None,
) -> dict:
    total_minutes = _route_total_minutes(route)
    transfers = _route_transfer_count(route)
    alert_hits = _route_alert_hits(route, alerts)
    transit_count = len(_route_lines(route))
    street_walking_seconds, in_station_transfer_seconds = route_walking_totals(route)
    event_penalty = event_crowd.route_event_penalty(
        route_index,
        ticketmaster_event_impacts or [],
    )
    walking_penalty = (
        round(street_walking_seconds / 60) * 2
        if routing_preference == "LESS_WALKING"
        else 0
    )
    preferred = {
        _normalized_mode(mode)
        for mode in preferred_modes or []
        if str(mode).strip()
    }
    route_modes = {
        _normalized_mode(step.get("type"))
        for step in route
        if _normalized_mode(step.get("type")) in {"SUBWAY", "BUS", "RAIL"}
    }
    preferred_mode_penalty = 4 if preferred and not route_modes.intersection(preferred) else 0
    score = _component_score_total(
        total_minutes=total_minutes,
        transfers=transfers,
        alert_count=len(alert_hits),
        event_crowd_penalty=event_penalty,
        walking_penalty=walking_penalty,
        preferred_mode_penalty=preferred_mode_penalty,
    )
    return {
        "total_minutes": total_minutes,
        "transfers": transfers,
        "alert_count": len(alert_hits),
        "transit_count": transit_count,
        "score": score,
        "alerts": alert_hits[:2],
        "event_crowd_penalty": event_penalty,
        "street_walking_seconds": street_walking_seconds,
        "in_station_transfer_seconds": in_station_transfer_seconds,
        "walk_minutes": round(street_walking_seconds / 60),
        "walking_penalty": walking_penalty,
        "preferred_mode_penalty": preferred_mode_penalty,
        "accessibility_status": route_accessibility(route),
    }

def _score_routes(
    routes: list[list[dict]],
    alerts: list[dict] | None,
    ticketmaster_event_impacts: list[dict] | None = None,
    *,
    routing_preference: str = "FEWER_TRANSFERS",
    preferred_modes: list[str] | set[str] | None = None,
) -> list[dict]:
    scored = []
    for index, route in enumerate(routes):
        score = _route_score(
            route,
            alerts,
            route_index=index,
            ticketmaster_event_impacts=ticketmaster_event_impacts,
            routing_preference=routing_preference,
            preferred_modes=preferred_modes,
        )
        scored.append({"index": index, **score})
    scored.sort(
        key=lambda row: (
            row["score"],
            row["total_minutes"],
            row["transfers"],
            row["index"],
        )
    )
    rank_by_index = {row["index"]: rank + 1 for rank, row in enumerate(scored)}
    for row in scored:
        row["rank"] = rank_by_index[row["index"]]
    return scored

def _score_by_index(scored_routes: list[dict]) -> dict[int, dict]:
    return {int(row["index"]): row for row in scored_routes}


def _normalized_mode(value: object) -> str:
    mode = str(value or "").strip().upper()
    return "RAIL" if mode in {"TRAIN", "LIGHT_RAIL", "TRAM"} else mode
