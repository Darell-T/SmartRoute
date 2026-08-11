"""Canonical, server-owned route-selection decision records."""

from __future__ import annotations

from typing import Literal, TypedDict

SelectionReason = Literal[
    "lowest_final_score",
    "hard_constraint",
    "advisor_tiebreak",
    "outer_agent_selection",
]


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
) -> RouteSelectionDecision:
    penalties: list[RoutePenalty] = []
    transfer_penalty = max(0, int(selected_score.get("transfers") or 0)) * 4
    if transfer_penalty:
        penalties.append(
            {
                "source": "transfers",
                "amount": float(transfer_penalty),
                "reason": "transfer cost",
            }
        )
    alert_penalty = max(0, int(selected_score.get("alert_count") or 0)) * 8
    if alert_penalty:
        penalties.append(
            {
                "source": "mta_alerts",
                "amount": float(alert_penalty),
                "reason": "active route alert",
            }
        )
    event_penalty = max(0.0, float(selected_score.get("event_crowd_penalty") or 0))
    if event_penalty:
        penalties.append(
            {
                "source": "crowd_events",
                "amount": event_penalty,
                "reason": "relevant event crowd exposure",
            }
        )

    constraints = ["at_least_one_transit_mode"]
    if excluded_modes:
        constraints.append("excluded_modes:" + ",".join(sorted(excluded_modes)))
    if arrival_by:
        constraints.append("arrival_by")
    if avoid_crowds and event_evidence_status in {"available", "no_relevant_events"}:
        constraints.append("crowd_evidence_considered")
    elif avoid_crowds and event_evidence_status == "partial":
        constraints.append("crowd_evidence_partial")

    evidence_ids = sorted(
        {
            str(impact.get("source_class") or "structured")
            + ":"
            + str(impact.get("event_id"))
            for impact in event_impacts
            if int(impact.get("route_index", -1)) == selected_index
            and str(impact.get("event_id") or "").strip()
        }
    )
    return {
        "selected_candidate_index": selected_index,
        "selected_candidate_id": selected_candidate_id,
        "base_score": float(selected_score.get("total_minutes") or 0),
        "final_score": float(selected_score.get("score") or 0),
        "hard_constraints_satisfied": constraints,
        "penalties": penalties,
        "selection_reason": selection_reason,
        "evidence_ids": evidence_ids,
    }
