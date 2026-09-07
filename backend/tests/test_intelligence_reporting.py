from __future__ import annotations

import unittest

from evaluation.route_intelligence.comparison import compare_scenario
from evaluation.route_intelligence.failure_modes import run_source_ablations
from evaluation.route_intelligence.replay import load_scenario
from evaluation.route_intelligence.reporting import (
    _measured_ms,
    build_fixture_validation_results,
)


class FixtureReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_metrics_are_derived_from_real_comparison_and_ablation_reports(self):
        scenarios = [
            load_scenario("clear-route"),
            load_scenario("stalled-subway"),
            load_scenario("weak-single-social-report"),
        ]
        reports = [await compare_scenario(scenario) for scenario in scenarios]
        ablations = [await run_source_ablations(scenario) for scenario in scenarios]

        result = build_fixture_validation_results(reports, scenarios, ablations)

        assert result["schema_version"] == 2
        assert result["evidence_scope"] == "deterministic_fixture"
        assert "shadow_overhead" not in repr(result)
        assert "do not prove" in result["claim_boundary"]
        assert "autonomous advisor" in result["claim_boundary"]
        metrics = result["metrics"]
        assert metrics["scenario_pass_rate"]["rate"] == 1.0
        assert metrics["correct_route_change_rate"]["rate"] == 1.0
        assert metrics["false_reroute_rate"]["rate"] == 0.0
        records = {row["scenario_id"]: row for row in result["scenario_records"]}
        assert records["stalled-subway"]["baseline_latency_ms"] >= 1
        assert records["stalled-subway"]["intelligence_latency_ms"] >= 1
        assert records["stalled-subway"]["source_effects"]["stalled_subway"] == "changed_route"
        assert "prompt" not in repr(result).lower()

    async def test_association_dedup_and_empty_scan_checks_use_observed_evidence(self):
        names = [
            "bus-corridor-road-closure",
            "multiple-source-corroboration",
            "partial-source-failure",
        ]
        scenarios = [load_scenario(name) for name in names]
        reports = [await compare_scenario(scenario) for scenario in scenarios]
        ablations = [await run_source_ablations(scenario) for scenario in scenarios]
        result = build_fixture_validation_results(reports, scenarios, ablations)
        metrics = result["metrics"]
        assert metrics["incident_association_accuracy"]["rate"] == 1.0
        assert metrics["deduplication_accuracy"]["rate"] == 1.0
        assert metrics["empty_scan_correctness"]["rate"] == 1.0

    def test_latency_rounding_and_invalid_samples_are_frozen(self):
        assert _measured_ms(0) == 0
        assert _measured_ms(0.1) == 1
        assert _measured_ms(1.01) == 2
        assert _measured_ms(-1) is None
        assert _measured_ms("fast") is None
        assert _measured_ms(float("inf")) is None


if __name__ == "__main__":
    unittest.main()
