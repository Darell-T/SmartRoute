"""Deterministic replay metrics for route-intelligence evaluation.

This module deliberately does not infer real-world benefit from fixtures and
does not score unclassified disagreements as correct or incorrect.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any


class EvidenceKind(StrEnum):
    """Accepted metric evidence. Only deterministic fixture records are kept."""

    DETERMINISTIC_FIXTURE = "deterministic_fixture"


class SourceContribution(StrEnum):
    """Known evidence labels; arbitrary upstream strings are never persisted."""

    MTA_ALERTS = "mta_alerts"
    STALLED_SUBWAY = "stalled_subway"
    STALLED_BUS = "stalled_bus"
    GROK_X = "grok_x"
    GROK_WEB = "grok_web"
    CACHED_511NY = "cached_511ny"
    TICKETMASTER = "ticketmaster"


class SourceEffect(StrEnum):
    CHANGED_ROUTE = "changed_route"
    CHANGED_EXPLANATION_ONLY = "changed_explanation_only"
    HAD_NO_EFFECT = "had_no_effect"


def _enum[T: Enum](value: object, enum_type: type[T]) -> T | None:
    if isinstance(value, enum_type):
        return value
    members = {item.value: item for item in enum_type}
    return members.get(str(value))


def _optional_bool(raw: Mapping[str, Any], name: str) -> bool | None:
    value = raw.get(name)
    return value if isinstance(value, bool) else None


def _bounded_latency(raw: Mapping[str, Any], name: str) -> int | None:
    value = raw.get(name)
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 86_400_000 else None


def _parsed_source_effects(
    raw: Mapping[str, Any],
) -> tuple[tuple[SourceContribution, SourceEffect], ...]:
    source_effects: list[tuple[SourceContribution, SourceEffect]] = []
    raw_effects = raw.get("source_effects")
    if not isinstance(raw_effects, Mapping):
        return ()
    for raw_source, raw_effect in raw_effects.items():
        source = _enum(raw_source, SourceContribution)
        effect = _enum(raw_effect, SourceEffect)
        if source is not None and effect is not None:
            source_effects.append((source, effect))
    return tuple(sorted(source_effects, key=lambda pair: pair[0].value))


def _require_fixture_identity(
    raw: Mapping[str, Any],
) -> tuple[EvidenceKind, str]:
    kind = _enum(raw.get("evidence_kind"), EvidenceKind)
    unknown_kind = "scenario metric record requires known evidence_kind"
    if kind is not EvidenceKind.DETERMINISTIC_FIXTURE:
        raise ValueError(unknown_kind)
    scenario_id = str(raw.get("scenario_id") or "").strip() or None
    missing_id = "fixture metric record requires scenario_id"
    if not scenario_id:
        raise ValueError(missing_id)
    return kind, scenario_id


@dataclass(frozen=True)
class ScenarioMetricRecord:
    """Small result contract produced by comparison/replay code.

    There are no route geometries, stop names, prompts, user inputs, raw
    incident text, or provider responses in this boundary.
    """

    scenario_id: str | None
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
    source_effects: tuple[tuple[SourceContribution, SourceEffect], ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ScenarioMetricRecord:
        kind, scenario_id = _require_fixture_identity(raw)
        return cls(
            scenario_id=scenario_id,
            evidence_kind=kind,
            expected_route_change=_optional_bool(raw, "expected_route_change"),
            route_changed=_optional_bool(raw, "route_changed"),
            matched_expectation=_optional_bool(raw, "matched_expectation"),
            known_relevant_disruption=_optional_bool(raw, "known_relevant_disruption"),
            incident_association_correct=_optional_bool(
                raw, "incident_association_correct"
            ),
            deduplication_correct=_optional_bool(raw, "deduplication_correct"),
            empty_scan_correct=_optional_bool(raw, "empty_scan_correct"),
            baseline_latency_ms=_bounded_latency(raw, "baseline_latency_ms"),
            intelligence_latency_ms=_bounded_latency(raw, "intelligence_latency_ms"),
            source_effects=_parsed_source_effects(raw),
        )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _optional_accuracy(
    rows: Iterable[ScenarioMetricRecord], field: str
) -> dict[str, int | float | None]:
    values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    passed = sum(value is True for value in values)
    return {"passed": passed, "total": len(values), "rate": _rate(passed, len(values))}


def _fixture_metrics(rows: list[ScenarioMetricRecord]) -> dict[str, Any]:
    expected_change = [row for row in rows if row.expected_route_change is True]
    expected_no_change = [row for row in rows if row.expected_route_change is False]
    matched = [row for row in rows if row.matched_expectation is not None]
    # A route-change expectation is evaluated only when the comparison runner
    # can state whether the selected route matched its golden expectation.
    evaluated_expected_change = [
        row for row in expected_change if row.matched_expectation is not None
    ]
    # A false reroute requires an actual baseline/intelligence comparison.
    evaluated_no_change = [
        row for row in expected_no_change if row.route_changed is not None
    ]
    evaluated_disruptions = [
        row
        for row in rows
        if row.known_relevant_disruption is True and row.route_changed is not None
    ]
    correct_route_changes = sum(
        row.route_changed is True and row.matched_expectation is True
        for row in evaluated_expected_change
    )
    false_reroutes = sum(row.route_changed is True for row in evaluated_no_change)
    missed_disruptions = sum(
        row.route_changed is False for row in evaluated_disruptions
    )
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
            "rate": _rate(
                sum(row.matched_expectation is True for row in matched), len(matched)
            ),
        },
        "correct_route_change_rate": {
            "correct": correct_route_changes,
            "evaluated_expected_route_changes": len(evaluated_expected_change),
            "unevaluated_expected_route_changes": len(expected_change)
            - len(evaluated_expected_change),
            "rate": _rate(correct_route_changes, len(evaluated_expected_change)),
        },
        "false_reroute_rate": {
            "unnecessary_reroutes": false_reroutes,
            "evaluated_no_change_scenarios": len(evaluated_no_change),
            "unevaluated_no_change_scenarios": len(expected_no_change)
            - len(evaluated_no_change),
            "rate": _rate(false_reroutes, len(evaluated_no_change)),
        },
        "missed_disruption_rate": {
            "missed": missed_disruptions,
            "evaluated_known_relevant_disruptions": len(evaluated_disruptions),
            "unevaluated_known_relevant_disruptions": sum(
                row.known_relevant_disruption is True for row in rows
            )
            - len(evaluated_disruptions),
            "rate": _rate(missed_disruptions, len(evaluated_disruptions)),
        },
        "incident_association_accuracy": _optional_accuracy(
            rows, "incident_association_correct"
        ),
        "deduplication_accuracy": _optional_accuracy(rows, "deduplication_correct"),
        "empty_scan_correctness": _optional_accuracy(rows, "empty_scan_correct"),
        "source_contribution": {
            source: dict(effect_counts) for source, effect_counts in effects.items()
        },
        "latency": {
            "baseline": _latency_summary(rows, "baseline_latency_ms"),
            "intelligence": _latency_summary(rows, "intelligence_latency_ms"),
        },
    }


def _latency_summary(
    rows: Iterable[ScenarioMetricRecord], field: str
) -> dict[str, int | float | None]:
    values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    if not values:
        return {"samples": 0, "min_ms": None, "max_ms": None, "mean_ms": None}
    return {
        "samples": len(values),
        "min_ms": min(values),
        "max_ms": max(values),
        "mean_ms": round(sum(values) / len(values), 2),
    }


def aggregate_metrics(
    records: Iterable[ScenarioMetricRecord | Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate only deterministic fixture metric records."""

    parsed = [
        item if isinstance(item, ScenarioMetricRecord) else ScenarioMetricRecord.from_mapping(item)
        for item in records
    ]
    fixture_rows = [
        row for row in parsed if row.evidence_kind is EvidenceKind.DETERMINISTIC_FIXTURE
    ]
    return {
        "schema_version": 2,
        "deterministic_fixture": _fixture_metrics(fixture_rows),
    }
