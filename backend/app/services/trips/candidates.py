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


def _parse_candidate_analysis(raw_text: str) -> tuple[int | None, dict[int, dict[str, str]]]:
    match = _CANDIDATE_ANALYSIS_PATTERN.search(raw_text or "")
    if not match:
        return None, {}

    try:
        payload = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None, {}

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
    if is_recommended:
        alert_phrase = (
            " with no active alert penalty"
            if route_score.get("alert_count", 0) == 0
            else f" despite {route_score['alert_count']} alert(s) on its lines"
        )
        return (
            f"Best overall score: {route_score['total_minutes']} min, "
            f"{route_score['transfers']} transfer(s){alert_phrase}."
        )

    route_minutes = int(route_score["total_minutes"])
    chosen_minutes = int(chosen_score["total_minutes"])
    delay = route_minutes - chosen_minutes
    transfer_delta = int(route_score["transfers"]) - int(chosen_score["transfers"])
    alert_delta = int(route_score["alert_count"]) - int(chosen_score["alert_count"])
    if delay >= 3:
        return f"Slower by about {delay} minutes under current service conditions."
    if transfer_delta > 0:
        return "Adds an extra transfer, which weakens reliability right now."
    if alert_delta > 0:
        return "Touches more active service alerts than the selected route."
    return "Available, but less reliable than the recommended route right now."

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
