from __future__ import annotations

import unittest

from evaluation.route_intelligence.metrics import ScenarioMetricRecord, SourceEffect, aggregate_metrics
from evaluation.route_intelligence.shadow import EvidenceKind, SourceContribution


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
        result = aggregate_metrics([
            _fixture("clear-route"),
            _fixture(
                "stalled-subway", expected_route_change=True, route_changed=True,
                known_relevant_disruption=True, source_effects={"stalled_subway": "changed_route", "grok_web": "changed_confidence"},
            ),
            _fixture("weak-social", route_changed=True, matched_expectation=False, source_effects={"grok_x": "had_no_effect"}),
        ])["deterministic_fixture"]
        self.assertEqual(result["metric_scope"], "deterministic_fixture")
        self.assertEqual(result["correct_route_change_rate"]["rate"], 1.0)
        self.assertEqual(result["false_reroute_rate"]["rate"], 0.5)
        self.assertEqual(result["missed_disruption_rate"]["rate"], 0.0)
        self.assertEqual(result["source_contribution"]["stalled_subway"]["changed_route"], 1)
        self.assertEqual(result["source_contribution"]["grok_x"]["had_no_effect"], 1)
        self.assertIn("not a real-world", result["label"])

    def test_zero_denominators_are_explicitly_safe(self):
        result = aggregate_metrics([])["deterministic_fixture"]
        self.assertIsNone(result["scenario_pass_rate"]["rate"])
        self.assertIsNone(result["correct_route_change_rate"]["rate"])
        self.assertIsNone(result["false_reroute_rate"]["rate"])
        self.assertIsNone(result["missed_disruption_rate"]["rate"])

    def test_optional_quality_metrics_do_not_inflate_missing_assertions(self):
        result = aggregate_metrics([
            _fixture("no-association", incident_association_correct=None, deduplication_correct=None, empty_scan_correct=None),
        ])["deterministic_fixture"]
        self.assertEqual(result["incident_association_accuracy"], {"passed": 0, "total": 0, "rate": None})
        self.assertEqual(result["deduplication_accuracy"]["total"], 0)
        self.assertEqual(result["empty_scan_correctness"]["total"], 0)

    def test_live_shadow_refuses_route_quality_claims_until_every_record_is_reviewed(self):
        result = aggregate_metrics([
            {"observation_id": "4f6f0f2f-a93b-4cef-a3bf-082c62b36640", "evidence_kind": "live_shadow", "route_changed": True},
            {"observation_id": "eeb8dd20-7224-4c97-940f-7ee1b16d58fd", "evidence_kind": "live_shadow", "route_changed": False, "classification": "equivalent_route"},
        ])["live_shadow"]
        self.assertEqual(result["metric_scope"], "live_shadow")
        self.assertEqual(result["quality"]["status"], "requires_human_review")
        self.assertIsNone(result["quality"]["false_reroute_rate"])
        self.assertEqual(result["unclassified_records"], 1)

    def test_live_shadow_reports_classified_counts_without_inventing_rates(self):
        result = aggregate_metrics([
            {"observation_id": "4f6f0f2f-a93b-4cef-a3bf-082c62b36640", "evidence_kind": "live_shadow", "classification": "correct_improvement"},
            {"observation_id": "eeb8dd20-7224-4c97-940f-7ee1b16d58fd", "evidence_kind": "live_shadow", "classification": "unnecessary_reroute"},
        ])["live_shadow"]
        self.assertEqual(result["quality"]["status"], "available")
        self.assertIsNone(result["quality"]["correct_route_change_rate"])
        self.assertEqual(result["quality"]["correct_improvements"], 1)
        self.assertEqual(result["quality"]["unnecessary_reroutes"], 1)

    def test_unknown_sources_and_effects_are_dropped_not_treated_as_contributors(self):
        record = ScenarioMetricRecord.from_mapping(_fixture(
            "source-filter",
            source_effects={"untrusted": "changed_route", "grok_web": "unknown", "cached_511ny": "changed_score"},
        ))
        self.assertEqual([(source.value, effect.value) for source, effect in record.source_effects], [("cached_511ny", "changed_score")])

    def test_enum_instances_and_unresolved_disruptions_are_not_misclassified(self):
        record = ScenarioMetricRecord.from_mapping({
            "scenario_id": "timeout", "evidence_kind": EvidenceKind.DETERMINISTIC_FIXTURE,
            "expected_route_change": True, "route_changed": None, "known_relevant_disruption": True,
            "source_effects": {SourceContribution.GROK_WEB: SourceEffect.CHANGED_CONFIDENCE},
        })
        metrics = aggregate_metrics([record])["deterministic_fixture"]
        self.assertEqual(metrics["missed_disruption_rate"], {
            "missed": 0, "evaluated_known_relevant_disruptions": 0,
            "unevaluated_known_relevant_disruptions": 1, "rate": None,
        })
        self.assertEqual(metrics["correct_route_change_rate"]["unevaluated_expected_route_changes"], 1)

    def test_unresolved_records_are_excluded_from_false_reroute_denominator(self):
        metrics = aggregate_metrics([
            _fixture("resolved-no-change", route_changed=False),
            _fixture("unevaluated-no-change", route_changed=None, matched_expectation=None),
        ])["deterministic_fixture"]
        self.assertEqual(metrics["false_reroute_rate"], {
            "unnecessary_reroutes": 0, "evaluated_no_change_scenarios": 1,
            "unevaluated_no_change_scenarios": 1, "rate": 0.0,
        })

    def test_latency_aggregation_is_bounded_and_zero_safe(self):
        empty = aggregate_metrics([])["deterministic_fixture"]["latency"]
        self.assertEqual(empty["baseline"], {"samples": 0, "min_ms": None, "max_ms": None, "mean_ms": None})
        metrics = aggregate_metrics([
            _fixture("fast", baseline_latency_ms=12, intelligence_latency_ms=9, shadow_overhead_ms=14),
            _fixture("slow", baseline_latency_ms=20, intelligence_latency_ms=15, shadow_overhead_ms=-1),
        ])["deterministic_fixture"]["latency"]
        self.assertEqual(metrics["baseline"], {"samples": 2, "min_ms": 12, "max_ms": 20, "mean_ms": 16.0})
        self.assertEqual(metrics["intelligence"]["mean_ms"], 12.0)
        self.assertEqual(metrics["shadow_overhead"], {"samples": 1, "min_ms": 14, "max_ms": 14, "mean_ms": 14.0})

    def test_live_metric_requires_uuidv4_observation_identifier(self):
        with self.assertRaisesRegex(ValueError, "UUIDv4"):
            ScenarioMetricRecord.from_mapping({"evidence_kind": "live_shadow", "observation_id": "session-private"})
        with self.assertRaisesRegex(ValueError, "UUIDv4"):
            ScenarioMetricRecord.from_mapping({"evidence_kind": "live_shadow", "observation_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8"})
