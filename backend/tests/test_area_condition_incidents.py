"""Focused tests for the bounded area-condition incident display helpers."""

from __future__ import annotations

import unittest

from app.services.agent.tools.transit import check_area_conditions


def _row(**overrides: object) -> dict:
    row = {
        "incident_id": "inc_1",
        "state": "confirmed",
        "corroborated": True,
        "location": "Atlantic Avenue",
        "severity": "high",
        "description": "Emergency response affecting an entrance.",
        "source": "mta_alerts",
    }
    row.update(overrides)
    return row


class AreaConditionIncidentsTests(unittest.TestCase):
    def test_display_combines_incidents_and_warnings_and_dedupes_by_identity(self):
        incident = _row()
        rows = check_area_conditions._display_incidents(
            [incident, dict(incident), _row(incident_id="inc_x", location="Flatbush Avenue")]
        )
        self.assertEqual(
            {row["location"] for row in rows},
            {"Atlantic Avenue", "Flatbush Avenue"},
        )

    def test_display_preserves_state_and_corroborated_truth(self):
        rows = check_area_conditions._display_incidents(
            [
                _row(state="confirmed", corroborated=True),
                _row(
                    incident_id="inc_x",
                    state="unconfirmed",
                    corroborated=False,
                    location="Flatbush Avenue",
                ),
            ]
        )
        by_location = {row["location"]: row for row in rows}
        self.assertEqual(by_location["Atlantic Avenue"]["state"], "confirmed")
        self.assertTrue(by_location["Atlantic Avenue"]["corroborated"])
        self.assertEqual(by_location["Flatbush Avenue"]["state"], "unconfirmed")
        self.assertFalse(by_location["Flatbush Avenue"]["corroborated"])

    def test_safe_incidents_bounds_rows_and_filters_non_mappings(self):
        rows = check_area_conditions._safe_incidents(
            [_row(incident_id=f"inc_{i}") for i in range(10)] + ["not-a-row"]
        )
        self.assertEqual(len(rows), check_area_conditions._MAX_INCIDENTS)
        self.assertNotIn("not-a-row", rows)

    def test_incident_evidence_projects_bounded_metadata_without_raw_rows(self):
        value = {
            "incidents": [_row()],
            "scan_metadata": {
                "status": "partial",
                "scanned_at": "2026-08-01T18:01:00Z",
                "cache_hit": False,
                "lookup_status": "complete",
                "coverage_status": "partial",
                "lookup_kind": "index",
                "requested_coverage_ids": ["lower-manhattan"],
                "warning_count": 1,
                "sources": {
                    "attempted": ["incident_index"],
                    "completed": ["incident_index"],
                    "extra": ["must-not-leak"],
                },
            },
        }
        evidence = check_area_conditions._incident_evidence(value)
        self.assertEqual(evidence["status"], "partial")
        self.assertEqual(evidence["scanned_at"], "2026-08-01T18:01:00Z")
        self.assertIs(evidence["cache_hit"], False)
        self.assertEqual(evidence["lookup_status"], "complete")
        self.assertEqual(evidence["coverage_status"], "partial")
        self.assertEqual(evidence["lookup_kind"], "index")
        self.assertEqual(evidence["requested_coverage_ids"], ["lower-manhattan"])
        self.assertEqual(evidence["warning_count"], 1)
        self.assertEqual(evidence["sources"]["completed"], ["incident_index"])
        self.assertNotIn("extra", evidence["sources"])
        self.assertNotIn("incidents", evidence)
        self.assertNotIn("inc_1", repr(evidence))
        self.assertNotIn("Emergency response", repr(evidence))

    def test_incident_evidence_preserves_truthful_statuses_and_defaults_unknown(self):
        for status in ("complete", "partial", "stale", "unavailable", "unscanned", "failed"):
            with self.subTest(status=status):
                evidence = check_area_conditions._incident_evidence(
                    {"scan_metadata": {"status": status}}
                )
                self.assertEqual(evidence["status"], status)
        evidence = check_area_conditions._incident_evidence(
            {"scan_metadata": {"status": "made_up"}}
        )
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(check_area_conditions._incident_evidence(None)["status"], "failed")

    def test_safe_sources_is_bounded_to_allowlisted_keys(self):
        sources = check_area_conditions._safe_sources(
            {
                "completed": [f"source-{i}" for i in range(10)],
                "errors": ["one"],
                "attempted": ["must-not-leak"],
            }
        )
        self.assertIsNotNone(sources)
        self.assertEqual(len(sources["completed"]), 6)
        self.assertEqual(sources["errors"], ["one"])
        self.assertNotIn("attempted", sources)
        self.assertIsNone(check_area_conditions._safe_sources([]))


if __name__ == "__main__":
    unittest.main()
