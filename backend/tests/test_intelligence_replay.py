"""Deterministic replay fixture-loader coverage.

These tests intentionally spy on real production normalization entrypoints.
They fail if the replay adapter starts constructing post-normalized objects
instead of exercising the same helpers used by route planning.
"""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from app.services.mta import alerts as mta_alerts
from app.services.mta import bus as mta_bus
from app.services.mta import subway as mta_subway
from app.services.incidents.ny511 import SnapshotStore
from app.services.trips.crowds import event_provider
from evaluation.route_intelligence.replay import (
    ReplayFixtureAdapters,
    ScenarioValidationError,
    load_all_scenarios,
    load_scenario,
    network_disabled,
)
from evaluation.route_intelligence import replay


class ReplayScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_route_loads_through_production_normalizers(self):
        scenario = load_scenario("clear-route")
        with patch.object(mta_alerts, "_parse_service_alerts", wraps=mta_alerts._parse_service_alerts) as alerts_spy, patch.object(
            mta_subway, "parse_vehicle_positions", wraps=mta_subway.parse_vehicle_positions
        ) as vehicles_spy, patch.object(
            mta_subway, "detect_stalled_trains", wraps=mta_subway.detect_stalled_trains
        ) as stalled_trains_spy, patch.object(
            mta_bus, "parse_stalled_bus_positions", wraps=mta_bus.parse_stalled_bus_positions
        ) as buses_spy, patch.object(
            SnapshotStore, "record_success", wraps=SnapshotStore.record_success, autospec=True
        ) as snapshot_spy, patch.object(
            replay, "match_cached_incidents", wraps=replay.match_cached_incidents
        ) as matching_spy, patch.object(
            event_provider, "_parse_event", wraps=event_provider._parse_event
        ) as event_spy:
            inputs = await ReplayFixtureAdapters(scenario).load()

        self.assertEqual(inputs.mta_alerts, [])
        self.assertEqual(inputs.subway_vehicle_positions, [])
        self.assertEqual(inputs.bus_vehicle_positions, [])
        self.assertEqual(inputs.stalled_trains, [])
        self.assertEqual(inputs.stalled_buses, [])
        self.assertEqual(inputs.ny511_snapshot.status, "fresh")
        self.assertEqual(inputs.ny511_snapshot.source_record_count, 1)
        self.assertEqual(inputs.ny511_snapshot.nyc_record_count, 0)
        self.assertEqual(inputs.ny511_matches, [])
        self.assertEqual(inputs.ticketmaster_events[0]["status"], "cancelled")
        self.assertIn("[ROUTE:0]", inputs.advisor_outputs["baseline"])
        self.assertIn("[CANDIDATE_ANALYSIS]", inputs.advisor_outputs["intelligence"])
        alerts_spy.assert_called_once()
        vehicles_spy.assert_called_once()
        stalled_trains_spy.assert_called_once()
        buses_spy.assert_called_once()
        snapshot_spy.assert_awaited_once()
        matching_spy.assert_called_once()
        event_spy.assert_called_once()

    async def test_frozen_time_is_used_for_snapshot_metadata(self):
        scenario = load_scenario("clear-route")
        inputs = await ReplayFixtureAdapters(scenario).load()
        self.assertEqual(inputs.ny511_snapshot.fetched_at, scenario.clock.now())
        self.assertEqual(scenario.clock.now().isoformat(), "2026-07-22T21:30:00+00:00")

    async def test_partial_failure_uses_production_unavailable_snapshot_lifecycle(self):
        scenario = load_scenario("partial-source-failure")
        inputs = await ReplayFixtureAdapters(scenario).load()

        self.assertIsNotNone(scenario.ny511_snapshot_fetched_at)
        self.assertLess(scenario.ny511_snapshot_fetched_at, scenario.clock.now())
        self.assertEqual(inputs.ny511_snapshot.status, "unavailable")
        self.assertEqual(inputs.ny511_snapshot.incidents, [])

    async def test_malformed_provider_fixture_fails_before_comparison(self):
        scenario = load_scenario("clear-route")
        with tempfile.TemporaryDirectory() as temp:
            invalid = Path(temp) / "invalid.pb64"
            invalid.write_text("not valid base64!", encoding="ascii")
            malformed = replace(
                scenario,
                fixture_paths={**scenario.fixture_paths, "mta_alerts": invalid},
            )
            with self.assertRaisesRegex(ScenarioValidationError, "invalid base64 fixture"):
                await ReplayFixtureAdapters(malformed).load()

    async def test_malformed_advisor_output_fixture_fails_before_comparison(self):
        scenario = load_scenario("clear-route")
        with tempfile.TemporaryDirectory() as temp:
            invalid = Path(temp) / "advisor_outputs.json"
            invalid.write_text('{"baseline": "", "intelligence": "valid"}', encoding="utf-8")
            malformed = replace(
                scenario,
                fixture_paths={**scenario.fixture_paths, "advisor_outputs": invalid},
            )
            with self.assertRaisesRegex(ScenarioValidationError, "advisor_outputs.baseline"):
                await ReplayFixtureAdapters(malformed).load()

    async def test_non_empty_but_invalid_advisor_contract_fails_before_comparison(self):
        scenario = load_scenario("clear-route")
        with tempfile.TemporaryDirectory() as temp:
            invalid = Path(temp) / "advisor_outputs.json"
            invalid.write_text(
                json.dumps({
                    "baseline": "Take the first route. [ROUTE:0]",
                    "intelligence": "Take the second route. [ROUTE:1]",
                }),
                encoding="utf-8",
            )
            malformed = replace(
                scenario,
                fixture_paths={**scenario.fixture_paths, "advisor_outputs": invalid},
            )
            with self.assertRaisesRegex(ScenarioValidationError, "candidate analysis"):
                await ReplayFixtureAdapters(malformed).load()

    def test_all_scenarios_are_loaded_in_stable_order(self):
        scenarios = load_all_scenarios()
        self.assertEqual(
            [item.scenario_id for item in scenarios],
            sorted(
                {
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
            ),
        )

    def test_missing_or_path_traversal_fixture_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bad"
            root.mkdir()
            manifest = {
                "scenario_id": "bad",
                "description": "bad fixture",
                "frozen_time": "2026-07-22T17:30:00-04:00",
                "origin": {}, "destination": {}, "enabled_sources": ["mta"], "expected": {},
                "fixtures": {key: "missing.json" for key in (
                    "route_candidates", "mta_alerts", "subway_vehicle_positions", "bus_vehicle_positions",
                    "stalled_vehicle_evidence", "grok_x", "grok_web", "ny511", "ticketmaster", "advisor_outputs"
                )},
            }
            manifest["fixtures"]["ny511"] = "../outside.json"
            (root / "scenario.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ScenarioValidationError, "inside"):
                load_scenario(root)

    def test_malformed_manifest_fails_strictly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bad"
            root.mkdir()
            (root / "scenario.json").write_text('{"scenario_id": "bad"}', encoding="utf-8")
            with self.assertRaisesRegex(ScenarioValidationError, "missing"):
                load_scenario(root)

    def test_network_guard_blocks_real_socket_connections(self):
        with network_disabled():
            with self.assertRaisesRegex(RuntimeError, "network access is disabled"):
                socket.create_connection(("example.com", 443), timeout=0.01)

    async def test_network_guard_blocks_http_clients(self):
        with network_disabled():
            async with httpx.AsyncClient() as client:
                with self.assertRaisesRegex(RuntimeError, "network access is disabled"):
                    await client.get("https://example.com")
            with self.assertRaisesRegex(RuntimeError, "network access is disabled"):
                with httpx.Client() as client:
                    client.get("https://example.com")


if __name__ == "__main__":
    unittest.main()
