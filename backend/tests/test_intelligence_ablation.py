"""Deterministic source-ablation contracts for route-intelligence replays."""

from __future__ import annotations

import socket
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.trips import advisor_context
from app.services.validation.comparison import compare_scenario
from app.services.validation.failure_modes import run_source_ablations
from app.services.validation.metrics import SourceEffect
from app.services.validation.replay import (
    CANONICAL_SOURCE_NAMES,
    ReplayFixtureAdapters,
    ScenarioValidationError,
    SourceStatus,
    _parse_source_statuses,
    load_scenario,
)


def _transcript(selected: int, candidate_count: int = 2, *, reason: str = "Recorded ablation result.") -> str:
    analysis = []
    for index in range(candidate_count):
        row = {"index": index, "is_recommended": index == selected}
        row["recommendation_reason" if index == selected else "rejection_reason"] = reason
        analysis.append(row)
    import json

    return (
        f"[ROUTE:{selected}]"
        f"[CANDIDATE_ANALYSIS]{json.dumps({'selected_route_index': selected, 'candidate_analysis': analysis}, separators=(',', ':'))}"
        "[/CANDIDATE_ANALYSIS]"
    )


class SourceAblationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.scenario = load_scenario("clear-route")
        self.inputs = await ReplayFixtureAdapters(self.scenario).load()
        self.variants = {
            source: _transcript(1 if source == "mta" else 0, reason=f"without {source}")
            for source in CANONICAL_SOURCE_NAMES
        }
        self.inputs = replace(self.inputs, advisor_ablation_outputs=self.variants)

    async def test_every_canonical_source_is_ablated_with_a_recorded_variant(self):
        with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=self.inputs)):
            report = await run_source_ablations(self.scenario)

        rows = {row["source"]: row for row in report["ablations"]}
        self.assertEqual(set(rows), CANONICAL_SOURCE_NAMES)
        self.assertTrue(all(row["recorded"] for row in rows.values()))
        self.assertEqual(rows["mta"]["selected_route_id"], "candidate-1")
        self.assertEqual(rows["mta"]["source_effect"], SourceEffect.CHANGED_ROUTE.value)
        self.assertEqual(rows["grok_x"]["advisor_variant"], "ablation:grok_x")
        # The report does not fabricate a deterministic score where none is
        # used for selection; it reports only observed source effects.
        self.assertNotIn("score", repr(report).lower())

    async def test_legacy_vehicle_alias_can_ablate_subway_without_disabling_bus(self):
        legacy_sources = set(self.scenario.enabled_sources)
        legacy_sources.difference_update({"subway_vehicle_detection", "bus_vehicle_detection"})
        legacy_sources.add("vehicle_detection")
        legacy_scenario = replace(self.scenario, enabled_sources=frozenset(legacy_sources))
        without_subway = set(legacy_scenario.enabled_sources)
        without_subway.remove("vehicle_detection")
        without_subway.add("bus_vehicle_detection")
        with patch.object(
            advisor_context, "build_advisor_payload", wraps=advisor_context.build_advisor_payload
        ) as build_payload, patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=self.inputs)):
            report = await compare_scenario(
                legacy_scenario, enabled_sources=without_subway, check_expected=False
            )

        intelligence_call = build_payload.call_args_list[-1]
        self.assertEqual(intelligence_call.kwargs["stalled_trains"], [])
        self.assertEqual(intelligence_call.kwargs["stalled_buses"], self.inputs.stalled_buses)
        self.assertEqual(report["advisor_variant"], "ablation:subway_vehicle_detection")

    async def test_declared_ablation_manifest_fails_when_requested_variant_is_missing(self):
        incomplete = replace(
            self.inputs,
            advisor_ablation_outputs={"mta": self.variants["mta"]},
        )
        disabled_grok_x = set(self.scenario.enabled_sources)
        disabled_grok_x.remove("grok_x")
        with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=incomplete)):
            with self.assertRaisesRegex(ScenarioValidationError, "missing the requested grok_x variant"):
                await compare_scenario(self.scenario, enabled_sources=disabled_grok_x)

    async def test_complete_empty_scan_is_clear_but_partial_failed_and_disabled_are_distinct(self):
        expected = {
            "baseline_route_id": "candidate-0",
            "intelligence_route_id": "candidate-0",
            "route_should_change": False,
        }
        cases = {
            "complete": {
                "grok_x": SourceStatus("complete"), "grok_web": SourceStatus("complete"), "511ny": SourceStatus("complete")
            },
            "partial": {
                "grok_x": SourceStatus("partial", ("x timed out",)), "grok_web": SourceStatus("complete"), "511ny": SourceStatus("complete")
            },
            "failed": {
                "grok_x": SourceStatus("failed", ("x unavailable",)), "grok_web": SourceStatus("failed", ("web unavailable",)), "511ny": SourceStatus("failed", ("snapshot unavailable",))
            },
            "disabled": {
                "grok_x": SourceStatus("disabled"), "grok_web": SourceStatus("disabled"), "511ny": SourceStatus("disabled")
            },
        }
        for wanted, statuses in cases.items():
            with self.subTest(wanted=wanted):
                scenario = replace(self.scenario, source_status=statuses, expected=expected)
                sources = set(self.scenario.enabled_sources)
                if wanted == "disabled":
                    sources.difference_update({"grok_x", "grok_web", "511ny"})
                with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=self.inputs)):
                    report = await compare_scenario(scenario, enabled_sources=sources)
                self.assertEqual(report["scan_status"], wanted)
                self.assertEqual(report["source_status"]["grok_x"]["status"], wanted if wanted != "complete" else "complete")

    async def test_failed_source_excludes_its_recorded_evidence_while_partial_source_may_retain_it(self):
        rows = [
            {"location": "A", "nearby_station": "34 St-Herald Sq", "severity": "high", "description": "x row", "source": "grok_x"},
            {"location": "B", "nearby_station": "34 St-Herald Sq", "severity": "low", "description": "web row", "source": "grok_web"},
        ]
        inputs = replace(self.inputs, grok_incidents=rows)
        failed = replace(
            self.scenario,
            source_status={"grok_x": SourceStatus("failed", ("recorded timeout",)), "grok_web": SourceStatus("partial", ("one result page failed",))},
        )
        with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=inputs)):
            report = await compare_scenario(failed)
        self.assertEqual(report["evidence"]["incident_count"], 1)
        self.assertEqual(report["evidence"]["source_counts"], {"grok_web": 1})
        self.assertEqual(report["scan_status"], "partial")

    async def test_verified_bus_association_keeps_nearby_subway_context_non_operational(self):
        match = {
            "source_id": "closure-1",
            "source": "511ny",
            "roadway_name": "Flatbush Avenue",
            "severity": "high",
            "description": "Road closure affects the bus corridor.",
            "nearest_stop": {"stop_name": "34 St-Herald Sq", "match_source": "point"},
            "affected_candidate_route_ids": ["candidate-1", "session-secret"],
            "affected_modes": ["bus", "walk", "not-a-mode"],
            "relevance_by_mode": {"bus": "potential_bus_corridor", "subway": "nearby_unconfirmed"},
            "impact_scope": "roadway",
        }
        snapshot_row = {
            "source_id": "closure-1",
            "source": "511ny",
            "severity_normalized": "high",
            "description": "Road closure affects the bus corridor.",
            "roadway_name": "Flatbush Avenue",
            "latitude": 40.749,
            "longitude": -73.989,
            "status": "active",
        }
        inputs = replace(
            self.inputs,
            ny511_snapshot=SimpleNamespace(status="fresh", incidents=[snapshot_row]),
            ny511_matches=[match],
        )
        with patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=inputs)):
            report = await compare_scenario(self.scenario)

        association = report["evidence"]["association_diagnostics"][0]
        self.assertEqual(report["evidence"]["incident_ids"], ["closure-1"])
        self.assertEqual(association["candidate_route_ids"], ["candidate-1"])
        self.assertEqual(set(association["modes"]), {"bus", "walk"})
        self.assertEqual(association["impact_scope"], "roadway")
        self.assertEqual(association["relevance_by_mode"]["subway"], "nearby_unconfirmed")
        self.assertNotIn("subway", association["modes"])

    async def test_resolved_or_expired_verified_511_match_cannot_reach_advisor_or_evidence(self):
        match = {
            "source_id": "closure-resolved",
            "source": "511ny",
            "severity": "high",
            "description": "Closure was formerly near the route.",
            "nearest_stop": {"stop_name": "34 St-Herald Sq", "match_source": "point"},
            "affected_candidate_route_ids": ["candidate-1"],
            "affected_modes": ["bus"],
            "relevance_by_mode": {"bus": "potential_bus_corridor"},
            "impact_scope": "roadway",
        }
        snapshot_row = {
            "source_id": "closure-resolved",
            "source": "511ny",
            "severity_normalized": "high",
            "description": "Closure was formerly near the route.",
            "roadway_name": "Flatbush Avenue",
            "latitude": 40.749,
            "longitude": -73.989,
        }
        terminal_rows = {
            "resolved": {**snapshot_row, "status": "resolved"},
            "expired": {**snapshot_row, "expected_end_at": "2026-07-22T20:00:00+00:00"},
        }
        for lifecycle, row in terminal_rows.items():
            with self.subTest(lifecycle=lifecycle):
                inputs = replace(
                    self.inputs,
                    ny511_snapshot=SimpleNamespace(status="fresh", incidents=[row]),
                    ny511_matches=[match],
                )
                with patch.object(
                    advisor_context, "build_advisor_payload", wraps=advisor_context.build_advisor_payload
                ) as build_payload, patch.object(ReplayFixtureAdapters, "load", new=AsyncMock(return_value=inputs)):
                    report = await compare_scenario(self.scenario)

                intelligence_call = build_payload.call_args_list[-1]
                self.assertEqual(intelligence_call.kwargs["incidents"], [])
                self.assertEqual(report["evidence"]["incident_ids"], [])
                self.assertEqual(report["evidence"]["association_diagnostics"], [])

    def test_source_status_schema_rejects_unknown_malformed_or_unsafe_values(self):
        with self.assertRaisesRegex(ScenarioValidationError, "unknown source"):
            _parse_source_statuses({"not-a-source": {"status": "complete", "errors": []}})
        with self.assertRaisesRegex(ScenarioValidationError, "exactly status and errors"):
            _parse_source_statuses({"grok_x": {"status": "complete"}})
        with self.assertRaisesRegex(ScenarioValidationError, "complete must not include errors"):
            _parse_source_statuses({"grok_x": {"status": "complete", "errors": ["bad"]}})
        parsed = _parse_source_statuses(
            {
                "grok_x": {
                    "status": "partial",
                    "errors": [
                        "request timed out while checking 123 Main St",
                        "malformed JSON from provider at 456 Broadway",
                        "snapshot is stale for rider at 789 5th Ave",
                        "see https://example.test/?token=secret",
                        "authorization: Bearer super-token api_key=literal-secret sk-liveSecret",
                        "token super-secret for rider at 10 Downing St",
                        "arbitrary provider detail about 99 W 12th St",
                    ],
                }
            }
        )
        self.assertEqual(
            parsed["grok_x"].errors,
            ("timeout", "malformed_response", "stale", "auth_error", "auth_error", "auth_error", "source_error"),
        )
        self.assertNotIn("Main", repr(parsed))
        self.assertNotIn("super-token", repr(parsed))
        self.assertNotIn("literal-secret", repr(parsed))
        self.assertNotIn("sk-liveSecret", repr(parsed))

    async def test_ablation_runner_never_uses_live_network(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network attempted")), patch.object(
            ReplayFixtureAdapters, "load", new=AsyncMock(return_value=self.inputs)
        ):
            report = await run_source_ablations(self.scenario)
        self.assertEqual(len(report["ablations"]), len(CANONICAL_SOURCE_NAMES))


if __name__ == "__main__":
    unittest.main()
