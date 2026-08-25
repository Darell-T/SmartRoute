"""Build deterministic route candidates and rider-facing comparison copy."""

import re

from app.services.trips import scoring, text
from app.services.mta.static_gtfs.stop_patterns import normalize_station_name

_CANDIDATE_ANALYSIS_PATTERN = re.compile(
    r"\[CANDIDATE_ANALYSIS\](.*?)\[/CANDIDATE_ANALYSIS\]",
    re.IGNORECASE | re.DOTALL,
)


def _strip_model_control_blocks(raw_text: str) -> str:
    without_route = re.sub(r"\s*\[ROUTE:\d+\]\s*", "", raw_text or "")
    return _CANDIDATE_ANALYSIS_PATTERN.sub("", without_route).strip()

def _build_fallback_candidate_reason(
    route: list[dict],
    chosen_route: list[dict],
    is_recommended: bool,
    route_score: dict | None = None,
    chosen_score: dict | None = None,
) -> str:
    route_score = route_score or scoring._route_score(route, [])
    chosen_score = chosen_score or scoring._route_score(chosen_route, [])
    route_alerts = route_score.get("alerts") or []
    chosen_alerts = chosen_score.get("alerts") or []
    route_alert = text._safe_text(route_alerts[0], 72) if route_alerts else ""
    chosen_alert = text._safe_text(chosen_alerts[0], 72) if chosen_alerts else ""
    if is_recommended:
        material_factors: list[str] = []
        event_penalty = float(route_score.get("event_crowd_penalty") or 0)
        walking_penalty = float(route_score.get("walking_penalty") or 0)
        if walking_penalty > 0:
            walk_minutes = max(0, int(route_score.get("walk_minutes") or 0))
            material_factors.append(
                f"preferred for less walking ({walk_minutes} min on foot)"
            )
        if event_penalty > 0:
            material_factors.append("relevant event crowd exposure")
        if material_factors:
            duration = int(route_score.get("total_minutes") or 0)
            return (
                f"Recommended at {duration} min; "
                + " and ".join(material_factors[:2])
                + "."
            )
        if route_score.get("alert_count", 0) == 0:
            return f"Fastest route at {route_score['total_minutes']} min."
        if route_alert:
            return (
                f"Fastest route despite an alert: {route_alert}."
            )
        return "Fastest route despite active service alerts."

    route_minutes = int(route_score["total_minutes"])
    chosen_minutes = int(chosen_score["total_minutes"])
    delay = route_minutes - chosen_minutes
    transfer_delta = int(route_score["transfers"]) - int(chosen_score["transfers"])
    alert_delta = int(route_score["alert_count"]) - int(chosen_score["alert_count"])
    if delay <= -2 and alert_delta > 0:
        if route_alert:
            return f"Faster by {abs(delay)} min, but affected by {route_alert}."
        return f"Faster by {abs(delay)} min, but affected by service alerts."
    if delay <= -2 and transfer_delta > 0:
        return f"Faster by {abs(delay)} min, but adds an extra transfer."
    if delay >= 3 and alert_delta > 0:
        if route_alert:
            return f"Slower by {delay} min and affected by {route_alert}."
        return f"Slower by {delay} min and affected by service alerts."
    if delay >= 3:
        return f"Slower by {delay} min."
    if transfer_delta > 0:
        return (
            "Adds an extra transfer."
            if transfer_delta == 1
            else f"Adds {transfer_delta} extra transfers."
        )
    if alert_delta > 0:
        if route_alert:
            return f"Affected by {route_alert}."
        return "Affected by service alerts."
    if chosen_alert and delay <= 2:
        return f"Similar time, but {chosen_alert} is already accounted for."
    return "Similar time, but less reliable than the selected route."

def _build_route_candidates(
    routes: list[list[dict]],
    chosen_index: int,
    candidate_analysis: dict[int, dict[str, str]],
    scored_routes: list[dict] | None = None,
) -> list[dict]:
    chosen_route = routes[chosen_index] if routes else []
    scores = scoring._score_by_index(scored_routes or scoring._score_routes(routes, []))
    chosen_score = scores.get(chosen_index, scoring._route_score(chosen_route, []))
    candidates = []
    for index, route in enumerate(routes):
        is_recommended = index == chosen_index
        # Candidate-analysis prose is model-authored and may contain stale
        # alternate duration/walking/transfer facts.  It remains useful for
        # validating the model's choice, but the rider-facing reason must be
        # derived from this server-owned score row instead.
        route_score = scores.get(index, scoring._route_score(route, []))
        fallback = _build_fallback_candidate_reason(
            route,
            chosen_route,
            is_recommended,
            route_score,
            chosen_score,
        )
        candidates.append(
            {
                "id": f"candidate-{index}",
                "index": index,
                "steps": route,
                "is_recommended": is_recommended,
                "total_minutes": route_score["total_minutes"],
                "selection_score": route_score["score"],
                "selection_rank": route_score.get("rank", index + 1),
                "score_breakdown": {
                    "duration_minutes": route_score["total_minutes"],
                    "transfers": route_score["transfers"],
                    "active_alerts": route_score["alert_count"],
                    "transit_lines": scoring._route_lines(route),
                    "walking_minutes": route_score.get("walk_minutes", 0),
                    "street_walking_seconds": route_score.get(
                        "street_walking_seconds", 0
                    ),
                    "in_station_transfer_seconds": route_score.get(
                        "in_station_transfer_seconds", 0
                    ),
                    "event_crowd_penalty": route_score.get(
                        "event_crowd_penalty", 0
                    ),
                    "accessibility_status": route_score.get(
                        "accessibility_status", "unknown"
                    ),
                },
                # Only the chosen route is enriched on the initial response;
                # alternates carry empty intermediate-stop lists and are filled
                # in lazily via POST /api/trip/enrich-route when selected.
                "enriched": is_recommended,
                "can_enrich_on_select": not is_recommended,
                "recommendation_reason": fallback if is_recommended else None,
                "rejection_reason": fallback if not is_recommended else None,
            }
        )
    return candidates


def _transit_steps(route: list[dict]) -> list[dict]:
    return [
        step
        for step in route
        if step.get("type") in ("SUBWAY", "BUS")
    ]


def _collect_route_and_bus_ids(routes: list[list[dict]]) -> tuple[set[str], set[str]]:
    """Collects the set of subway/bus route ids and the subset that are bus
    routes across every candidate route, and stamps each transit step with
    empty `intermediate_stops`/`intermediate_stop_locations` keys -- present
    (even if empty) until, and unless, GTFS enrichment fills them in. Shared
    by /api/trip and the plan_trip agent tool, which both build these same
    sets from a freshly parsed Google Routes response before fetching live
    context (alerts/stalled vehicles) for those routes."""
    route_ids: set[str] = set()
    bus_route_ids: set[str] = set()
    for route in routes:
        for step in route:
            step_type = step["type"]
            if step_type in ("SUBWAY", "BUS"):
                route_ids.add(step["route_id"])
                step.setdefault("intermediate_stops", [])
                step.setdefault("intermediate_stop_locations", [])
            if step_type == "BUS":
                bus_route_ids.add(step["route_id"])
    return route_ids, bus_route_ids


def _route_ids(route: list[dict]) -> list[str]:
    route_ids: list[str] = []
    for step in _transit_steps(route):
        route_id = str(step.get("route_id") or step.get("train_line") or "").strip().upper()
        if route_id and route_id not in route_ids:
            route_ids.append(route_id)
    return route_ids


def _display_stop(value: object) -> str:
    return text._safe_text(str(value or ""), 44).strip()


def _candidate_display_label(route: list[dict]) -> str:
    steps = _transit_steps(route)
    route_ids = _route_ids(route)
    if not route_ids:
        return "walk-only option"

    modes = {str(step.get("type") or "").upper() for step in steps}
    route_label = "/".join(route_ids)
    if modes == {"BUS"}:
        base = f"{route_label} bus option"
    elif len(route_ids) == 1:
        base = f"{route_label} route"
    else:
        base = f"{route_label} subway option"

    if len(steps) > 1:
        transfer_stop = _display_stop(steps[1].get("departure_stop"))
        if transfer_stop:
            return f"{base} via {transfer_stop}"

    board_stop = _display_stop(steps[0].get("departure_stop"))
    return f"{base} from {board_stop}" if board_stop else base


def _build_route_candidate_labels(routes: list[list[dict]]) -> list[dict]:
    return [
        {
            "index": index,
            "displayLabel": _candidate_display_label(route),
            "routeIds": _route_ids(route),
        }
        for index, route in enumerate(routes)
    ]


def route_family_signature(route: list[dict]) -> tuple[tuple[str, str, str, str], ...]:
    """Return the stable transit-family and transfer-topology identity."""

    signature = []
    for step in route or []:
        if (
            not isinstance(step, dict)
            or str(step.get("type") or "").upper() not in {"SUBWAY", "BUS"}
        ):
            continue
        mode = str(step.get("type") or "").upper()
        route_id = str(step.get("route_id") or step.get("train_line") or "").strip().upper()
        boarding = step.get("departure_stop_id") or step.get("departure_stop")
        alighting = step.get("arrival_stop_id") or step.get("arrival_stop")
        signature.append(
            (
                mode,
                route_id,
                normalize_station_name(str(boarding or "")),
                normalize_station_name(str(alighting or "")),
            )
        )
    return tuple(signature)


def dedupe_route_families(routes: list[list[dict]]) -> list[list[dict]]:
    """Keep the first provider route for each structural family."""

    seen = set()
    unique = []
    for route in routes:
        signature = route_family_signature(route)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(route)
    return unique
