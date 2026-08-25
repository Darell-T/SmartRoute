"""Deterministic replay and human-reviewed evaluation-shadow metrics.

This module deliberately does not infer real-world benefit from fixtures and
does not score unclassified live disagreements as correct or incorrect.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping
from uuid import UUID

from evaluation.route_intelligence.shadow import EvidenceKind, ReviewClassification, SourceContribution


class SourceEffect(str, Enum):
    CHANGED_ROUTE = "changed_route"
    CHANGED_SCORE = "changed_score"
    CHANGED_CONFIDENCE = "changed_confidence"
    CHANGED_EXPLANATION_ONLY = "changed_explanation_only"
    HAD_NO_EFFECT = "had_no_effect"


def _enum(value: object, enum_type: type[Enum]) -> Enum | None:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))  # type: ignore[call-arg]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ScenarioMetricRecord:
    """Small result contract produced by comparison/replay code.

    There are no route geometries, stop names, prompts, user inputs, raw
    incident text, or provider responses in this boundary.
    """

    scenario_id: str | None
    observation_id: str | None
    evidence_kind: EvidenceKind
    expected_route_change: bool | None
    route_changed: bool | None
    matched_expectation: bool | None
    known_relevant_disruption: bool | None
    incident_association_correct: bool | None
    deduplication_correct: bool | None
    empty_scan_correct: bool | None
    baseline_latency_ms: int | None
    intelligence_latency_ms: int | None
    shadow_overhead_ms: int | None
    source_effects: tuple[tuple[SourceContribution, SourceEffect], ...]
    classification: ReviewClassification | None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ScenarioMetricRecord":
        kind = _enum(raw.get("evidence_kind"), EvidenceKind)
        if kind is None:
            raise ValueError("scenario metric record requires known evidence_kind")
        scenario_id = str(raw.get("scenario_id") or "").strip() or None
        observation_id = str(raw.get("observation_id") or "").strip() or None
        if kind is EvidenceKind.DETERMINISTIC_FIXTURE and not scenario_id:
            raise ValueError("fixture metric record requires scenario_id")
        if kind is EvidenceKind.LIVE_SHADOW:
            try:
                parsed_observation = UUID(observation_id or "")
            except (ValueError, AttributeError) as exc:
                raise ValueError("live shadow metric record requires UUIDv4 observation_id") from exc
            if parsed_observation.version != 4:
                raise ValueError("live shadow metric record requires UUIDv4 observation_id")
            observation_id = str(parsed_observation)

        def boolean(name: str) -> bool | None:
            value = raw.get(name)
            return value if isinstance(value, bool) else None

        source_effects: list[tuple[SourceContribution, SourceEffect]] = []
        raw_effects = raw.get("source_effects")
        if isinstance(raw_effects, Mapping):
            for raw_source, raw_effect in raw_effects.items():
                source = _enum(raw_source, SourceContribution)
                effect = _enum(raw_effect, SourceEffect)
                if source is not None and effect is not None:
                    source_effects.append((source, effect))
        classification = _enum(raw.get("classification"), ReviewClassification)

        def latency(name: str) -> int | None:
            value = raw.get(name)
            if isinstance(value, bool):
                return None
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if 0 <= parsed <= 86_400_000 else None

        return cls(
            scenario_id=scenario_id,
            observation_id=observation_id,
            evidence_kind=kind,
            expected_route_change=boolean("expected_route_change"),
            route_changed=boolean("route_changed"),
            matched_expectation=boolean("matched_expectation"),
            known_relevant_disruption=boolean("known_relevant_disruption"),
            incident_association_correct=boolean("incident_association_correct"),
            deduplication_correct=boolean("deduplication_correct"),
            empty_scan_correct=boolean("empty_scan_correct"),
            baseline_latency_ms=latency("baseline_latency_ms"),
            intelligence_latency_ms=latency("intelligence_latency_ms"),
            shadow_overhead_ms=latency("shadow_overhead_ms"),
            source_effects=tuple(sorted(source_effects, key=lambda pair: pair[0].value)),
            classification=classification,
        )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _optional_accuracy(rows: Iterable[ScenarioMetricRecord], field: str) -> dict[str, int | float | None]:
    values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    passed = sum(value is True for value in values)
    return {"passed": passed, "total": len(values), "rate": _rate(passed, len(values))}


def _fixture_metrics(rows: list[ScenarioMetricRecord]) -> dict[str, Any]:
    expected_change = [row for row in rows if row.expected_route_change is True]
    expected_no_change = [row for row in rows if row.expected_route_change is False]
    matched = [row for row in rows if row.matched_expectation is not None]
    # A route-change expectation is evaluated only when the comparison runner
    # can state whether the selected route matched its golden expectation.
    evaluated_expected_change = [row for row in expected_change if row.matched_expectation is not None]
    # A false reroute requires an actual baseline/intelligence comparison.
    evaluated_no_change = [row for row in expected_no_change if row.route_changed is not None]
    evaluated_disruptions = [
        row for row in rows if row.known_relevant_disruption is True and row.route_changed is not None
    ]
    correct_route_changes = sum(
        row.route_changed is True and row.matched_expectation is True for row in evaluated_expected_change
    )
    false_reroutes = sum(row.route_changed is True for row in evaluated_no_change)
    missed_disruptions = sum(row.route_changed is False for row in evaluated_disruptions)
    effects: dict[str, Counter[str]] = {
        source.value: Counter() for source in SourceContribution
    }
    for row in rows:
        for source, effect in row.source_effects:
            effects[source.value][effect.value] += 1
    return {
        "metric_scope": EvidenceKind.DETERMINISTIC_FIXTURE.value,
        "label": "deterministic fixture validation; not a real-world performance claim",
        "scenario_pass_rate": {
            "passed": sum(row.matched_expectation is True for row in matched),
            "total": len(matched),
            "rate": _rate(sum(row.matched_expectation is True for row in matched), len(matched)),
        },
        "correct_route_change_rate": {
            "correct": correct_route_changes,
            "evaluated_expected_route_changes": len(evaluated_expected_change),
            "unevaluated_expected_route_changes": len(expected_change) - len(evaluated_expected_change),
            "rate": _rate(correct_route_changes, len(evaluated_expected_change)),
        },
        "false_reroute_rate": {
            "unnecessary_reroutes": false_reroutes,
            "evaluated_no_change_scenarios": len(evaluated_no_change),
            "unevaluated_no_change_scenarios": len(expected_no_change) - len(evaluated_no_change),
            "rate": _rate(false_reroutes, len(evaluated_no_change)),
        },
        "missed_disruption_rate": {
            "missed": missed_disruptions,
            "evaluated_known_relevant_disruptions": len(evaluated_disruptions),
            "unevaluated_known_relevant_disruptions": sum(row.known_relevant_disruption is True for row in rows) - len(evaluated_disruptions),
            "rate": _rate(missed_disruptions, len(evaluated_disruptions)),
        },
        "incident_association_accuracy": _optional_accuracy(rows, "incident_association_correct"),
        "deduplication_accuracy": _optional_accuracy(rows, "deduplication_correct"),
        "empty_scan_correctness": _optional_accuracy(rows, "empty_scan_correct"),
        "source_contribution": {source: dict(effect_counts) for source, effect_counts in effects.items()},
        "latency": {
            "baseline": _latency_summary(rows, "baseline_latency_ms"),
            "intelligence": _latency_summary(rows, "intelligence_latency_ms"),
            "shadow_overhead": _latency_summary(rows, "shadow_overhead_ms"),
        },
    }


def _latency_summary(rows: Iterable[ScenarioMetricRecord], field: str) -> dict[str, int | float | None]:
    values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    if not values:
        return {"samples": 0, "min_ms": None, "max_ms": None, "mean_ms": None}
    return {
        "samples": len(values),
        "min_ms": min(values),
        "max_ms": max(values),
        "mean_ms": round(sum(values) / len(values), 2),
    }


def _live_metrics(rows: list[ScenarioMetricRecord]) -> dict[str, Any]:
    classifications = Counter(
        row.classification.value for row in rows if row.classification is not None
    )
    classified = sum(classifications.values())
    all_classified = classified == len(rows)
    # Route quality requires a person or separately verified ground truth.  A
    # live disagreement alone is not a false reroute or an improvement.
    quality: dict[str, Any] = {
        "status": "available" if all_classified and rows else "requires_human_review",
        "correct_route_change_rate": None,
        "false_reroute_rate": None,
        "missed_disruption_rate": None,
    }
    if all_classified and rows:
        quality.update(
            {
                "correct_improvements": classifications[ReviewClassification.CORRECT_IMPROVEMENT.value],
                "unnecessary_reroutes": classifications[ReviewClassification.UNNECESSARY_REROUTE.value],
                "missed_disruptions": classifications[ReviewClassification.MISSED_DISRUPTION.value],
                "classified_records": classified,
            }
        )
    return {
        "metric_scope": EvidenceKind.LIVE_SHADOW.value,
        "label": "live shadow observations; route-quality claims require human classification",
        "records": len(rows),
        "classified_records": classified,
        "unclassified_records": len(rows) - classified,
        "classification_counts": dict(classifications),
        "latency": {
            "baseline": _latency_summary(rows, "baseline_latency_ms"),
            "intelligence": _latency_summary(rows, "intelligence_latency_ms"),
            "shadow_overhead": _latency_summary(rows, "shadow_overhead_ms"),
        },
        "quality": quality,
    }


def aggregate_metrics(records: Iterable[ScenarioMetricRecord | Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate records without conflating fixture evidence with live shadow."""

    parsed = [
        item if isinstance(item, ScenarioMetricRecord) else ScenarioMetricRecord.from_mapping(item)
        for item in records
    ]
    fixture_rows = [row for row in parsed if row.evidence_kind is EvidenceKind.DETERMINISTIC_FIXTURE]
    live_rows = [row for row in parsed if row.evidence_kind is EvidenceKind.LIVE_SHADOW]
    return {
        "schema_version": 1,
        "deterministic_fixture": _fixture_metrics(fixture_rows),
        "live_shadow": _live_metrics(live_rows),
    }
