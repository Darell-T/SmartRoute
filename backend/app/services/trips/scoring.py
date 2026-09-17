"""Route scoring + route-step accessors.

Pure functions over Google-parsed route step dicts. Depends only on ``text``
(for ``safe_text``). ``step_route_id`` lives here for route scoring and
shared candidate display helpers.
"""

from app.services import text
from app.services.mta.alerts import is_material_service_alert
from app.services.parsing import nonnegative_int
from app.services.trips.crowds import event as event_crowd
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


def _parse_route_seconds_minutes(route: list[dict]) -> int | None:
    for step in route or []:
        route_total_seconds = step.get("route_total_seconds")
        if (
            isinstance(route_total_seconds, (int, float))
            and not isinstance(route_total_seconds, bool)
            and route_total_seconds >= 0
        ):
            return max(1, round(route_total_seconds / 60))
    return None


def _parse_route_minutes_field(route: list[dict]) -> int | None:
    for step in route or []:
        route_total = step.get("route_total_minutes")
        if isinstance(route_total, (int, float)):
            return max(1, round(route_total))
    return None


def _select_live_arrival_minutes(route: list[dict]) -> int | None:
    live_arrivals = [
        step.get("minutes_until_arrival")
        for step in route or []
        if step.get("type") in TRANSIT_MODES
        and isinstance(step.get("minutes_until_arrival"), (int, float))
    ]
    if live_arrivals:
        return max(1, round(max(live_arrivals)))
    return None


def route_total_minutes(route: list[dict]) -> int:
    # ``build_canonical_itinerary`` treats the provider's seconds value as the
    # authoritative door-to-door duration.  Keep scoring and passenger reason
    # facts on that same owner when older parsed responses also carry a rounded
    # ``route_total_minutes`` field.
    parsed = _parse_route_seconds_minutes(route)
    if parsed is not None:
        return parsed
    parsed = _parse_route_minutes_field(route)
    if parsed is not None:
        return parsed
    live = _select_live_arrival_minutes(route)
    if live is not None:
        return live
    return max(1, sum(_step_minutes(step) for step in route))


def route_transfer_count(route: list[dict]) -> int:
    transit_steps = [step for step in route if step.get("type") in TRANSIT_MODES]
    return max(0, len(transit_steps) - 1)


def step_route_id(step: dict) -> str:
    return str(step.get("route_id") or step.get("train_line") or "").strip().upper()


def _route_lines(route: list[dict]) -> list[str]:
    lines: list[str] = []
    for step in route or []:
        if step.get("type") not in TRANSIT_MODES:
            continue
        line = step_route_id(step)
        if line and line not in lines:
            lines.append(line)
    return lines


def _parse_alert_route_ids(alert: dict) -> set[str]:
    return {
        str(route_id or "").strip().upper()
        for route_id in alert.get("route_ids", [])
        if str(route_id or "").strip()
    }


def _select_matching_alerts(
    route: list[dict], alerts: list[dict] | None
) -> list[dict]:
    route_lines = set(_route_lines(route))
    matching: list[dict] = []
    for alert in alerts or []:
        if not isinstance(alert, dict) or not is_material_service_alert(alert):
            continue
        if route_lines & _parse_alert_route_ids(alert):
            matching.append(alert)
    return matching


def route_alert_hits(route: list[dict], alerts: list[dict] | None) -> list[str]:
    hits: list[str] = []
    for alert in _select_matching_alerts(route, alerts):
        title = text.safe_text(alert.get("header") or "active alert", 80)
        if title and title not in hits:
            hits.append(title)
    return hits


def _calculate_alert_severity_penalty(copy: str) -> float | None:
    if any(term in copy for term in ("elevator", "escalator", "accessibility")):
        return None
    if any(term in copy for term in ("suspended", "no service", "not running")):
        return 24.0
    if "severe" in copy:
        return 16.0
    if "minor" in copy:
        return 4.0
    return 8.0


def route_alert_penalty(route: list[dict], alerts: list[dict] | None) -> float:
    """Weight relevant service impact, not mere alert existence.

    Accessibility-only notices remain available as evidence but do not make a
    non-accessibility route request take a large detour. Suspended/no-service
    notices can justify roughly a 20-minute sacrifice; ordinary disruptions
    retain the existing eight-minute cost and explicitly minor notices cost
    less.
    """

    penalty = 0.0
    seen: set[str] = set()
    for alert in _select_matching_alerts(route, alerts):
        copy = " ".join(
            str(alert.get(field) or "") for field in ("header", "description")
        ).casefold()
        if copy in seen:
            continue
        seen.add(copy)
        added = _calculate_alert_severity_penalty(copy)
        if added is not None:
            penalty += added
    return penalty


def _canonical_total_minutes(itinerary: dict | None, route: list[dict]) -> int:
    """Read the passenger total from the finalized itinerary when available.

    Selection must never compare a rounded provider field with a separately
    derived walking estimate.  The itinerary is the one canonical owner of
    the door-to-door total; the route fallback exists for older callers that
    do not yet provide an itinerary.
    """

    if isinstance(itinerary, dict):
        value = itinerary.get("total_duration_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(1, round(float(value) / 60))
    return route_total_minutes(route)


def _route_incident_hits(incidents: list[dict] | None) -> list[str]:
    """Return confirmed incident descriptions already scoped to one route."""

    hits: list[str] = []
    for incident in incidents or []:
        if not isinstance(incident, dict):
            continue
        description = text.safe_text(
            incident.get("description")
            or incident.get("location")
            or incident.get("title")
            or "confirmed route incident",
            120,
        )
        if description and description not in hits:
            hits.append(description)
    return hits


def _vehicle_signal_hits(claims: list[dict] | None) -> list[str]:
    """Return possible vehicle signals without treating them as confirmed."""

    hits: list[str] = []
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        status_text = " ".join(
            str(claim.get(key) or "")
            for key in ("status", "progress_status", "ProgressStatus")
        ).casefold()
        if "layover" in status_text:
            continue
        route = str(claim.get("route") or claim.get("route_id") or "").strip().upper()
        if not route:
            continue
        description = f"possible delay signal on {route}"
        if description not in hits:
            hits.append(description)
    return hits


def _select_walking_seconds(
    canonical: dict, route: list[dict]
) -> tuple[int, int]:
    street_seconds = nonnegative_int(canonical.get("total_street_walking_seconds"))
    in_station_seconds = nonnegative_int(
        canonical.get("total_in_station_transfer_seconds")
    )
    if street_seconds or in_station_seconds:
        return street_seconds, in_station_seconds
    return route_walking_totals(route)


def _calculate_walking_preference_penalty(
    street_seconds: int, routing_preference: str
) -> int:
    if routing_preference != "LESS_WALKING":
        return 0
    return round(street_seconds / 60) * 2


def _calculate_preferred_mode_penalty(
    route: list[dict], preferred_modes: list[str] | set[str] | None
) -> int:
    preferred = {
        normalized_mode(mode) for mode in preferred_modes or [] if str(mode).strip()
    }
    if not preferred:
        return 0
    route_modes = {
        normalized_mode(step.get("type"))
        for step in route
        if normalized_mode(step.get("type")) in {"SUBWAY", "BUS", "RAIL"}
    }
    return 0 if route_modes.intersection(preferred) else 4


def finalized_route_score(
    *,
    route: list[dict],
    itinerary: dict | None,
    alerts: list[dict] | None,
    incidents: list[dict] | None,
    vehicle_claims: list[dict] | None,
    event_impacts: list[dict] | None,
    route_index: int = 0,
    routing_preference: str = "FEWER_TRANSFERS",
    preferred_modes: list[str] | set[str] | None = None,
    hard_constraints: dict | None = None,
    evidence_coverage: dict[str, str] | None = None,
) -> dict:
    """Build a private fallback row from one finalized candidate.

    This function is intentionally server-only.  It consumes the same
    finalized itinerary and route-scoped evidence that the model sees as
    qualitative factors, and is used only for deterministic fallback,
    telemetry, and consistency checks.  It does not rank or label candidates
    for the outer model.
    """

    canonical = itinerary if isinstance(itinerary, dict) else {}
    total_minutes = _canonical_total_minutes(canonical, route)
    transfers = nonnegative_int(
        canonical.get("transfer_count"),
        default=route_transfer_count(route),
    )
    alert_hits = route_alert_hits(route, alerts)
    alert_penalty = route_alert_penalty(route, alerts)
    incident_hits = _route_incident_hits(incidents)
    vehicle_hits = _vehicle_signal_hits(vehicle_claims)
    # Confirmed incidents receive the largest service-condition penalty. A
    # vehicle signal remains explicitly unconfirmed and is therefore smaller.
    incident_penalty = float(len(incident_hits) * 12)
    vehicle_penalty = float(len(vehicle_hits) * 4)
    event_penalty = event_crowd.route_event_penalty(
        route_index,
        event_impacts or [],
    )
    street_seconds, in_station_seconds = _select_walking_seconds(canonical, route)
    walking_penalty = _calculate_walking_preference_penalty(
        street_seconds, routing_preference
    )
    preferred_mode_penalty = _calculate_preferred_mode_penalty(route, preferred_modes)
    service_condition_penalty = alert_penalty + incident_penalty + vehicle_penalty
    score = component_score_total(
        total_minutes=total_minutes,
        transfers=transfers,
        alert_count=len(alert_hits),
        alert_penalty=service_condition_penalty,
        event_crowd_penalty=event_penalty,
        walking_penalty=walking_penalty,
        preferred_mode_penalty=preferred_mode_penalty,
    )
    constraints = hard_constraints or {}
    return {
        "total_minutes": total_minutes,
        "transfers": transfers,
        "alert_count": len(alert_hits),
        "alert_penalty": alert_penalty,
        "transit_count": len(_route_lines(route)),
        "score": score,
        "alerts": alert_hits[:2],
        "event_crowd_penalty": event_penalty,
        "street_walking_seconds": street_seconds,
        "in_station_transfer_seconds": in_station_seconds,
        "walk_minutes": round(street_seconds / 60),
        "walking_penalty": walking_penalty,
        "preferred_mode_penalty": preferred_mode_penalty,
        "accessibility_status": constraints.get(
            "accessibility_status", route_accessibility(route)
        ),
        "incident_count": len(incident_hits),
        "confirmed_incident_impacts": incident_hits[:3],
        "vehicle_signal_count": len(vehicle_hits),
        "unconfirmed_vehicle_impacts": vehicle_hits[:3],
        "service_condition_penalty": service_condition_penalty,
        "hard_constraints_satisfied": constraints.get("satisfied") is True,
        "hard_constraint_violations": list(constraints.get("violations") or []),
        "evidence_coverage": dict(evidence_coverage or {}),
        "arrival_at": canonical.get("arrival_at"),
        "departure_at": canonical.get("departure_at"),
        "waypoint_count": len(canonical.get("waypoints") or []),
        "finalized": True,
    }


def component_score_total(
    *,
    total_minutes: int,
    transfers: int,
    alert_count: int,
    event_crowd_penalty: float,
    walking_penalty: int,
    preferred_mode_penalty: int,
    alert_penalty: float | None = None,
) -> float:
    """Single authoritative route score formula.

    Shared by single-leg ``route_score`` and multi-stop aggregate rows so
    every score is exactly explainable from the component fields of the row
    that reports it.
    """
    return (
        total_minutes
        + transfers * 4
        + (alert_count * 8 if alert_penalty is None else alert_penalty)
        + event_crowd_penalty
        + walking_penalty
        + preferred_mode_penalty
    )


def alert_penalty_from_score(score: dict) -> float:
    """Read or reconstruct the alert component from a canonical score row."""

    explicit = score.get("alert_penalty")
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            return 0.0
    explained_without_alerts = (
        float(score.get("total_minutes") or 0)
        + max(0, int(score.get("transfers") or 0)) * 4
        + max(0.0, float(score.get("event_crowd_penalty") or 0))
        + max(0.0, float(score.get("walking_penalty") or 0))
        + max(0.0, float(score.get("preferred_mode_penalty") or 0))
    )
    inferred = float(score.get("score") or 0) - explained_without_alerts
    if inferred > 0:
        return inferred
    return float(max(0, int(score.get("alert_count") or 0)) * 8)


def route_score(
    route: list[dict],
    alerts: list[dict] | None,
    *,
    route_index: int = 0,
    ticketmaster_event_impacts: list[dict] | None = None,
    routing_preference: str = "FEWER_TRANSFERS",
    preferred_modes: list[str] | set[str] | None = None,
) -> dict:
    total_minutes = route_total_minutes(route)
    transfers = route_transfer_count(route)
    alert_hits = route_alert_hits(route, alerts)
    alert_penalty = route_alert_penalty(route, alerts)
    transit_count = len(_route_lines(route))
    street_walking_seconds, in_station_transfer_seconds = route_walking_totals(route)
    event_penalty = event_crowd.route_event_penalty(
        route_index,
        ticketmaster_event_impacts or [],
    )
    walking_penalty = _calculate_walking_preference_penalty(
        street_walking_seconds, routing_preference
    )
    preferred_mode_penalty = _calculate_preferred_mode_penalty(route, preferred_modes)
    score = component_score_total(
        total_minutes=total_minutes,
        transfers=transfers,
        alert_count=len(alert_hits),
        alert_penalty=alert_penalty,
        event_crowd_penalty=event_penalty,
        walking_penalty=walking_penalty,
        preferred_mode_penalty=preferred_mode_penalty,
    )
    return {
        "total_minutes": total_minutes,
        "transfers": transfers,
        "alert_count": len(alert_hits),
        "alert_penalty": alert_penalty,
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


def score_routes(
    routes: list[list[dict]],
    alerts: list[dict] | None,
    ticketmaster_event_impacts: list[dict] | None = None,
    *,
    routing_preference: str = "FEWER_TRANSFERS",
    preferred_modes: list[str] | set[str] | None = None,
) -> list[dict]:
    scored = []
    for index, route in enumerate(routes):
        score = route_score(
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


def score_by_index(scored_routes: list[dict]) -> dict[int, dict]:
    return {int(row["index"]): row for row in scored_routes}


def normalized_mode(value: object) -> str:
    mode = str(value or "").strip().upper()
    return "RAIL" if mode in {"TRAIN", "LIGHT_RAIL", "TRAM"} else mode
