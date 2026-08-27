"""Bounded route-advisor contracts for offline evaluation and replay.

Production route planning is deterministic and model-free.  This module owns
the legacy advisor payload and control-block parser needed by evaluation,
replay, and validation scripts.  It only shapes already-collected evidence;
it never fetches Ticketmaster, 511NY, Grok, or MTA data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, NoReturn, TypeGuard

from app.services.evidence import EvidenceEnvelope, current_payload
from app.services.trips import candidates, text


class PlanningMode(StrEnum):
    """The evidence boundary used when comparing route decisions."""

    BASELINE = "baseline"
    INTELLIGENCE = "intelligence"


_MAX_TICKETMASTER_EVENT_IMPACTS = 12
_MAX_EVENT_LIST_VALUES = 8
_ALLOWED_CROWD_LEVELS = {"low", "moderate", "high"}
_ALLOWED_SOURCE_CLASSES = {
    "structured",
    "official_web",
    "official_x",
    "independent_web",
    "independent_x",
}
_ALLOWED_VERIFICATION_TIERS = {"structured", "official", "corroborative"}
_ALLOWED_EXPOSURE_WINDOWS = {"ingress", "during", "egress"}
_CANDIDATE_ANALYSIS_PATTERN = re.compile(
    r"\[CANDIDATE_ANALYSIS\](.*?)\[/CANDIDATE_ANALYSIS\]",
    re.IGNORECASE | re.DOTALL,
)


def parse_planning_mode(value: PlanningMode | str | None) -> PlanningMode:
    """Validate a planning mode instead of silently weakening a replay."""

    if isinstance(value, PlanningMode):
        return value
    normalized = str(value or PlanningMode.INTELLIGENCE.value).strip().lower()
    try:
        return PlanningMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in PlanningMode)
        message = f"planning mode must be one of: {allowed}"
        raise ValueError(message) from exc


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


def _copy_optional_number(raw: Mapping[str, Any], row: dict[str, Any], key: str) -> None:
    value = raw.get(key)
    if isinstance(value, (int, float)):
        row[key] = value


def _copy_optional_token(
    raw: Mapping[str, Any],
    row: dict[str, Any],
    key: str,
    allowed: set[str],
    limit: int,
) -> None:
    value = _bounded_text(raw.get(key), limit).lower()
    if value in allowed:
        row[key] = value


def _ticketmaster_event_row(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    event_id = _bounded_text(raw.get("event_id") or raw.get("id"), 80)
    venue = _bounded_text(raw.get("venue") or raw.get("venue_name"), 100)
    title = _bounded_text(raw.get("title") or raw.get("name"), 140)
    # An identifier, venue, or title makes the provenance reviewable.  Do
    # not pass anonymous, arbitrary payloads into a model prompt.
    if not (event_id or venue or title):
        return None
    row: dict[str, Any] = {
        "event_id": event_id,
        "title": title,
        "venue": venue,
        "stations": _bounded_string_list(raw.get("stations")),
        "lines": _bounded_string_list(raw.get("lines"), item_limit=12),
        "impact_scope": _bounded_text(raw.get("impact_scope") or "station_crowding", 48),
        "window_start_iso": _bounded_text(
            raw.get("window_start_iso")
            or raw.get("surge_start_iso")
            or raw.get("pre_event_start_iso"),
            48,
        ),
        "window_end_iso": _bounded_text(
            raw.get("window_end_iso")
            or raw.get("surge_end_iso")
            or raw.get("pre_event_end_iso"),
            48,
        ),
    }
    for numeric_key in ("route_index", "distance_meters", "risk_score", "confidence"):
        _copy_optional_number(raw, row, numeric_key)
    _copy_optional_token(raw, row, "exposure_window", _ALLOWED_EXPOSURE_WINDOWS, 16)
    _copy_optional_token(raw, row, "crowd_level", _ALLOWED_CROWD_LEVELS, 16)
    _copy_optional_token(raw, row, "source_class", _ALLOWED_SOURCE_CLASSES, 24)
    _copy_optional_token(raw, row, "verification_tier", _ALLOWED_VERIFICATION_TIERS, 24)
    row["scoring_authorized"] = bool(raw.get("scoring_authorized", True))
    return row


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
        row = _ticketmaster_event_row(raw)
        if row is None:
            continue
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
    scored_candidates: Iterable[Mapping[str, object]] | None = None,
    evidence: Mapping[str, EvidenceEnvelope[Any]] | None = None,
    mode: PlanningMode | str | None = PlanningMode.INTELLIGENCE,
) -> dict[str, Any]:
    """Build a common advisor contract for endpoint, agent, and replays.

    ``baseline`` retains candidate routes, labels, and standard MTA alerts. It
    deliberately sends empty supplemental evidence arrays, rather than an
    accidentally sparse or differently shaped request.  ``intelligence``
    includes supplied signals.
    """

    parsed_mode = parse_planning_mode(mode)
    envelopes = dict(evidence or {})

    def fresh_values(name: str, fallback: Iterable[object] | None) -> list[object]:
        envelope = envelopes.get(name)
        if envelope is None:
            return list(fallback or ())
        return list(current_payload(envelope, empty=[]))

    payload: dict[str, Any] = {
        "routes": routes,
        "route_candidate_labels": candidates._build_route_candidate_labels(routes),
        "service_alerts": fresh_values("alerts", service_alerts),
        "planning_mode": parsed_mode.value,
        "incidents": [],
        "stalled_trains": [],
        "stalled_buses": [],
        "ticketmaster_event_impacts": [],
        "evidence": {
            name: envelope.to_model_dict(empty=[])
            for name, envelope in sorted(envelopes.items())
        },
    }
    if parsed_mode is PlanningMode.BASELINE:
        return payload

    payload.update(
        {
            "incidents": fresh_values("advisor", incidents),
            "stalled_trains": fresh_values("subway_vehicles", stalled_trains),
            "stalled_buses": fresh_values("bus_vehicles", stalled_buses),
            "ticketmaster_event_impacts": normalize_ticketmaster_event_impacts(
                fresh_values("events", ticketmaster_event_impacts)
            ),
        }
    )
    if scored_candidates is not None:
        payload["scored_candidates"] = _normalize_scored_candidates(scored_candidates)
    return payload


def _normalize_scored_candidates(values: Iterable[Mapping[str, object]]) -> list[dict[str, int | float]]:
    """Expose the bounded deterministic comparison view to an agent advisor.

    This is evidence for the selected model, not a route-selection override.
    The canonical route identities remain the zero-based ``routes`` indexes.
    """
    normalized: list[dict[str, int | float]] = []
    for raw in values:
        index = raw.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            continue
        row: dict[str, int | float] = {"index": index}
        for key in (
            "rank",
            "total_minutes",
            "transfers",
            "alert_count",
            "event_crowd_penalty",
            "score",
        ):
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row[key] = value
        normalized.append(row)
    return sorted(normalized, key=lambda row: int(row["index"]))


def _raise_value(message: str) -> NoReturn:
    raise ValueError(message)


def _is_non_bool_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _analysis_match_payload(raw_text: str, *, strict: bool) -> str | None:
    matches = list(_CANDIDATE_ANALYSIS_PATTERN.finditer(raw_text or ""))
    if not matches:
        if not strict:
            return None
        _raise_value("candidate analysis control block is missing")
    if strict and len(matches) != 1:
        _raise_value("candidate analysis control block must appear exactly once")
    return matches[0].group(1).strip()


def _loads_analysis_object(raw_payload: str, *, strict: bool) -> dict | None:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        if not strict:
            return None
        invalid_json = "candidate analysis is not valid JSON"
        raise ValueError(invalid_json) from exc
    if isinstance(payload, dict):
        return payload
    if not strict:
        return None
    _raise_value("candidate analysis must be an object")
    return None


def _lenient_analysis_row(row: object) -> tuple[int, dict[str, str]] | None:
    if not isinstance(row, dict):
        return None
    try:
        index = int(row.get("index"))
    except (TypeError, ValueError):
        return None
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
    if not (recommendation_reason or rejection_reason):
        return None
    return index, {
        "recommendation_reason": recommendation_reason,
        "rejection_reason": rejection_reason,
    }


def _parse_lenient_candidate_analysis(
    payload: dict,
) -> tuple[int | None, dict[int, dict[str, str]]]:
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
        parsed_row = _lenient_analysis_row(row)
        if parsed_row is not None:
            index, reasons = parsed_row
            analysis[index] = reasons
    return parsed_selected, analysis


def parse_candidate_analysis(
    raw_text: str,
    *,
    candidate_count: int | None = None,
    strict: bool = False,
) -> tuple[int | None, dict[int, dict[str, str]]]:
    """Parse the advisor's candidate-analysis control block."""

    raw_payload = _analysis_match_payload(raw_text, strict=strict)
    if raw_payload is None:
        return None, {}
    payload = _loads_analysis_object(raw_payload, strict=strict)
    if payload is None:
        return None, {}
    if strict:
        return _parse_strict_candidate_analysis(payload, candidate_count)
    return _parse_lenient_candidate_analysis(payload)


def _require_candidate_count(candidate_count: int | None) -> int:
    if _is_non_bool_int(candidate_count) and candidate_count >= 1:
        return candidate_count
    _raise_value("candidate analysis requires a positive candidate count")


def _require_selected_index(value: object, candidate_count: int) -> int:
    if not _is_non_bool_int(value):
        _raise_value("selected_route_index must be an integer")
    if not 0 <= value < candidate_count:
        _raise_value("selected_route_index is outside the candidate range")
    return value


def _strict_row_reasons(
    row: Mapping[str, Any],
    index: int,
    selected_index: int,
    recommended: bool,
) -> tuple[str, str]:
    recommendation_reason = text._safe_text(row.get("recommendation_reason") or "").strip()
    rejection_reason = text._safe_text(row.get("rejection_reason") or "").strip()
    if recommended:
        if index != selected_index or not recommendation_reason:
            _raise_value("selected candidate requires recommendation_reason")
        return recommendation_reason, rejection_reason
    if not rejection_reason:
        _raise_value("unselected candidate requires rejection_reason")
    return recommendation_reason, rejection_reason


def _parse_strict_analysis_row(
    row: object,
    candidate_count: int,
    selected_index: int,
    analysis: dict[int, dict[str, str]],
) -> int | None:
    if not isinstance(row, dict):
        _raise_value("candidate analysis rows must be objects")
    index = row.get("index")
    recommended = row.get("is_recommended")
    if not _is_non_bool_int(index) or not 0 <= index < candidate_count:
        _raise_value("candidate analysis index is invalid")
    if index in analysis:
        _raise_value("candidate analysis contains a duplicate index")
    if not isinstance(recommended, bool):
        _raise_value("candidate analysis is_recommended must be boolean")
    recommendation_reason, rejection_reason = _strict_row_reasons(
        row, index, selected_index, recommended
    )
    analysis[index] = {
        "recommendation_reason": recommendation_reason,
        "rejection_reason": rejection_reason,
    }
    return index if recommended else None


def _parse_strict_candidate_analysis(
    payload: dict,
    candidate_count: int | None,
) -> tuple[int, dict[int, dict[str, str]]]:
    count = _require_candidate_count(candidate_count)
    selected_index = _require_selected_index(payload.get("selected_route_index"), count)
    rows = payload.get("candidate_analysis")
    if not isinstance(rows, list) or len(rows) != count:
        _raise_value("candidate analysis must contain every candidate exactly once")

    analysis: dict[int, dict[str, str]] = {}
    recommended_indexes: list[int] = []
    for row in rows:
        recommended_index = _parse_strict_analysis_row(
            row, count, selected_index, analysis
        )
        if recommended_index is not None:
            recommended_indexes.append(recommended_index)
    if set(analysis) != set(range(count)) or recommended_indexes != [selected_index]:
        _raise_value(
            "candidate analysis recommendation does not match selected route"
        )
    return selected_index, analysis


def _parse_strict_advisor_selection(
    raw_recommendation: str,
    candidate_count: int,
) -> tuple[int, dict[int, dict[str, str]]]:
    route_tags = re.findall(r"\[ROUTE:(\d+)\]", raw_recommendation or "")
    if len(route_tags) != 1:
        _raise_value("route selection control marker must appear exactly once")
    chosen_index = int(route_tags[0])
    if not 0 <= chosen_index < candidate_count:
        _raise_value("route selection index is outside the candidate range")
    analysis_selected_index, candidate_analysis = parse_candidate_analysis(
        raw_recommendation,
        candidate_count=candidate_count,
        strict=True,
    )
    if analysis_selected_index != chosen_index:
        _raise_value("route selection markers disagree")
    return chosen_index, candidate_analysis


def _parse_lenient_advisor_selection(
    raw_recommendation: str,
    candidate_count: int,
) -> tuple[int, dict[int, dict[str, str]]]:
    chosen_index = 0
    route_tag_match = re.search(r"\[ROUTE:(\d+)\]", raw_recommendation or "")
    if route_tag_match:
        chosen_index = int(route_tag_match.group(1))
    analysis_selected_index, candidate_analysis = parse_candidate_analysis(
        raw_recommendation
    )
    if route_tag_match is None and analysis_selected_index is not None:
        chosen_index = analysis_selected_index
    if not 0 <= chosen_index < candidate_count:
        chosen_index = 0
    return chosen_index, candidate_analysis


def parse_advisor_selection(
    raw_recommendation: str,
    candidate_count: int,
    *,
    strict: bool = False,
) -> tuple[int, dict[int, dict[str, str]]]:
    """Parse route control data, with strict agent validation when requested.

    The default intentionally preserves REST route-zero fallback.
    ``strict=True`` raises on malformed, absent, mismatched, or out-of-range
    control data so the agent boundary can record a deterministic fallback.
    """

    if strict:
        return _parse_strict_advisor_selection(raw_recommendation, candidate_count)
    return _parse_lenient_advisor_selection(raw_recommendation, candidate_count)
