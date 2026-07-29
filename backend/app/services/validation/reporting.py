"""Bridge deterministic replay output into validation metrics.

This module evaluates only manifest expectations and observed comparison data.
It does not infer ground truth from model prose and never treats fixture
results as live performance evidence.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from app.services.validation.metrics import aggregate_metrics
from app.services.validation.replay import ReplayScenario


def _association_matches(report: Mapping[str, Any], scenario: ReplayScenario) -> bool | None:
    expected = scenario.expected.get("expected_association")
    if not isinstance(expected, Mapping):
        return None
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    rows = evidence.get("association_diagnostics")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
        return False
    actual = rows[0]
    if list(actual.get("candidate_route_ids") or ()) != list(expected.get("candidate_route_ids") or ()):
        return False
    if list(actual.get("modes") or ()) != list(expected.get("modes") or ()):
        return False
    if actual.get("impact_scope") != expected.get("impact_scope"):
        return False
    relevance = expected.get("subway_relevance")
    if relevance is not None:
        actual_relevance = actual.get("relevance_by_mode")
        if not isinstance(actual_relevance, Mapping) or actual_relevance.get("subway") != relevance:
            return False
    return True


def _deduplication_matches(report: Mapping[str, Any], scenario: ReplayScenario) -> bool | None:
    expected = scenario.expected.get("deduplication_correct")
    if not isinstance(expected, bool):
        return None
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    expected_count = scenario.expected.get("expected_incident_count")
    expected_sources = scenario.expected.get("expected_source_counts")
    return bool(
        evidence.get("incident_count") == expected_count
        and isinstance(expected_sources, Mapping)
        and dict(evidence.get("source_counts") or {}) == dict(expected_sources)
    ) is expected


def _empty_scan_matches(report: Mapping[str, Any], scenario: ReplayScenario) -> bool | None:
    expected = scenario.expected.get("empty_scan_correct")
    if not isinstance(expected, bool):
        return None
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    observed = (
        evidence.get("incident_count") == 0
        and report.get("scan_status") == scenario.expected.get("scan_status", "complete")
    )
    return observed is expected


def _source_effects(ablation_report: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    rows = ablation_report.get("ablations")
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") != "complete":
            continue
        source = row.get("source_contribution")
        effect = row.get("source_effect")
        if isinstance(source, str) and isinstance(effect, str):
            result[source] = effect
    return result


def _measured_ms(value: object) -> int | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    # The metrics schema intentionally stores bounded integer milliseconds.
    # Preserve a real sub-millisecond sample as 1 ms instead of rounding it to
    # a misleading zero-duration operation.
    return 0 if parsed == 0 else max(1, math.ceil(parsed))


def fixture_metric_record(
    report: Mapping[str, Any],
    scenario: ReplayScenario,
    ablation_report: Mapping[str, Any],
) -> dict[str, Any]:
    comparison = report.get("comparison") if isinstance(report.get("comparison"), Mapping) else {}
    timings = (
        comparison.get("local_replay_timings_ms")
        if isinstance(comparison.get("local_replay_timings_ms"), Mapping)
        else {}
    )
    return {
        "scenario_id": scenario.scenario_id,
        "evidence_kind": "deterministic_fixture",
        "expected_route_change": scenario.expected.get("route_should_change"),
        "route_changed": comparison.get("route_changed"),
        "matched_expectation": comparison.get("matched_expectation"),
        "known_relevant_disruption": scenario.expected.get("known_relevant_disruption"),
        "incident_association_correct": _association_matches(report, scenario),
        "deduplication_correct": _deduplication_matches(report, scenario),
        "empty_scan_correct": _empty_scan_matches(report, scenario),
        "baseline_latency_ms": _measured_ms(timings.get("baseline_construction_and_parse")),
        "intelligence_latency_ms": _measured_ms(
            timings.get("intelligence_evidence_payload_and_parse")
        ),
        "source_effects": _source_effects(ablation_report),
    }


def build_fixture_validation_results(
    reports: list[Mapping[str, Any]],
    scenarios: list[ReplayScenario],
    ablation_reports: list[Mapping[str, Any]],
) -> dict[str, Any]:
    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    ablation_by_id = {
        str(report.get("scenario_id")): report for report in ablation_reports
    }
    records = [
        fixture_metric_record(
            report,
            scenario_by_id[str(report.get("scenario_id"))],
            ablation_by_id[str(report.get("scenario_id"))],
        )
        for report in reports
    ]
    return {
        "schema_version": 1,
        "evidence_scope": "deterministic_fixture",
        "claim_boundary": (
            "Recorded advisor transcripts validate deterministic payload and "
            "selection-contract behavior; they do not prove autonomous advisor "
            "accuracy, causal route improvement, or real-world travel-time gains."
        ),
        "scenario_records": records,
        "metrics": aggregate_metrics(records)["deterministic_fixture"],
    }
