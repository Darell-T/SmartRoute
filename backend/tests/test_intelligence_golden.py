"""Golden route-intelligence replays and false-reroute contracts.

Every scenario is loaded from provider-shaped fixtures by
``ReplayFixtureAdapters`` and compared through the production advisor payload
and selection parser.  Assertions below inspect only application evidence and
recorded decisions; no live provider or model call is permitted.
"""

from __future__ import annotations

import unittest

from evaluation.route_intelligence.comparison import compare_scenario
from evaluation.route_intelligence.failure_modes import run_source_ablations
from evaluation.route_intelligence.replay import CANONICAL_SOURCE_NAMES, load_all_scenarios


EXPECTED_SCENARIOS = {
    "clear-route",
    "stalled-subway",
    "stalled-bus",
    "bus-corridor-road-closure",
    "nearby-roadway-unrelated-subway",
    "station-access-incident",
    "ticketmaster-crowd-window",
    "multiple-source-corroboration",
    "weak-single-social-report",
    "stale-or-resolved-incident",
    "radius-boundary",
    "partial-source-failure",
}


class GoldenScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.scenarios = {item.scenario_id: item for item in load_all_scenarios()}

    async def test_required_scenario_set_is_complete_and_matches_every_expectation(self):
        self.assertEqual(set(self.scenarios), EXPECTED_SCENARIOS)
        for scenario_id, scenario in self.scenarios.items():
            with self.subTest(scenario=scenario_id):
                report = await compare_scenario(scenario)
                self.assertTrue(report["comparison"]["matched_expectation"])
                self.assertEqual(report["advisor_identity"]["advisor_provider"], "anthropic")
                self.assertEqual(
                    set(report["source_status"]), CANONICAL_SOURCE_NAMES
                )
                self.assertIn("expected_incident_count", scenario.expected)
                self.assertEqual(
                    report["evidence"]["incident_count"],
                    scenario.expected["expected_incident_count"],
                )
                if "expected_incident_ids" in scenario.expected:
                    self.assertEqual(
                        report["evidence"]["incident_ids"],
                        scenario.expected["expected_incident_ids"],
                    )
                for excluded in scenario.expected.get("excluded_incident_ids", []):
                    self.assertNotIn(excluded, report["evidence"]["incident_ids"])
                if "expected_source_counts" in scenario.expected:
                    self.assertEqual(
                        report["evidence"]["source_counts"],
                        scenario.expected["expected_source_counts"],
                    )
                if "expected_mta_alert_count" in scenario.expected:
                    self.assertEqual(
                        report["evidence"]["mta_alert_count"],
                        scenario.expected["expected_mta_alert_count"],
                    )
                if "expected_stalled_train_count" in scenario.expected:
                    self.assertEqual(
                        report["evidence"]["stalled_train_count"],
                        scenario.expected["expected_stalled_train_count"],
                    )
                if "expected_stalled_bus_count" in scenario.expected:
                    self.assertEqual(
                        report["evidence"]["stalled_bus_count"],
                        scenario.expected["expected_stalled_bus_count"],
                    )
                if "expected_ticketmaster_event_ids" in scenario.expected:
                    self.assertEqual(
                        report["evidence"]["ticketmaster_event_ids"],
                        scenario.expected["expected_ticketmaster_event_ids"],
                    )

    async def test_verified_511_associations_keep_mode_and_scope_semantics(self):
        for scenario_id in (
            "bus-corridor-road-closure",
            "nearby-roadway-unrelated-subway",
            "station-access-incident",
        ):
            with self.subTest(scenario=scenario_id):
                scenario = self.scenarios[scenario_id]
                report = await compare_scenario(scenario)
                expected = scenario.expected["expected_association"]
                association = report["evidence"]["association_diagnostics"][0]
                self.assertEqual(
                    association["candidate_route_ids"], expected["candidate_route_ids"]
                )
                self.assertEqual(association["modes"], expected["modes"])
                self.assertEqual(association["impact_scope"], expected["impact_scope"])
                subway_relevance = expected.get("subway_relevance")
                if subway_relevance is not None:
                    self.assertEqual(
                        association["relevance_by_mode"].get("subway"),
                        subway_relevance,
                    )

        roadway = await compare_scenario("nearby-roadway-unrelated-subway")
        association = roadway["evidence"]["association_diagnostics"][0]
        self.assertNotIn("subway", association["modes"])
        self.assertFalse(roadway["comparison"]["route_changed"])

    async def test_false_positive_scenarios_do_not_reroute(self):
        no_change = (
            "clear-route",
            "nearby-roadway-unrelated-subway",
            "weak-single-social-report",
            "stale-or-resolved-incident",
            "radius-boundary",
            "partial-source-failure",
        )
        for scenario_id in no_change:
            with self.subTest(scenario=scenario_id):
                report = await compare_scenario(scenario_id)
                self.assertFalse(report["comparison"]["route_changed"])

        weak = await compare_scenario("weak-single-social-report")
        self.assertEqual(weak["evidence"]["source_counts"], {"grok_x": 1})
        terminal = await compare_scenario("stale-or-resolved-incident")
        self.assertEqual(terminal["evidence"]["incident_count"], 0)
        partial = await compare_scenario("partial-source-failure")
        self.assertEqual(partial["scan_status"], "partial")
        self.assertNotEqual(partial["scan_status"], "complete")

    async def test_multi_source_evidence_is_merged_once(self):
        report = await compare_scenario("multiple-source-corroboration")
        self.assertEqual(report["evidence"]["incident_count"], 1)
        self.assertEqual(
            report["evidence"]["source_counts"],
            {"511ny": 1, "grok_web": 1, "grok_x": 1},
        )
        self.assertEqual(report["evidence"]["stalled_train_count"], 1)
        self.assertTrue(report["comparison"]["route_changed"])

    async def test_radius_boundary_is_inclusive_and_outside_point_is_excluded(self):
        scenario = self.scenarios["radius-boundary"]
        self.assertEqual(
            scenario.expected["boundary_policy"], "distance_meters <= radius_meters"
        )
        report = await compare_scenario(scenario)
        self.assertEqual(
            report["evidence"]["incident_ids"],
            ["radius-inside", "radius-boundary"],
        )
        self.assertNotIn("radius-outside", report["evidence"]["incident_ids"])

    async def test_every_scenario_records_all_seven_one_source_ablations(self):
        for scenario_id in sorted(self.scenarios):
            with self.subTest(scenario=scenario_id):
                report = await run_source_ablations(self.scenarios[scenario_id])
                rows = {row["source"]: row for row in report["ablations"]}
                self.assertEqual(set(rows), CANONICAL_SOURCE_NAMES)
                self.assertTrue(all(row["recorded"] for row in rows.values()))
                self.assertTrue(all(row["status"] == "complete" for row in rows.values()))

    async def test_decision_critical_sources_change_the_recorded_choice(self):
        critical = {
            "stalled-subway": "subway_vehicle_detection",
            "stalled-bus": "bus_vehicle_detection",
            "bus-corridor-road-closure": "511ny",
            "station-access-incident": "511ny",
            "ticketmaster-crowd-window": "ticketmaster",
        }
        for scenario_id, source in critical.items():
            with self.subTest(scenario=scenario_id, source=source):
                report = await run_source_ablations(self.scenarios[scenario_id])
                row = next(item for item in report["ablations"] if item["source"] == source)
                self.assertEqual(row["source_effect"], "changed_route")
                self.assertEqual(row["selected_route_id"], "candidate-0")

    async def test_mta_ablation_changes_recorded_explanation_and_removes_alert(self):
        report = await run_source_ablations(
            self.scenarios["multiple-source-corroboration"]
        )
        row = next(item for item in report["ablations"] if item["source"] == "mta")
        self.assertEqual(row["source_effect"], "changed_explanation_only")
        self.assertEqual(report["all_sources"]["evidence"]["mta_alert_count"], 1)
        self.assertEqual(row["evidence"]["mta_alert_count"], 0)


if __name__ == "__main__":
    unittest.main()
