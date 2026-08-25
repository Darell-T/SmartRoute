"""Offline trip-route integration for privacy-safe evaluation-shadow records."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from evaluation.route_intelligence import advisor_context, advisor
from evaluation.route_intelligence.shadow import (
    CounterfactualBaselineEvaluation,
    EvidenceKind,
    JsonlShadowSink,
    NullShadowSink,
    ShadowEvaluationStatus,
    build_shadow_record,
    execute_counterfactual_shadow,
)


_TRUE = {"1", "true", "yes", "on"}
_SOURCE_LABELS = {
    "511ny": "cached_511ny",
    "grok_x": "grok_x",
    "x": "grok_x",
    "x_search": "grok_x",
    "grok_web": "grok_web",
    "web": "grok_web",
    "web_search": "grok_web",
}


def shadow_enabled() -> bool:
    enabled = os.getenv("EVALUATION_SHADOW_ENABLED", "false").strip().casefold() in _TRUE
    path = (os.getenv("EVALUATION_SHADOW_LOG_PATH") or "").strip()
    return enabled and Path(path).suffix.casefold() == ".jsonl"


def shadow_timeout_seconds() -> str:
    # The shared executor owns numeric parsing and the hard maximum.
    return os.getenv("EVALUATION_SHADOW_TIMEOUT_SECONDS", "2.0")


def shadow_sample_rate() -> float:
    try:
        configured = float(os.getenv("EVALUATION_SHADOW_SAMPLE_RATE", "0.05"))
    except ValueError:
        return 0.05
    return min(1.0, max(0.0, configured))


def shadow_sampled() -> bool:
    return secrets.randbelow(10_000) < round(shadow_sample_rate() * 10_000)


def shadow_sink() -> JsonlShadowSink | NullShadowSink:
    path = (os.getenv("EVALUATION_SHADOW_LOG_PATH") or "").strip()
    if not path:
        return NullShadowSink()
    candidate = Path(path)
    if candidate.suffix.casefold() != ".jsonl":
        return NullShadowSink()
    return JsonlShadowSink(candidate)


def parse_counterfactual_baseline(
    raw_recommendation: str, candidate_count: int
) -> CounterfactualBaselineEvaluation:
    """Reject a model fallback as a completed counterfactual decision."""

    route = re.search(r"\[ROUTE:(\d+)\]", raw_recommendation or "")
    analysis_selected, analysis = advisor_context.parse_candidate_analysis(raw_recommendation or "")
    selected, _ = advisor_context.parse_advisor_selection(
        raw_recommendation or "", candidate_count
    )
    if (
        route is None
        or int(route.group(1)) != selected
        or analysis_selected != selected
        or set(analysis) != set(range(candidate_count))
    ):
        return CounterfactualBaselineEvaluation(None, ShadowEvaluationStatus.FAILED)
    return CounterfactualBaselineEvaluation(
        f"candidate-{selected}", ShadowEvaluationStatus.COMPLETE
    )


def safe_candidate_summaries(route_candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for row in route_candidates:
        breakdown = row.get("score_breakdown")
        breakdown = breakdown if isinstance(breakdown, Mapping) else {}
        summaries.append(
            {
                "id": row.get("id"),
                "lines": breakdown.get("transit_lines"),
                "total_minutes": row.get("total_minutes"),
                "transfers": breakdown.get("transfers"),
                "selection_score": row.get("selection_score"),
            }
        )
    return summaries


def safe_source_counts(
    *,
    incidents: Sequence[Mapping[str, Any]],
    alert_count: int,
    stalled_train_count: int,
    stalled_bus_count: int,
    ticketmaster_event_count: int = 0,
) -> dict[str, int]:
    counts = {
        "mta_alerts": max(0, alert_count),
        "stalled_subway": max(0, stalled_train_count),
        "stalled_bus": max(0, stalled_bus_count),
        "ticketmaster": max(0, ticketmaster_event_count),
    }
    for incident in incidents:
        raw = incident.get("source") or ""
        for token in re.split(r"[,|]", str(raw)):
            label = _SOURCE_LABELS.get(token.strip().casefold())
            if label:
                counts[label] = counts.get(label, 0) + 1
    return counts


async def run_trip_shadow(
    displayed_result: dict[str, Any],
    *,
    baseline_evaluator: Callable[
        [], Awaitable[CounterfactualBaselineEvaluation | Mapping[str, object]]
    ],
    production_route_id: str,
    production_status: ShadowEvaluationStatus,
    candidate_summaries: Sequence[Mapping[str, Any]],
    source_counts: Mapping[str, int],
    incident_count: int,
    scan_status: str,
    snapshot_status: str,
    intelligence_latency_ms: int,
) -> dict[str, Any]:
    """Evaluate a counterfactual after the displayed result is final.

    The shared executor guarantees object identity on disabled, timeout,
    evaluator failure, record failure, and sink failure paths.
    """

    def record_factory(evaluation, baseline_latency_ms, shadow_overhead_ms):
        return build_shadow_record(
            evidence_kind=EvidenceKind.LIVE_SHADOW,
            advisor_identity=advisor.advisor_identity(),
            production_intelligence_route_id=production_route_id,
            production_intelligence_status=production_status,
            counterfactual_baseline_route_id=evaluation.selected_route_id,
            counterfactual_baseline_status=evaluation.status,
            candidate_summaries=candidate_summaries,
            source_counts=source_counts,
            incident_count=incident_count,
            scan_status=scan_status,
            ny511_snapshot_status=snapshot_status,
            intelligence_latency_ms=intelligence_latency_ms,
            baseline_latency_ms=baseline_latency_ms,
            shadow_overhead_ms=shadow_overhead_ms,
        )

    outcome = await execute_counterfactual_shadow(
        displayed_result,
        enabled=shadow_enabled() and shadow_sampled(),
        baseline_evaluator=baseline_evaluator,
        timeout_s=shadow_timeout_seconds(),
        record_factory=record_factory,
        sink=shadow_sink(),
    )
    return outcome.displayed_result
