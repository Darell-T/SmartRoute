"""Shared, bounded route-advisor context construction.

The HTTP trip endpoint and the agent ``plan_trip`` tool must give the route
advisor the same view of a set of candidate routes.  Keeping that boundary in
one small module also gives deterministic replays an honest baseline: it keeps
the routes and core MTA alerts, while explicitly removing the supplemental
city-intelligence inputs under evaluation.

This module only shapes already-collected evidence.  In particular, it never
fetches Ticketmaster, 511NY, Grok, or MTA data.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Iterable, Mapping

from app.services.trips import candidates, text


class PlanningMode(str, Enum):
    """The evidence boundary used when comparing route decisions."""

    BASELINE = "baseline"
    INTELLIGENCE = "intelligence"
    SHADOW = "shadow"


_MAX_TICKETMASTER_EVENT_IMPACTS = 12
_MAX_EVENT_LIST_VALUES = 8
_ALLOWED_CROWD_LEVELS = {"low", "moderate", "high"}


def parse_planning_mode(value: PlanningMode | str | None) -> PlanningMode:
    """Validate a planning mode instead of silently weakening a replay."""

    if isinstance(value, PlanningMode):
        return value
    normalized = str(value or PlanningMode.INTELLIGENCE.value).strip().lower()
    try:
        return PlanningMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in PlanningMode)
        raise ValueError(f"planning mode must be one of: {allowed}") from exc


def _bounded_text(value: object, limit: int) -> str:
    sanitized = text._safe_text(str(value or ""), limit).strip()
    # Event evidence does not need a provider URL, and a copied query string
    # could contain a credential. Preserve the rider-facing description while
    # removing URL-shaped material before it reaches a model prompt or log.
    return re.sub(r"https?://\S+", "[link removed]", sanitized).strip()


def _bounded_string_list(value: object, *, item_limit: int = 36) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        normalized = _bounded_text(item, item_limit)
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) >= _MAX_EVENT_LIST_VALUES:
            break
    return result


def normalize_ticketmaster_event_impacts(values: Iterable[object] | None) -> list[dict[str, Any]]:
    """Keep optional event evidence small, structured, and advisor-safe.

    Ticketmaster itself is intentionally not called here.  Production callers
    may supply evidence that was already obtained through their own bounded
    path, while replays can inject recorded event-impact rows directly.
    """

    normalized: list[dict[str, Any]] = []
    for raw in values or ():
        if not isinstance(raw, Mapping):
            continue
        event_id = _bounded_text(raw.get("event_id") or raw.get("id"), 80)
        venue = _bounded_text(raw.get("venue") or raw.get("venue_name"), 100)
        title = _bounded_text(raw.get("title") or raw.get("name"), 140)
        # An identifier, venue, or title makes the provenance reviewable.  Do
        # not pass anonymous, arbitrary payloads into a model prompt.
        if not (event_id or venue or title):
            continue

        row: dict[str, Any] = {
            "event_id": event_id,
            "title": title,
            "venue": venue,
            "stations": _bounded_string_list(raw.get("stations")),
            "lines": _bounded_string_list(raw.get("lines"), item_limit=12),
            "impact_scope": _bounded_text(raw.get("impact_scope") or "station_crowding", 48),
            "window_start_iso": _bounded_text(
                raw.get("window_start_iso") or raw.get("surge_start_iso") or raw.get("pre_event_start_iso"),
                48,
            ),
            "window_end_iso": _bounded_text(
                raw.get("window_end_iso") or raw.get("surge_end_iso") or raw.get("pre_event_end_iso"),
                48,
            ),
        }
        crowd_level = _bounded_text(raw.get("crowd_level"), 16).lower()
        if crowd_level in _ALLOWED_CROWD_LEVELS:
            row["crowd_level"] = crowd_level
        normalized.append(row)
        if len(normalized) >= _MAX_TICKETMASTER_EVENT_IMPACTS:
            break
    return normalized


def build_advisor_payload(
    *,
    routes: list[list[dict]],
    service_alerts: Iterable[object] | None,
    incidents: Iterable[object] | None = None,
    stalled_trains: Iterable[object] | None = None,
    stalled_buses: Iterable[object] | None = None,
    ticketmaster_event_impacts: Iterable[object] | None = None,
    mode: PlanningMode | str | None = PlanningMode.INTELLIGENCE,
) -> dict[str, Any]:
    """Build a common advisor contract for endpoint, agent, and replays.

    ``baseline`` retains candidate routes, labels, and standard MTA alerts. It
    deliberately sends empty supplemental evidence arrays, rather than an
    accidentally sparse or differently shaped request.  ``intelligence`` and
    ``shadow`` include supplied signals.  Shadow has the same evidence as
    intelligence; selecting whether it can affect a user response is owned by
    the caller, not this data-shaping function.
    """

    parsed_mode = parse_planning_mode(mode)
    payload: dict[str, Any] = {
        "routes": routes,
        "route_candidate_labels": candidates._build_route_candidate_labels(routes),
        "service_alerts": list(service_alerts or ()),
        "planning_mode": parsed_mode.value,
        "incidents": [],
        "stalled_trains": [],
        "stalled_buses": [],
        "ticketmaster_event_impacts": [],
    }
    if parsed_mode is PlanningMode.BASELINE:
        return payload

    payload.update(
        {
            "incidents": list(incidents or ()),
            "stalled_trains": list(stalled_trains or ()),
            "stalled_buses": list(stalled_buses or ()),
            "ticketmaster_event_impacts": normalize_ticketmaster_event_impacts(ticketmaster_event_impacts),
        }
    )
    return payload


def parse_advisor_selection(raw_recommendation: str, candidate_count: int) -> tuple[int, dict[int, dict[str, str]]]:
    """Parse a model/recorded recommendation while preserving route-zero fallback."""

    chosen_index = 0
    route_tag_match = re.search(r"\[ROUTE:(\d+)\]", raw_recommendation or "")
    if route_tag_match:
        chosen_index = int(route_tag_match.group(1))
    analysis_selected_index, candidate_analysis = candidates._parse_candidate_analysis(raw_recommendation)
    if route_tag_match is None and analysis_selected_index is not None:
        chosen_index = analysis_selected_index
    if not 0 <= chosen_index < candidate_count:
        chosen_index = 0
    return chosen_index, candidate_analysis
