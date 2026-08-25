"""Canonical persisted record for a completed route-selection decision."""

from __future__ import annotations

from typing import Literal, TypedDict

from app.services.trips import scoring

SelectionReason = Literal[
    "lowest_final_score",
    "hard_constraint",
    "advisor_tiebreak",
    "outer_agent_selection",
    "deterministic_fallback",
]
SelectionSource = Literal["model", "deterministic_fallback"]


class RoutePenalty(TypedDict):
    source: str
    amount: float
    reason: str


class RouteSelectionDecision(TypedDict):
    selected_candidate_index: int
    selected_candidate_id: str
    base_score: float
    final_score: float
    hard_constraints_satisfied: list[str]
    penalties: list[RoutePenalty]
    selection_reason: SelectionReason
    reason_code: str | None
    selection_source: SelectionSource
    evidence_ids: list[str]


def build_route_selection_decision(
    *,
    selected_index: int,
    selected_candidate_id: str,
    selected_score: dict,
    selection_reason: SelectionReason,
    excluded_modes: set[str],
    arrival_by: bool,
    avoid_crowds: bool,
    event_evidence_status: str,
    event_impacts: list[dict],
    selection_source: SelectionSource = "model",
    reason_code: str | None = None,
) -> RouteSelectionDecision:
    penalties = _route_penalties(selected_score)
    constraints = _constraint_facts(
        excluded_modes,
        arrival_by=arrival_by,
        avoid_crowds=avoid_crowds,
        event_evidence_status=event_evidence_status,
    )
    evidence_ids = _selected_evidence_ids(event_impacts, selected_index)
    return {
        "selected_candidate_index": selected_index,
        "selected_candidate_id": selected_candidate_id,
        "base_score": float(selected_score.get("total_minutes") or 0),
        "final_score": float(selected_score.get("score") or 0),
        "hard_constraints_satisfied": constraints,
        "penalties": penalties,
        "selection_reason": selection_reason,
        "reason_code": reason_code,
        "selection_source": selection_source,
        "evidence_ids": evidence_ids,
    }


def _route_penalties(selected_score: dict) -> list[RoutePenalty]:
    penalty_specs = (
        (
            "transfers",
            max(0, int(selected_score.get("transfers") or 0)) * 4,
            "transfer cost",
        ),
        (
            "mta_alerts",
            scoring.alert_penalty_from_score(selected_score),
            "active route alert",
        ),
        (
            "confirmed_incidents",
            max(0, int(selected_score.get("incident_count") or 0)) * 12,
            "confirmed route incident",
        ),
        (
            "vehicle_signals",
            max(0, int(selected_score.get("vehicle_signal_count") or 0)) * 4,
            "possible vehicle delay signal",
        ),
        (
            "crowd_events",
            max(0.0, float(selected_score.get("event_crowd_penalty") or 0)),
            "relevant event crowd exposure",
        ),
    )
    return [
        {"source": source, "amount": float(amount), "reason": reason}
        for source, amount, reason in penalty_specs
        if amount
    ]


def _constraint_facts(
    excluded_modes: set[str],
    *,
    arrival_by: bool,
    avoid_crowds: bool,
    event_evidence_status: str,
) -> list[str]:
    constraints = ["at_least_one_transit_mode"]
    if excluded_modes:
        constraints.append("excluded_modes:" + ",".join(sorted(excluded_modes)))
    if arrival_by:
        constraints.append("arrival_by")
    if avoid_crowds and event_evidence_status in {"available", "no_relevant_events"}:
        constraints.append("crowd_evidence_considered")
    elif avoid_crowds and event_evidence_status == "partial":
        constraints.append("crowd_evidence_partial")
    return constraints


def _selected_evidence_ids(event_impacts: list[dict], selected_index: int) -> list[str]:
    return sorted(
        {
            str(impact.get("source_class") or "structured")
            + ":"
            + str(impact.get("event_id"))
            for impact in event_impacts
            if int(impact.get("route_index", -1)) == selected_index
            and str(impact.get("event_id") or "").strip()
        }
    )


__all__ = (
    "RoutePenalty",
    "RouteSelectionDecision",
    "SelectionReason",
    "SelectionSource",
    "build_route_selection_decision",
)
