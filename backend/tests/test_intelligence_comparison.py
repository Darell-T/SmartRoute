"""Tests for deterministic baseline-versus-intelligence comparisons."""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from evaluation.route_intelligence import advisor_context
from evaluation.route_intelligence.comparison import (
    compare_scenario,
    render_human_report,
    semantic_projection,
    ticketmaster_impacts_for_replay,
)
from evaluation.route_intelligence.replay import ReplayFixtureAdapters, ScenarioValidationError, load_scenario
from scripts import replay_route_intelligence


class ComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_boundary_uses_production_baseline_and_intelligence_shapes(self):
        scenario = load_scenario("clear-route")
        with patch.object(
            advisor_context, "build_advisor_payload", wraps=advisor_context.build_advisor_payload
        ) as payload_spy:
            report = await compare_scenario(scenario)

        self.assertTrue(report["comparison"]["matched_expectation"])
        baseline_call, intelligence_call = payload_spy.call_args_list
        self.assertEqual(baseline_call.kwargs["mode"], advisor_context.PlanningMode.BASELINE)
        self.assertNotIn("incidents", baseline_call.kwargs)
        self.assertEqual(intelligence_call.kwargs["mode"], advisor_context.PlanningMode.INTELLIGENCE)
        self.assertIn("incidents", intelligence_call.kwargs)

    async def test_clear_route_stays_unchanged_and_has_advisor_identity_latency(self):
        report = await compare_scenario("clear-route")

        self.assertEqual(report["baseline"]["selected_route_id"], "candidate-0")
        self.assertEqual(report["intelligence"]["selected_route_id"], "candidate-0")
        self.assertFalse(report["comparison"]["route_changed"])
        self.assertTrue(report["comparison"]["matched_expectation"])
        self.assertGreaterEqual(report["comparison"]["decision_latency_ms"], 0)
        timings = report["comparison"]["local_replay_timings_ms"]
        self.assertEqual(
            set(timings),
            {
                "fixture_normalization", "baseline_construction_and_parse",
                "intelligence_evidence_payload_and_parse", "total_replay_comparison",
            },
        )
        self.assertTrue(all(value >= 0 for value in timings.values()))
        self.assertEqual(report["advisor_identity"]["advisor_provider"], "anthropic")
        self.assertIn("advisor_model", report["advisor_identity"])
        projection = semantic_projection(report)["comparison"]
        self.assertNotIn("decision_latency_ms", projection)
        self.assertNotIn("local_replay_timings_ms", projection)

    async def test_changed_and_unexpected_decisions_are_reported_from_recorded_transcripts(self):
        scenario = load_scenario("clear-route")
        inputs = await ReplayFixtureAdapters(scenario).load()
        changed_transcript = inputs.advisor_outputs["intelligence"].replace("[ROUTE:0]", "[ROUTE:1]").replace(
            '"selected_route_index":0', '"selected_route_index":1'
        ).replace('"index":0,"is_recommended":true', '"index":0,"is_recommended":false').replace(
            '"index":1,"is_recommended":false', '"index":1,"is_recommended":true'
        )
        changed_inputs = replace(inputs, advisor_outputs={**inputs.advisor_outputs, "intelligence": changed_transcript})
        expected_change = replace(
            scenario,
            expected={"baseline_route_id": "candidate-0", "intelligence_route_id": "candidate-1", "route_should_change": True},
        )
        with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=changed_inputs)):
            changed = await compare_scenario(expected_change)
        self.assertTrue(changed["comparison"]["route_changed"])
        self.assertTrue(changed["comparison"]["matched_expectation"])

        unexpected = replace(
            scenario,
            expected={"baseline_route_id": "candidate-0", "intelligence_route_id": "candidate-0", "route_should_change": False},
        )
        with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=changed_inputs)):
            failed = await compare_scenario(unexpected)
        self.assertFalse(failed["comparison"]["matched_expectation"])

    async def test_malformed_expectation_fails_before_loading_inputs(self):
        scenario = replace(load_scenario("clear-route"), expected={"route_should_change": "false"})
        with self.assertRaisesRegex(ScenarioValidationError, "expected is missing"):
            await compare_scenario(scenario)

    async def test_nonfresh_snapshot_is_partial_and_excludes_adversarial_511_match(self):
        scenario = load_scenario("clear-route")
        inputs = await ReplayFixtureAdapters(scenario).load()
        for snapshot_status in ("stale", "unavailable"):
            with self.subTest(snapshot_status=snapshot_status):
                nonfresh_inputs = replace(
                    inputs,
                    ny511_snapshot=inputs.ny511_snapshot.model_copy(update={"status": snapshot_status}),
                    ny511_matches=[
                        {
                            "source_id": "nonfresh-closure", "source": "511ny", "severity": "high",
                            "description": "A nonfresh closure must not alter replay intelligence.",
                            "nearest_stop": {"stop_name": "34 St-Herald Sq"},
                            "affected_candidate_route_ids": ["candidate-0"], "impact_scope": "roadway",
                        }
                    ],
                )
                expected = replace(
                    scenario,
                    expected={
                        "baseline_route_id": "candidate-0", "intelligence_route_id": "candidate-0",
                        "route_should_change": False, "scan_status": "partial",
                        "ny511_snapshot_status": snapshot_status,
                    },
                )
                with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=nonfresh_inputs)):
                    report = await compare_scenario(expected)
                self.assertEqual(report["scan_status"], "partial")
                self.assertEqual(report["evidence"]["ny511_snapshot_status"], snapshot_status)
                self.assertEqual(report["evidence"]["incident_ids"], [])
                self.assertEqual(report["evidence"]["association_diagnostics"], [])
                self.assertTrue(report["comparison"]["matched_expectation"])

    async def test_511_ablation_reports_disabled_without_leaking_fixture_status(self):
        scenario = load_scenario("clear-route")
        inputs = await ReplayFixtureAdapters(scenario).load()
        expected = replace(
            scenario,
            expected={
                "baseline_route_id": "candidate-0", "intelligence_route_id": "candidate-0",
                "route_should_change": False, "scan_status": "disabled", "ny511_snapshot_status": "disabled",
            },
        )
        with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=inputs)):
            report = await compare_scenario(expected, enabled_sources={"mta"})
        self.assertEqual(report["scan_status"], "disabled")
        self.assertEqual(report["evidence"]["ny511_snapshot_status"], "disabled")
        self.assertTrue(report["comparison"]["matched_expectation"])

    async def test_separate_subway_and_bus_ablation_toggles_are_honored(self):
        scenario = load_scenario("clear-route")
        inputs = await ReplayFixtureAdapters(scenario).load()
        signal_inputs = replace(inputs, stalled_trains=[{"route_id": "N"}], stalled_buses=[{"route_id": "M7"}])
        with patch.object(
            advisor_context, "build_advisor_payload", wraps=advisor_context.build_advisor_payload
        ) as payload_spy, patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=signal_inputs)):
            report = await compare_scenario(scenario, enabled_sources={"mta", "subway_vehicle_detection"})
        intelligence_call = payload_spy.call_args_list[-1]
        self.assertEqual(intelligence_call.kwargs["stalled_trains"], [{"route_id": "N"}])
        self.assertEqual(intelligence_call.kwargs["stalled_buses"], [])
        self.assertEqual(report["evidence"]["stalled_train_count"], 1)
        self.assertEqual(report["evidence"]["stalled_bus_count"], 0)

    async def test_runner_stays_offline_even_when_a_socket_is_attempted(self):
        scenario = load_scenario("clear-route")
        with patch.object(socket, "create_connection", side_effect=AssertionError("network was attempted")):
            report = await compare_scenario(scenario)
        self.assertTrue(report["comparison"]["matched_expectation"])

    async def test_cancelled_distant_and_in_window_events_have_correct_effect(self):
        scenario = load_scenario("clear-route")
        cancelled = {
            "event_id": "cancelled", "name": "Cancelled", "venue_key": "msg", "venue_name": "MSG",
            "status": "cancelled", "start_time_status": "confirmed", "start_iso": "2026-07-22T19:00:00Z",
            "estimated_end_iso": "2026-07-22T21:45:00Z",
        }
        distant = {**cancelled, "event_id": "distant", "status": "onsale", "venue_key": None}
        active = {**cancelled, "event_id": "msg-live", "status": "onsale"}
        impacts = await ticketmaster_impacts_for_replay(
            [cancelled, distant, active], frozen_time=scenario.clock.now(), enabled=True
        )
        self.assertEqual([row["event_id"] for row in impacts], ["msg-live"])
        self.assertEqual(impacts[0]["stations"], ["34 St-Penn Station"])

    async def test_human_report_is_concise_and_does_not_copy_transcripts(self):
        report = await compare_scenario("clear-route")
        rendered = render_human_report(report)
        self.assertIn("Scenario: clear-route", rendered)
        self.assertIn("Result: PASS", rendered)
        self.assertIn("active event(s)", rendered)
        self.assertIn("511NY snapshot", rendered)
        self.assertNotIn("[ROUTE:", rendered)
        self.assertNotIn("CANDIDATE_ANALYSIS", rendered)

    def test_cli_writes_reports_and_returns_nonzero_for_unmet_expectation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            json_out, text_out = directory / "nested" / "report.json", directory / "nested" / "report.txt"
            with patch.object(sys, "argv", ["replay", "clear-route", "--json-out", str(json_out), "--text-out", str(text_out)]):
                self.assertEqual(replay_route_intelligence.main(), 0)
            machine = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertTrue(machine["all_expectations_matched"])
            self.assertEqual(machine["validation"]["evidence_scope"], "deterministic_fixture")
            self.assertEqual(len(machine["ablations"]), 1)
            self.assertIn("Result: PASS", text_out.read_text(encoding="utf-8"))

    def test_cli_returns_nonzero_when_a_comparison_fails(self):
        failed = {
            "scenario_id": "unexpected-change",
            "baseline": {"selected_route_id": "candidate-0"},
            "intelligence": {"selected_route_id": "candidate-1"},
            "evidence": {},
            "comparison": {"matched_expectation": False},
        }
        with patch.object(replay_route_intelligence, "_run", new=AsyncMock(return_value=[failed])), patch.object(
            sys, "argv", ["replay", "unexpected-change"]
        ):
            self.assertEqual(replay_route_intelligence.main(), 1)


if __name__ == "__main__":
    unittest.main()
