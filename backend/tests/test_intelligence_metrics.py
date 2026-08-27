from __future__ import annotations

import unittest

import pytest
from evaluation.route_intelligence.metrics import (
    EvidenceKind,
    ScenarioMetricRecord,
    SourceContribution,
    SourceEffect,
    aggregate_metrics,
)


def _fixture(scenario_id: str, **overrides):
    values = {
        "scenario_id": scenario_id,
        "evidence_kind": "deterministic_fixture",
        "expected_route_change": False,
        "route_changed": False,
        "matched_expectation": True,
        "known_relevant_disruption": False,
        "incident_association_correct": True,
        "deduplication_correct": True,
        "empty_scan_correct": True,
        "source_effects": {"mta_alerts": "had_no_effect"},
    }
    values.update(overrides)
    return values


class IntelligenceMetricTests(unittest.TestCase):
    def test_fixture_rates_measure_expected_changes_and_false_reroutes(self):
        result = aggregate_metrics(
            [
                _fixture("clear-route"),
                _fixture(
                    "stalled-subway",
                    expected_route_change=True,
                    route_changed=True,
                    known_relevant_disruption=True,
                    source_effects={
                        "stalled_subway": "changed_route",
                        "grok_web": "changed_explanation_only",
                    },
                ),
                _fixture(
                    "weak-social",
                    route_changed=True,
                    matched_expectation=False,
                    source_effects={"grok_x": "had_no_effect"},
                ),
            ]
        )["deterministic_fixture"]
        assert result["metric_scope"] == "deterministic_fixture"
        assert result["correct_route_change_rate"]["rate"] == 1.0
        assert result["false_reroute_rate"]["rate"] == 0.5
        assert result["missed_disruption_rate"]["rate"] == 0.0
        assert result["source_contribution"]["stalled_subway"]["changed_route"] == 1
        assert result["source_contribution"]["grok_x"]["had_no_effect"] == 1
        assert "not a real-world" in result["label"]

    def test_zero_denominators_are_explicitly_safe(self):
        result = aggregate_metrics([])["deterministic_fixture"]
        assert result["scenario_pass_rate"]["rate"] is None
        assert result["correct_route_change_rate"]["rate"] is None
        assert result["false_reroute_rate"]["rate"] is None
        assert result["missed_disruption_rate"]["rate"] is None

    def test_optional_quality_metrics_do_not_inflate_missing_assertions(self):
        result = aggregate_metrics(
            [
                _fixture(
                    "no-association",
                    incident_association_correct=None,
                    deduplication_correct=None,
                    empty_scan_correct=None,
                ),
            ]
        )["deterministic_fixture"]
        assert result["incident_association_accuracy"] == {
            "passed": 0,
            "total": 0,
            "rate": None,
        }
        assert result["deduplication_accuracy"]["total"] == 0
        assert result["empty_scan_correctness"]["total"] == 0

    def test_unknown_sources_and_effects_are_dropped_not_treated_as_contributors(self):
        record = ScenarioMetricRecord.from_mapping(
            _fixture(
                "source-filter",
                source_effects={
                    "untrusted": "changed_route",
                    "grok_web": "unknown",
                    "cached_511ny": "changed_explanation_only",
                },
            )
        )
        assert [
            (source.value, effect.value) for source, effect in record.source_effects
        ] == [("cached_511ny", "changed_explanation_only")]

    def test_enum_instances_and_unresolved_disruptions_are_not_misclassified(self):
        record = ScenarioMetricRecord.from_mapping(
            {
                "scenario_id": "timeout",
                "evidence_kind": EvidenceKind.DETERMINISTIC_FIXTURE,
                "expected_route_change": True,
                "route_changed": None,
                "known_relevant_disruption": True,
                "source_effects": {
                    SourceContribution.GROK_WEB: SourceEffect.CHANGED_EXPLANATION_ONLY
                },
            }
        )
        metrics = aggregate_metrics([record])["deterministic_fixture"]
        assert metrics["missed_disruption_rate"] == {
            "missed": 0,
            "evaluated_known_relevant_disruptions": 0,
            "unevaluated_known_relevant_disruptions": 1,
            "rate": None,
        }
        assert (
            metrics["correct_route_change_rate"]["unevaluated_expected_route_changes"]
            == 1
        )

    def test_unresolved_records_are_excluded_from_false_reroute_denominator(self):
        metrics = aggregate_metrics(
            [
                _fixture("resolved-no-change", route_changed=False),
                _fixture(
                    "unevaluated-no-change",
                    route_changed=None,
                    matched_expectation=None,
                ),
            ]
        )["deterministic_fixture"]
        assert metrics["false_reroute_rate"] == {
            "unnecessary_reroutes": 0,
            "evaluated_no_change_scenarios": 1,
            "unevaluated_no_change_scenarios": 1,
            "rate": 0.0,
        }

    def test_latency_aggregation_is_bounded_and_zero_safe(self):
        empty = aggregate_metrics([])["deterministic_fixture"]["latency"]
        assert empty["baseline"] == {
            "samples": 0,
            "min_ms": None,
            "max_ms": None,
            "mean_ms": None,
        }
        metrics = aggregate_metrics(
            [
                _fixture(
                    "fast",
                    baseline_latency_ms=12,
                    intelligence_latency_ms=9,
                ),
                _fixture(
                    "slow",
                    baseline_latency_ms=20,
                    intelligence_latency_ms=15,
                ),
            ]
        )["deterministic_fixture"]["latency"]
        assert metrics["baseline"] == {
            "samples": 2,
            "min_ms": 12,
            "max_ms": 20,
            "mean_ms": 16.0,
        }
        assert metrics["intelligence"]["mean_ms"] == 12.0
        assert "shadow_overhead" not in metrics

    def test_non_fixture_evidence_kinds_are_rejected(self):
        with pytest.raises(ValueError, match="known evidence_kind"):
            ScenarioMetricRecord.from_mapping(
                {"evidence_kind": "live_shadow", "scenario_id": "shadowed"}
            )
        with pytest.raises(ValueError, match="known evidence_kind"):
            ScenarioMetricRecord.from_mapping(
                {"evidence_kind": "unknown", "scenario_id": "other"}
            )
        with pytest.raises(ValueError, match="fixture metric record requires scenario_id"):
            ScenarioMetricRecord.from_mapping({"evidence_kind": "deterministic_fixture"})

    def test_aggregate_metrics_uses_schema_version_two(self):
        result = aggregate_metrics([_fixture("clear-route")])
        assert result["schema_version"] == 2
        assert set(result) == {"schema_version", "deterministic_fixture"}
