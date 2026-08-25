"""Grounded route-candidate evaluation and deterministic fallback choice."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, TypedDict

from app.services.mta.alerts import is_material_service_alert

_CURRENT_EVIDENCE_STATUS = "current"
_FALLBACK_REASON_PRIORITY = (
    "fewer_transfers",
    "less_walking",
    "lower_event_crowd_exposure",
    "fastest",
    "avoids_active_disruption",
    "accessibility",
    "coverage_gap",
    "reasonable_local_option",
    "meets_hard_constraints",
)


class CandidateDecisionEvaluation(TypedDict):
    supported_reason_codes: set[str]
    has_missing_branch: bool
    crowd_limitation_required: bool
    structured_reasons: dict[str, dict[str, Any]]


class FallbackCandidateDecision(TypedDict):
    entry: dict[str, Any]
    reason_code: str


class DominatedSelectionDecision(TypedDict):
    challenged: bool
    preference: str | None


def evaluate_candidate_decision(
    record: dict[str, Any], entry: dict[str, Any]
) -> CandidateDecisionEvaluation:
    """Evaluate which structured reasons the finalized candidate supports."""

    selected = _validated_selection(entry)
    if selected is None:
        return _empty_evaluation()

    alternatives = _eligible_alternatives(record, selected)
    supported = _base_supported_reasons(selected)

    missing_branch = _has_missing_branch(record, entry)
    if not _branch_comparison_is_complete(selected):
        supported.add("coverage_gap")
    else:
        _add_comparison_reasons(supported, selected, alternatives)
    return _decision_evaluation(
        supported,
        selected,
        alternatives,
        has_missing_branch=missing_branch,
    )


def _empty_evaluation() -> CandidateDecisionEvaluation:
    return {
        "supported_reason_codes": set(),
        "has_missing_branch": False,
        "crowd_limitation_required": False,
        "structured_reasons": {},
    }


def evaluate_dominated_selection(
    record: dict[str, Any], entry: dict[str, Any]
) -> DominatedSelectionDecision:
    """Detect only a clear preference-specific Pareto domination."""

    selected = _validated_selection(entry)
    preference = _explicit_routing_preference(selected) if selected else None
    if selected is None or preference is None:
        return {"challenged": False, "preference": preference}
    for alternative in _eligible_alternatives(record, selected):
        if _dominates_for_preference(selected, alternative, preference):
            return {"challenged": True, "preference": preference}
    return {"challenged": False, "preference": preference}


def _validated_selection(entry: dict[str, Any]) -> dict[str, Any] | None:
    selected = entry.get("digest")
    if not isinstance(selected, dict):
        return None
    return selected if selected.get("hard_constraints_satisfied") is True else None


def _eligible_alternatives(
    record: dict[str, Any], selected: dict[str, Any]
) -> list[dict[str, Any]]:
    alternatives: list[dict[str, Any]] = []
    for item in record.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidate = item.get("digest")
        if (
            isinstance(candidate, dict)
            and candidate is not selected
            and candidate.get("hard_constraints_satisfied") is True
        ):
            alternatives.append(candidate)
    return alternatives


def _base_supported_reasons(selected: dict[str, Any]) -> set[str]:
    supported = {"meets_hard_constraints"}
    if (
        selected.get("accessibility_required") is True
        and selected.get("accessibility_status") == "accessible"
    ):
        supported.add("accessibility")
    if _coverage_is_incomplete(selected):
        supported.add("coverage_gap")
    return supported


def _coverage_is_incomplete(selected: dict[str, Any]) -> bool:
    coverage = selected.get("evidence_coverage") or {}
    return isinstance(coverage, dict) and any(
        str(value) in {"partial", "stale", "unavailable", "unscanned"}
        for value in coverage.values()
    )


def _add_comparison_reasons(
    supported: set[str],
    selected: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> None:
    if _has_stage_a_factor(selected, "reasonable_local_option"):
        supported.add("reasonable_local_option")
    if alternatives:
        _add_comparative_reasons(supported, selected, alternatives)


def select_fallback_candidate(
    record: dict[str, Any],
) -> FallbackCandidateDecision | None:
    """Select a hard-valid fallback using the private deterministic ranking."""
    ranked = _ranked_fallback_entries(record)
    if not ranked:
        return None
    entry = min(ranked, key=lambda item: item[0])[1]
    supported = evaluate_candidate_decision(record, entry)["supported_reason_codes"]
    for reason_code in _FALLBACK_REASON_PRIORITY:
        if reason_code not in supported:
            continue
        if reason_code == "coverage_gap" and _branch_pool_exclusion_only(record):
            continue
        return {"entry": entry, "reason_code": reason_code}
    return None


def _ranked_fallback_entries(
    record: dict[str, Any],
) -> list[tuple[tuple[float, float, float, int], dict[str, Any]]]:
    scores = _fallback_scores(record)
    ranked: list[tuple[tuple[float, float, float, int], dict[str, Any]]] = []
    for entry in record.get("candidates") or []:
        if not isinstance(entry, dict):
            continue
        digest = entry.get("digest")
        if not _fallback_entry_is_valid(entry, digest):
            continue
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        score = scores.get(index)
        if score is None:
            continue
        ranked.append(
            (
                (
                    float(score["score"]),
                    float(score["total_minutes"]),
                    float(score["transfers"]),
                    index,
                ),
                entry,
            )
        )
    return ranked


def _fallback_scores(record: dict[str, Any]) -> dict[int, dict[str, Any]]:
    scores: dict[int, dict[str, Any]] = {}
    for row in record.get("scored") or []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        values = tuple(
            _numeric_factor(row.get(key))
            for key in ("score", "total_minutes", "transfers")
        )
        if all(value is not None for value in values):
            scores[index] = row
    return scores


def _fallback_entry_is_valid(entry: dict[str, Any], digest: object) -> bool:
    return bool(
        isinstance(digest, dict)
        and digest.get("hard_constraints_satisfied") is True
        and str(entry.get("candidate_id") or "").strip()
    )


def _add_comparative_reasons(
    supported: set[str],
    selected: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> None:
    _add_preference_reasons(supported, selected, alternatives)
    _add_disruption_reason(supported, selected, alternatives)
    _add_crowd_reason(supported, selected, alternatives)


def _add_preference_reasons(
    supported: set[str],
    selected: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> None:
    explicit = _explicit_routing_preference(selected)
    crowd_active = _crowd_preference_is_active(selected)
    if explicit is None and not crowd_active:
        if _strict_minimum(selected, alternatives, "duration_minutes"):
            supported.add("fastest")
        if _supports_routing_preference(selected, "LESS_WALKING") and _strict_minimum(
            selected, alternatives, "walking_minutes"
        ):
            supported.add("less_walking")
        if _supports_routing_preference(
            selected, "FEWER_TRANSFERS"
        ) and _strict_minimum(selected, alternatives, "transfers"):
            supported.add("fewer_transfers")
    elif explicit == "LESS_WALKING" and _strict_minimum(
        selected, alternatives, "walking_minutes"
    ):
        supported.add("less_walking")
    elif explicit == "FEWER_TRANSFERS" and _strict_minimum(
        selected, alternatives, "transfers"
    ):
        supported.add("fewer_transfers")


def _add_disruption_reason(
    supported: set[str],
    selected: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> None:
    if not _crowd_preference_is_active(selected) and _condition_count(selected) < min(
        map(_condition_count, alternatives)
    ):
        supported.add("avoids_active_disruption")


def _add_crowd_reason(
    supported: set[str],
    selected: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> None:
    if not _crowd_preference_is_active(selected):
        return
    selected_risk = _crowd_risk(selected)
    alternative_risks = [_crowd_risk(item) for item in alternatives]
    if (
        _crowd_evidence_is_current(selected)
        and all(_crowd_evidence_is_current(item) for item in alternatives)
        and selected_risk is not None
        and all(item is not None for item in alternative_risks)
        and selected_risk < min(item for item in alternative_risks if item is not None)
    ):
        supported.add("lower_event_crowd_exposure")


def _decision_evaluation(
    supported: set[str],
    selected: dict[str, Any],
    alternatives: list[dict[str, Any]],
    *,
    has_missing_branch: bool,
) -> CandidateDecisionEvaluation:
    crowd_limitation_required = (
        _crowd_preference_is_active(selected)
        and "lower_event_crowd_exposure" not in supported
    )
    return {
        "supported_reason_codes": supported,
        "has_missing_branch": has_missing_branch,
        "crowd_limitation_required": crowd_limitation_required,
        "structured_reasons": _structured_reasons(
            supported,
            selected,
            alternatives,
            crowd_limitation_required=crowd_limitation_required,
        ),
    }


def _structured_reasons(
    supported: set[str],
    selected: dict[str, Any],
    alternatives: list[dict[str, Any]],
    *,
    crowd_limitation_required: bool,
) -> dict[str, dict[str, Any]]:
    reasons = {code: {"code": code} for code in supported}
    if "fastest" in reasons:
        difference = _comparison_difference(selected, alternatives, "duration_minutes")
        if difference is not None:
            reasons["fastest"]["difference_seconds"] = round(difference * 60)
    if "fewer_transfers" in reasons:
        difference = _comparison_difference(selected, alternatives, "transfers")
        if difference is not None:
            reasons["fewer_transfers"]["transfer_difference"] = round(difference)
    if "lower_event_crowd_exposure" in reasons:
        reasons["lower_event_crowd_exposure"].update(
            event_count=len(selected.get("event_or_crowd_impacts") or []),
            provider_status=_crowd_evidence_status(selected),
        )
    if crowd_limitation_required:
        for reason in reasons.values():
            reason["crowd_evidence_status"] = _crowd_evidence_status(selected)
    return reasons


def _comparison_difference(
    selected: dict[str, Any], alternatives: list[dict[str, Any]], key: str
) -> float | None:
    selected_value = _numeric_factor(selected.get(key))
    alternative_values = [_numeric_factor(item.get(key)) for item in alternatives]
    if (
        selected_value is None
        or not alternative_values
        or any(value is None for value in alternative_values)
    ):
        return None
    return max(
        0.0,
        min(value for value in alternative_values if value is not None)
        - selected_value,
    )


def _crowd_evidence_status(selected: dict[str, Any]) -> str:
    status = str(selected.get("event_evidence_status") or "").strip().casefold()
    if status:
        return status
    coverage = selected.get("evidence_coverage")
    if isinstance(coverage, dict):
        return str(coverage.get("events") or "unavailable").strip().casefold()
    return "unavailable"


def _supports_routing_preference(selected: dict[str, Any], preference: str) -> bool:
    preferences = selected.get("soft_preferences")
    if not isinstance(preferences, dict):
        return False
    active = str(preferences.get("routing_preference") or "").strip().upper()
    return active == preference


def _explicit_routing_preference(selected: dict[str, Any]) -> str | None:
    preferences = selected.get("soft_preferences")
    if not isinstance(preferences, dict):
        return None
    source = str(preferences.get("routing_preference_source") or "").strip()
    if source not in {"current_turn", "persisted_rider"}:
        return None
    preference = str(preferences.get("routing_preference") or "").strip().upper()
    return preference if preference in {"FEWER_TRANSFERS", "LESS_WALKING"} else None


def _dominates_for_preference(
    selected: dict[str, Any],
    alternative: dict[str, Any],
    preference: str,
) -> bool:
    primary_key = {
        "LESS_WALKING": "walking_minutes",
        "FEWER_TRANSFERS": "transfers",
    }.get(preference)
    if primary_key is None or not _condition_factors_are_comparable(
        selected, alternative
    ):
        return False
    keys = ("duration_minutes", "walking_minutes", "transfers")
    selected_values = [_numeric_factor(selected.get(key)) for key in keys]
    alternative_values = [_numeric_factor(alternative.get(key)) for key in keys]
    if any(value is None for value in (*selected_values, *alternative_values)):
        return False
    if (
        _dominance_condition_count(alternative)
        > _dominance_condition_count(selected)
        or not _condition_impacts_are_no_worse(selected, alternative)
    ):
        return False
    if any(
        alternative_value > selected_value
        for alternative_value, selected_value in zip(
            alternative_values, selected_values
        )
    ):
        return False
    primary_index = keys.index(primary_key)
    return alternative_values[primary_index] < selected_values[primary_index]


def _condition_factors_are_comparable(
    selected: dict[str, Any], alternative: dict[str, Any]
) -> bool:
    keys = ("official_service_impacts", "confirmed_incident_impacts")
    return all(
        isinstance(candidate.get(key), list)
        for candidate in (selected, alternative)
        for key in keys
    )


def _condition_impacts_are_no_worse(
    selected: dict[str, Any], alternative: dict[str, Any]
) -> bool:
    for key in ("official_service_impacts", "confirmed_incident_impacts"):
        selected_impacts = _condition_fingerprints(
            _dominance_condition_values(key, selected.get(key))
        )
        alternative_impacts = _condition_fingerprints(
            _dominance_condition_values(key, alternative.get(key))
        )
        if (
            selected_impacts is None
            or alternative_impacts is None
            or not alternative_impacts <= selected_impacts
        ):
            return False
    return True


def _dominance_condition_values(key: str, value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    if key != "official_service_impacts":
        return list(value)
    return [item for item in value if is_material_service_alert(item)]


def _dominance_condition_count(candidate: dict[str, Any]) -> int:
    official = candidate.get("official_service_impacts")
    confirmed = candidate.get("confirmed_incident_impacts")
    official_count = (
        sum(1 for item in official if is_material_service_alert(item))
        if isinstance(official, list)
        else 0
    )
    confirmed_count = len(confirmed) if isinstance(confirmed, list) else 0
    return official_count + confirmed_count


def _condition_fingerprints(value: object) -> set[str] | None:
    if not isinstance(value, list):
        return None
    fingerprints: set[str] = set()
    for item in value:
        fingerprint = _condition_fingerprint(item)
        if fingerprint is None:
            return None
        fingerprints.add(fingerprint)
    return fingerprints


def _condition_fingerprint(value: object) -> str | None:
    if isinstance(value, str):
        normalized = " ".join(value.split()).casefold()
    elif isinstance(value, dict) and value:
        try:
            normalized = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not normalized or len(normalized) > 512:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _crowd_preference_is_active(selected: dict[str, Any]) -> bool:
    preferences = selected.get("soft_preferences")
    return isinstance(preferences, dict) and preferences.get("avoid_crowds") is True


def _crowd_evidence_is_current(selected: dict[str, Any]) -> bool:
    coverage = selected.get("evidence_coverage")
    if isinstance(coverage, dict) and "events" in coverage:
        return coverage.get("events") == _CURRENT_EVIDENCE_STATUS
    status = str(selected.get("event_evidence_status") or "").strip().casefold()
    return status in {"available", "no_relevant_events", "complete"}


def _branch_comparison_is_complete(candidate: dict[str, Any]) -> bool:
    rows = candidate.get("branch_coverage")
    if not isinstance(rows, list) or not rows:
        return True
    return all(
        isinstance(row, dict)
        and str(row.get("status") or "").strip().casefold() in {"available", "excluded"}
        for row in rows
    )


def _strict_minimum(
    selected: dict[str, Any], alternatives: list[dict[str, Any]], key: str
) -> bool:
    value = _numeric_factor(selected.get(key))
    alternative_values = [_numeric_factor(item.get(key)) for item in alternatives]
    if value is None or any(item is None for item in alternative_values):
        return False
    return value < min(item for item in alternative_values if item is not None)


def _numeric_factor(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _condition_count(candidate: dict[str, Any]) -> int:
    return sum(
        len(candidate.get(key) or [])
        for key in ("official_service_impacts", "confirmed_incident_impacts")
        if isinstance(candidate.get(key), list)
    )


def _crowd_risk(candidate: dict[str, Any]) -> float | None:
    impacts = candidate.get("event_or_crowd_impacts") or []
    if not isinstance(impacts, list):
        return None
    values: list[float] = []
    for impact in impacts:
        if not isinstance(impact, dict) or impact.get("scoring_authorized") is not True:
            return None
        risk = _numeric_factor(impact.get("risk_score"))
        if risk is None:
            return None
        values.append(risk)
    return sum(values)


def _has_stage_a_factor(candidate: dict[str, Any], code: str) -> bool:
    factors = candidate.get("stage_a_factors")
    return isinstance(factors, list) and any(
        isinstance(item, dict)
        and item.get("code") == code
        and item.get("status") == "validated"
        for item in factors
    )


def _has_missing_branch(record: dict[str, Any], entry: dict[str, Any]) -> bool:
    digest = entry.get("digest")
    rows = digest.get("branch_coverage") if isinstance(digest, dict) else None
    if not isinstance(rows, list):
        rows = record.get("branch_coverage")
    return isinstance(rows, list) and any(
        isinstance(row, dict)
        and str(row.get("status") or "").strip().casefold() in {"unavailable", "failed"}
        for row in rows
    )


def _branch_pool_exclusion_only(record: dict[str, Any]) -> bool:
    rows = record.get("branch_coverage")
    if not isinstance(rows, list):
        return False
    statuses = {
        str(row.get("status") or "").strip().casefold()
        for row in rows
        if isinstance(row, dict)
    }
    return "excluded" in statuses and statuses <= {"available", "excluded"}


__all__ = (
    "CandidateDecisionEvaluation",
    "DominatedSelectionDecision",
    "FallbackCandidateDecision",
    "evaluate_candidate_decision",
    "evaluate_dominated_selection",
    "select_fallback_candidate",
)
