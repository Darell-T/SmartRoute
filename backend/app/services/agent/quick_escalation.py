"""Classify Quick-mode edge conditions without changing its model."""

from __future__ import annotations

from typing import Any, Literal, Protocol

QuickEscalationReason = Literal[
    "unresolved_place",
    "ambiguous_station_or_destination",
    "mandatory_constraints_unsatisfied",
    "conflicting_mandatory_evidence",
    "effectively_tied_final_scores",
    "required_tool_failure",
]


class ToolResultLike(Protocol):
    ok: bool
    data: Any
    error: str | None


_PLACE_FAILURE_MARKERS = (
    "could not resolve",
    "could not find",
    "need your current location",
)
_CONSTRAINT_FAILURE_MARKERS = (
    "no transit modes left",
    "no transit route found",
    "mandatory constraint",
)


def effectively_tied_scores(scored: list[dict], *, tolerance: float = 1.0) -> bool:
    values = sorted(float(row.get("score") or 0.0) for row in scored)
    return len(values) > 1 and values[1] - values[0] <= max(0.0, tolerance)


def reason_for_tool_result(
    tool_name: str,
    result: ToolResultLike,
    *,
    required: bool,
) -> QuickEscalationReason | None:
    data = result.data if isinstance(result.data, dict) else {}
    if data.get("quick_escalation_reason") == "effectively_tied_final_scores":
        return "effectively_tied_final_scores"
    if data.get("conflicting_mandatory_evidence") is True:
        return "conflicting_mandatory_evidence"
    if data.get("source_status") == "stop_not_resolved":
        return "ambiguous_station_or_destination"
    if isinstance(data.get("ambiguity"), list) and len(data["ambiguity"]) > 1:
        return "ambiguous_station_or_destination"
    if result.ok:
        return None

    error = str(result.error or "").casefold()
    if any(marker in error for marker in _PLACE_FAILURE_MARKERS):
        return "unresolved_place"
    if any(marker in error for marker in _CONSTRAINT_FAILURE_MARKERS):
        return "mandatory_constraints_unsatisfied"
    if required and tool_name in {"plan_trip", "poi_search", "lookup_arrivals"}:
        return "required_tool_failure"
    return None
