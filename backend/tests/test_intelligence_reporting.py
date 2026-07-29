from __future__ import annotations

import unittest

from app.services.validation.comparison import compare_scenario
from app.services.validation.failure_modes import run_source_ablations
from app.services.validation.replay import load_scenario
from app.services.validation.reporting import build_fixture_validation_results


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

        self.assertEqual(result["evidence_scope"], "deterministic_fixture")
        self.assertIn("do not prove", result["claim_boundary"])
        self.assertIn("autonomous advisor", result["claim_boundary"])
        metrics = result["metrics"]
        self.assertEqual(metrics["scenario_pass_rate"]["rate"], 1.0)
        self.assertEqual(metrics["correct_route_change_rate"]["rate"], 1.0)
        self.assertEqual(metrics["false_reroute_rate"]["rate"], 0.0)
        records = {row["scenario_id"]: row for row in result["scenario_records"]}
        self.assertGreaterEqual(records["stalled-subway"]["baseline_latency_ms"], 1)
        self.assertGreaterEqual(records["stalled-subway"]["intelligence_latency_ms"], 1)
        self.assertEqual(
            records["stalled-subway"]["source_effects"]["stalled_subway"],
            "changed_route",
        )
        self.assertNotIn("prompt", repr(result).lower())

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
        self.assertEqual(metrics["incident_association_accuracy"]["rate"], 1.0)
        self.assertEqual(metrics["deduplication_accuracy"]["rate"], 1.0)
        self.assertEqual(metrics["empty_scan_correctness"]["rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
