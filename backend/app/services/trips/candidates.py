"""Parse ATLAS's control blocks and build the route-candidate list.

Depends on ``scoring`` (route scores) and ``text`` (sanitization). The model
emits a ``[CANDIDATE_ANALYSIS]{...}[/CANDIDATE_ANALYSIS]`` block; this module
parses it and, when the model omits a reason, falls back to a computed
time/transfer/alert comparison (display copy only -- never changes selection).
"""

import json
import re

from app.services.trips import scoring, text

_CANDIDATE_ANALYSIS_PATTERN = re.compile(
    r"\[CANDIDATE_ANALYSIS\](.*?)\[/CANDIDATE_ANALYSIS\]",
    re.IGNORECASE | re.DOTALL,
)


def _parse_candidate_analysis(
    raw_text: str,
    *,
    candidate_count: int | None = None,
    strict: bool = False,
) -> tuple[int | None, dict[int, dict[str, str]]]:
    """Parse the shared candidate-analysis marker.

    REST and shadow evaluation retain the historical best-effort behavior.
    Agent route turns opt into ``strict`` so malformed control data cannot
    silently select a canonical route.
    """
    matches = list(_CANDIDATE_ANALYSIS_PATTERN.finditer(raw_text or ""))
    if not matches:
        if strict:
            raise ValueError("candidate analysis control block is missing")
        return None, {}
    if strict and len(matches) != 1:
        raise ValueError("candidate analysis control block must appear exactly once")

    try:
        payload = json.loads(matches[0].group(1).strip())
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError("candidate analysis is not valid JSON") from exc
        return None, {}
    if not isinstance(payload, dict):
        if strict:
            raise ValueError("candidate analysis must be an object")
        return None, {}

    if strict:
        return _parse_strict_candidate_analysis(payload, candidate_count)

    selected_index = payload.get("selected_route_index")
    try:
        parsed_selected = int(selected_index) if selected_index is not None else None
    except (TypeError, ValueError):
        parsed_selected = None

    rows = payload.get("candidate_analysis")
    if not isinstance(rows, list):
        return parsed_selected, {}

    analysis: dict[int, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        is_recommended = bool(row.get("is_recommended"))
        generic_reason = row.get("reason") or ""
        recommendation_reason = text._safe_text(
            row.get("recommendation_reason")
            or (generic_reason if is_recommended else "")
            or ""
        )
        rejection_reason = text._safe_text(
            row.get("rejection_reason")
            or (generic_reason if not is_recommended else "")
            or ""
        )
        if recommendation_reason or rejection_reason:
            analysis[index] = {
                "recommendation_reason": recommendation_reason,
                "rejection_reason": rejection_reason,
            }

    return parsed_selected, analysis


def _parse_strict_candidate_analysis(
    payload: dict,
    candidate_count: int | None,
) -> tuple[int, dict[int, dict[str, str]]]:
    """Validate the agent-only route control contract without a second parser."""
    if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) or candidate_count < 1:
        raise ValueError("candidate analysis requires a positive candidate count")
    selected_index = payload.get("selected_route_index")
    if not isinstance(selected_index, int) or isinstance(selected_index, bool):
        raise ValueError("selected_route_index must be an integer")
    if not 0 <= selected_index < candidate_count:
        raise ValueError("selected_route_index is outside the candidate range")
    rows = payload.get("candidate_analysis")
    if not isinstance(rows, list) or len(rows) != candidate_count:
        raise ValueError("candidate analysis must contain every candidate exactly once")

    analysis: dict[int, dict[str, str]] = {}
    recommended_indexes: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("candidate analysis rows must be objects")
        index = row.get("index")
        recommended = row.get("is_recommended")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < candidate_count:
            raise ValueError("candidate analysis index is invalid")
        if index in analysis:
            raise ValueError("candidate analysis contains a duplicate index")
        if not isinstance(recommended, bool):
            raise ValueError("candidate analysis is_recommended must be boolean")

        recommendation_reason = text._safe_text(row.get("recommendation_reason") or "").strip()
        rejection_reason = text._safe_text(row.get("rejection_reason") or "").strip()
        if recommended:
            if index != selected_index or not recommendation_reason:
                raise ValueError("selected candidate requires recommendation_reason")
            recommended_indexes.append(index)
        elif rejection_reason:
            pass
        else:
            raise ValueError("unselected candidate requires rejection_reason")
        analysis[index] = {
            "recommendation_reason": recommendation_reason,
            "rejection_reason": rejection_reason,
        }

    if set(analysis) != set(range(candidate_count)) or recommended_indexes != [selected_index]:
        raise ValueError("candidate analysis recommendation does not match selected route")
    return selected_index, analysis

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
        if route_score.get("alert_count", 0) == 0:
            return (
                f"Fastest route at {route_score['total_minutes']} min with "
                "no reported service alerts."
            )
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
        analysis = candidate_analysis.get(index, {})
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
                },
                # Only the chosen route is enriched on the initial response;
                # alternates carry empty intermediate-stop lists and are filled
                # in lazily via POST /api/trip/enrich-route when selected.
                "enriched": is_recommended,
                "can_enrich_on_select": not is_recommended,
                "recommendation_reason": (
                    analysis.get("recommendation_reason") or fallback
                    if is_recommended
                    else None
                ),
                "rejection_reason": (
                    analysis.get("rejection_reason") or fallback
                    if not is_recommended
                    else None
                ),
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
