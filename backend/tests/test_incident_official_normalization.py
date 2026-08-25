"""Focused pure-normalization tests for official incident sources.

Deterministic identity, timing, bounds, dedupe, and raw-payload shaping are
exercised directly against the official incident module with recorded values;
no collection orchestration, provider, or network calls are involved.
Collection/status/provider-boundary coverage lives in the companion official
source test module.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.services.incidents.official import (
    SOURCE_ALERTS,
    SOURCE_GTFS_RT,
    STALLED_EXPIRY_S,
    dedupe_incidents,
    normalize_alert,
    normalize_stalled,
)
from app.services.mta.config import ALERTS_URL

FIXED_NOW = 1_800_000_000.0

_CANONICAL_KEYS = {
    "source",
    "source_id",
    "state",
    "advisor_eligible",
    "impact_scope",
    "description",
    "observed_at",
    "last_verified_at",
    "expires_at",
    "source_records",
    "affected_route_ids",
    "affected_stop_ids",
}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class AlertNormalizationTests(unittest.TestCase):
    def test_bounds_and_provenance(self):
        attempted_at = _iso(FIXED_NOW)
        first = normalize_alert(
            {
                "alert_id": "alert-1",
                "route_ids": ["a", "A", "B"],
                "stop_ids": [f"D{i}" for i in range(30)],
                "header": "A train delays",
                "description": "A trains run with delays near Jay St",
                "start": FIXED_NOW - 600,
                "end": FIXED_NOW + 3600,
            },
            attempted_at,
        )
        second = normalize_alert(
            {
                "alert_id": "alert-2",
                "route_ids": ["C"],
                "header": "C header only",
                "description": None,
            },
            attempted_at,
        )
        self.assertEqual(first["source"], SOURCE_ALERTS)
        self.assertEqual(first["source_id"], "alert-1")
        self.assertEqual(first["state"], "confirmed")
        self.assertIs(first["advisor_eligible"], True)
        self.assertEqual(first["impact_scope"], "subway_operations")
        self.assertEqual(first["description"], "A trains run with delays near Jay St")
        self.assertEqual(set(first["affected_route_ids"]), {"A", "B"})
        self.assertEqual(len(first["affected_route_ids"]), 2)
        self.assertEqual(len(first["affected_stop_ids"]), 24)
        self.assertEqual(first["expires_at"], float(FIXED_NOW + 3600))
        self.assertEqual(first["observed_at"], _iso(FIXED_NOW - 600))
        self.assertEqual(first["last_verified_at"], attempted_at)
        self.assertEqual(
            first["source_records"],
            [
                {
                    "source": SOURCE_ALERTS,
                    "source_id": "alert-1",
                    "source_url": ALERTS_URL,
                    "observed_at": _iso(FIXED_NOW - 600),
                }
            ],
        )
        self.assertEqual(second["description"], "C header only")
        self.assertIsNone(second["expires_at"])
        self.assertEqual(second["observed_at"], attempted_at)
        self.assertTrue(attempted_at.endswith("+00:00"))

    def test_start_and_end_timing_semantics(self):
        expected_attempted = _iso(FIXED_NOW)
        parsed = [
            {
                "alert_id": "a1",
                "header": "h1",
                "description": "d1",
                "route_ids": ["Q"],
                "start": FIXED_NOW - 600,
                "end": FIXED_NOW + 3600,
            },
            {
                "alert_id": "a2",
                "header": "h2",
                "description": "d2",
                "route_ids": ["B"],
                "start": float("nan"),
                "end": float("inf"),
            },
            {
                "alert_id": "a3",
                "header": "h3",
                "description": "d3",
                "route_ids": ["L"],
                "start": 0,
                "end": -5,
            },
            {
                "alert_id": "a4",
                "header": "h4",
                "description": "d4",
                "route_ids": ["G"],
                "start": None,
                "end": None,
            },
        ]
        incidents = {
            case["alert_id"]: normalize_alert(case, expected_attempted) for case in parsed
        }
        valid = incidents["a1"]
        self.assertEqual(valid["observed_at"], _iso(FIXED_NOW - 600))
        self.assertEqual(valid["expires_at"], float(FIXED_NOW + 3600))
        self.assertEqual(valid["last_verified_at"], expected_attempted)
        self.assertEqual(
            valid["source_records"],
            [
                {
                    "source": SOURCE_ALERTS,
                    "source_id": "a1",
                    "source_url": ALERTS_URL,
                    "observed_at": _iso(FIXED_NOW - 600),
                }
            ],
        )
        for alert_id in ("a2", "a3", "a4"):
            incident = incidents[alert_id]
            self.assertEqual(incident["observed_at"], expected_attempted)
            self.assertIsNone(incident["expires_at"])


class StalledNormalizationTests(unittest.TestCase):
    def test_unconfirmed_and_deterministic(self):
        attempted_at = _iso(FIXED_NOW)
        positions = [
            {
                "route_id": "Q",
                "trip_id": "q-trip-7",
                "stop_id": "D24N",
                "status": "STOPPED_AT",
                "timestamp": FIXED_NOW - 400,
            }
        ]
        stalled = [
            {
                "route_id": "Q",
                "stop_id": "D24N",
                "status": "STOPPED_AT",
                "stalled_minutes": 6,
            }
        ]
        incidents_a = normalize_stalled(stalled, positions, attempted_at, now=FIXED_NOW)
        incidents_b = normalize_stalled(stalled, positions, attempted_at, now=FIXED_NOW)
        self.assertEqual(len(incidents_a), 1)
        incident = incidents_a[0]
        self.assertEqual(incident["source"], SOURCE_GTFS_RT)
        self.assertTrue(incident["source_id"].startswith("stalled-"))
        self.assertEqual(incident["state"], "unconfirmed")
        self.assertIs(incident["advisor_eligible"], False)
        self.assertEqual(incident["impact_scope"], "subway_operations")
        self.assertEqual(incident["affected_route_ids"], ["Q"])
        self.assertEqual(incident["affected_stop_ids"], ["D24N"])
        self.assertEqual(incident["expires_at"], FIXED_NOW + STALLED_EXPIRY_S)
        self.assertIn("stale", incident["description"].lower())
        self.assertIn("Q", incident["description"])
        self.assertEqual(incidents_a, incidents_b)


class DeterministicIdentityTests(unittest.TestCase):
    def _alert_incident(self, source_id: str, *, description: str = "same") -> dict:
        return {
            "source": SOURCE_ALERTS,
            "source_id": source_id,
            "state": "confirmed",
            "advisor_eligible": True,
            "impact_scope": "subway_operations",
            "description": description,
            "observed_at": _iso(FIXED_NOW),
            "last_verified_at": _iso(FIXED_NOW),
            "expires_at": None,
            "source_records": [],
            "affected_route_ids": ["Q"],
            "affected_stop_ids": [],
        }

    def test_dedupe_is_deterministic(self):
        incidents = [
            self._alert_incident("dup-1"),
            self._alert_incident("dup-1"),
            self._alert_incident("other", description="other"),
        ]
        deduped = dedupe_incidents(incidents)
        self.assertEqual(len(deduped), 2)
        self.assertEqual([i["source_id"] for i in deduped], ["dup-1", "other"])
        self.assertEqual(deduped, dedupe_incidents(incidents))

    def test_same_source_id_across_sources_stays_distinct(self):
        stalled = {
            "source": SOURCE_GTFS_RT,
            "source_id": "dup-1",
            "state": "unconfirmed",
            "advisor_eligible": False,
            "impact_scope": "subway_operations",
            "description": "stalled",
            "observed_at": _iso(FIXED_NOW),
            "last_verified_at": _iso(FIXED_NOW),
            "expires_at": FIXED_NOW + STALLED_EXPIRY_S,
            "source_records": [],
            "affected_route_ids": ["Q"],
            "affected_stop_ids": ["D24N"],
        }
        deduped = dedupe_incidents([self._alert_incident("dup-1"), stalled])
        self.assertEqual(len(deduped), 2)
        self.assertEqual({i["source"] for i in deduped}, {SOURCE_ALERTS, SOURCE_GTFS_RT})


class CanonicalShapeTests(unittest.TestCase):
    def test_raw_provider_fields_never_leak(self):
        attempted_at = _iso(FIXED_NOW)
        raw_alert = {
            "alert_id": "a1",
            "header": "h",
            "description": "d",
            "route_ids": ["Q"],
            "stop_ids": ["D24"],
            "start": 100,
            "end": 200,
            "nested": {"raw": "payload"},
            "coordinates": [1.0, 2.0],
            "payload": b"raw bytes",
        }
        raw_position = {
            "route_id": "Q",
            "stop_id": "D24N",
            "status": "STOPPED_AT",
            "trip_id": "t1",
            "timestamp": FIXED_NOW - 400,
            "nested": {"raw": "payload"},
            "lat": 1.0,
            "lng": 2.0,
            "position_source": "vehicle_position",
        }
        incidents = [
            normalize_alert(raw_alert, attempted_at),
            normalize_stalled([raw_position], [raw_position], attempted_at, now=FIXED_NOW)[0],
        ]
        self.assertEqual(len(incidents), 2)
        for incident in incidents:
            self.assertTrue(set(incident) <= _CANONICAL_KEYS, incident)
            for key, value in incident.items():
                if key in {"affected_route_ids", "affected_stop_ids"}:
                    self.assertTrue(all(isinstance(item, str) for item in value))
                elif key == "source_records":
                    for record in value:
                        self.assertLessEqual(
                            set(record), {"source", "source_id", "source_url", "observed_at"}
                        )
                        self.assertTrue(all(isinstance(item, str) for item in record.values()))
                else:
                    self.assertNotIsInstance(
                        value, (dict, list, tuple, set, bytes, bytearray)
                    )
        alert_incident, stalled_incident = incidents
        self.assertNotIn("nested", alert_incident)
        self.assertNotIn("nested", stalled_incident)
