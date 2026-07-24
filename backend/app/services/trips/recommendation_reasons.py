"""Deterministic recommendation facts for canonical itineraries.

These compact records describe only facts the scorer already knows.  They are
deliberately separate from the advisor's prose so a model cannot introduce an
unsupported duration, transfer, or disruption claim into an itinerary.
"""

from __future__ import annotations

from typing import Any, Iterable


def build_recommendation_reasons(
    selected_score: dict[str, Any],
    alternative_scores: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return supported, deterministic facts about the selected candidate."""
    alternatives = [score for score in alternative_scores if isinstance(score, dict)]
    if not alternatives:
        return []

    reasons: list[dict[str, Any]] = []
    selected_minutes = _nonnegative_int(selected_score.get("total_minutes"))
    alternative_minutes = [
        _nonnegative_int(score.get("total_minutes")) for score in alternatives
    ]
    next_best_minutes = min(alternative_minutes, default=selected_minutes)
    if selected_minutes <= next_best_minutes:
        reasons.append(
            {
                "code": "fastest",
                "difference_seconds": max(0, next_best_minutes - selected_minutes) * 60,
            }
        )

    selected_transfers = _nonnegative_int(selected_score.get("transfers"))
    best_alternative_transfers = min(
        (_nonnegative_int(score.get("transfers")) for score in alternatives),
        default=selected_transfers,
    )
    if selected_transfers < best_alternative_transfers:
        reasons.append(
            {
                "code": "fewer_transfers",
                "transfer_difference": best_alternative_transfers - selected_transfers,
            }
        )

    selected_alerts = _nonnegative_int(selected_score.get("alert_count"))
    if any(_nonnegative_int(score.get("alert_count")) > selected_alerts for score in alternatives):
        reasons.append({"code": "avoids_active_disruption"})

    return reasons


def format_recommendation_reason(reason: object) -> str | None:
    """Format one supported fact for legacy string consumers only."""
    if not isinstance(reason, dict):
        return None
    code = reason.get("code")
    if code == "fastest":
        seconds = _nonnegative_int(reason.get("difference_seconds"))
        if seconds >= 60:
            return f"About {round(seconds / 60)} min faster than the next option."
        return "Fastest available route."
    if code == "fewer_transfers":
        difference = _nonnegative_int(reason.get("transfer_difference"))
        if difference:
            unit = "transfer" if difference == 1 else "transfers"
            return f"Uses {difference} fewer {unit}."
    if code == "avoids_active_disruption":
        return "Avoids active service alerts on another option."
    return None


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
