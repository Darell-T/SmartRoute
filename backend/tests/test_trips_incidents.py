"""Focused tests for the rider incident-index lookup adapter.

Covers the single immediate index lookup, deterministic candidate
association, coverage-truthful metadata, and the static guarantee that the
normal rider path never scans providers.
"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import incident_index
from app.services.trips import incidents
from app.services.trips import incident_index_adapter
from app.services.trips.incident_context import (
    CandidateStopAssociation,
    CandidateStopContext,
)
from app.utils import cache


def _context(
    stop_id: str = "D24",
    name: str = "Church Av",
    latitude: float = 40.650,
    longitude: float = -73.963,
    route_id: str = "Q",
    candidate: str = "candidate-0",
) -> CandidateStopContext:
    return CandidateStopContext(
        stop_id,
        name,
        latitude,
        longitude,
        [CandidateStopAssociation(candidate, mode="subway", route_id=route_id)],
    )


def _incident_key(incident_id: str) -> str:
    return f"{incident_index.INCIDENT_PREFIX}{incident_id}"


def _expire_record(key: str) -> None:
    record = json.loads(cache.cache_get(key))
    record["expires_at"] = time.time() - 60
    cache.cache_set(key, json.dumps(record), 3600)


class TripIncidentIndexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_client = cache.redis_client
        cache.redis_client = None
        cache._mem.clear()

    def tearDown(self) -> None:
        cache.redis_client = self._original_client
        cache._mem.clear()

    async def test_repeated_upsert_only_changes_new_index_memberships(self) -> None:
        incident = {
            "source": "mta_alerts",
            "source_id": "stable-indexes",
            "affected_route_ids": ["Q"],
            "affected_stop_ids": ["D24"],
        }
        incident_index.upsert_incident(incident)

        with patch.object(
            incident_index,
            "_update_index",
            wraps=incident_index._update_index,
        ) as update_index:
            incident_index.upsert_incident({**incident, "description": "new details"})
            update_index.assert_not_called()
            incident_index.upsert_incident({**incident, "affected_route_ids": ["B"]})

        self.assertEqual(update_index.call_count, 2)

    async def test_one_immediate_index_lookup_with_correct_tokens(self) -> None:
        calls: list[dict] = []
        real_lookup = incident_index.lookup_incidents_async

        async def spy(**kwargs):
            calls.append(kwargs)
            return await real_lookup(**kwargs)

        with patch.object(incidents.incident_index, "lookup_incidents_async", side_effect=spy):
            result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["stop_ids"], ["D24"])
        self.assertEqual(calls[0]["route_ids"], ["Q"])
        self.assertEqual(calls[0]["coverage_ids"], ["central-south-brooklyn"])
        metadata = result["scan_metadata"]
        self.assertEqual(metadata["lookup_kind"], "index")
        self.assertEqual(metadata["requested_coverage_ids"], ["central-south-brooklyn"])
        self.assertEqual(metadata["lookup_status"], "complete")
        self.assertEqual(
            metadata["sources"],
            {"attempted": ["incident_index"], "completed": ["incident_index"]},
        )

    async def test_confirmed_batch_and_route_incident_reaches_advisor(self) -> None:
        incident_index.set_coverage(
            {"coverage_id": "central-south-brooklyn", "coverage_status": "current"}
        )
        incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "advisor-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "station_access",
                "severity": "high",
                "location_name": "Church Avenue",
                "description": "Emergency response at the station entrance.",
                "affected_stop_ids": ["D24"],
                "affected_route_ids": ["Q"],
                "affected_batch_ids": ["central-south-brooklyn"],
                "source_coverage": ["x_search", "web_search"],
                "corroboration_state": "corroborated",
            }
        )

        result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(len(result["incidents"]), 1)
        advisor = result["incidents"][0]
        self.assertEqual(advisor["affected_candidate_route_ids"], ["candidate-0"])
        self.assertTrue(advisor["advisor_eligible"])
        self.assertEqual(advisor["state"], "confirmed")
        self.assertTrue(advisor["incident_id"].startswith("inc_"))
        self.assertEqual(advisor["source"], "x_search + web_search")
        self.assertEqual(advisor["affected_modes"], ["subway"])
        metadata = result["scan_metadata"]
        self.assertEqual(metadata["coverage_status"], "current")
        self.assertEqual(metadata["status"], "complete")
        self.assertEqual(metadata["warning_count"], 0)
        self.assertTrue(incidents.incident_lookup_succeeded(metadata))
        self.assertTrue(incidents.incident_scan_is_complete(metadata))

    async def test_confirmed_official_single_source_record_is_corroborated_and_eligible(self) -> None:
        incident_index.upsert_incident(
            {
                "source": "mta_alerts",
                "source_id": "official-confirmed-1",
                "state": "confirmed",
                "corroboration_state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "subway_operations",
                "severity": "high",
                "description": "Q train service change.",
                "affected_route_ids": ["Q"],
                "source_records": [
                    {
                        "source": "MTA Alerts",
                        "source_id": "mta-1",
                        "source_url": "https://alerts.mta.info",
                    }
                ],
            }
        )

        result = await incidents.scan_route_incidents([_context()])

        # A confirmed official record is verified evidence even with a single
        # authoritative source record; corroboration comes from canonical
        # state, never from the number of source records.
        self.assertEqual(len(result["incidents"]), 1)
        advisor = result["incidents"][0]
        self.assertTrue(advisor["advisor_eligible"])
        self.assertEqual(advisor["state"], "confirmed")
        self.assertTrue(advisor["corroborated"])
        self.assertEqual(len(advisor["source_records"]), 1)
        self.assertEqual(advisor["source_records"][0]["source"], "MTA Alerts")

    async def test_two_unconfirmed_source_records_do_not_imply_corroboration(self) -> None:
        incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "double-unconfirmed-1",
                "state": "unconfirmed",
                "advisor_eligible": True,
                "impact_scope": "station_access",
                "description": "Two unverified reports.",
                "affected_stop_ids": ["D24"],
                "affected_batch_ids": ["central-south-brooklyn"],
                "source_records": [
                    {"source": "x_search", "source_id": "post-1"},
                    {"source": "x_search", "source_id": "post-2"},
                ],
            }
        )

        result = await incidents.scan_route_incidents([_context()])

        # Two same-origin/unconfirmed records are not proof: the record stays
        # a warning and never projects as corroborated.
        self.assertEqual(result["incidents"], [])
        self.assertEqual(len(result["warnings"]), 1)
        warning = result["warnings"][0]
        self.assertFalse(warning["advisor_eligible"])
        self.assertFalse(warning["corroborated"])
        self.assertEqual(len(warning["source_records"]), 2)

    async def test_route_only_official_incident_matches_by_route_without_batch(self) -> None:
        incident_index.upsert_incident(
            {
                "source": "mta_alerts",
                "source_id": "official-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "subway_operations",
                "description": "Q train service change.",
                "affected_route_ids": ["Q"],
            }
        )

        result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(len(result["incidents"]), 1)
        self.assertEqual(result["incidents"][0]["affected_candidate_route_ids"], ["candidate-0"])
        # Route-wide official evidence is usable even with no coverage record.
        self.assertEqual(result["scan_metadata"]["coverage_status"], "unscanned")
        self.assertEqual(result["scan_metadata"]["status"], "unscanned")
        self.assertTrue(incidents.incident_lookup_succeeded(result["scan_metadata"]))

    async def test_stop_specific_incident_matches_physical_stop(self) -> None:
        incident_index.upsert_incident(
            {
                "source": "ny511",
                "source_id": "stop-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "station_access",
                "description": "Elevator outage at Church Av.",
                "affected_stop_ids": ["D24"],
                "affected_batch_ids": ["central-south-brooklyn"],
            }
        )

        result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(len(result["incidents"]), 1)
        self.assertEqual(result["incidents"][0]["affected_candidate_route_ids"], ["candidate-0"])

    async def test_batch_mismatch_keeps_incident_out_of_advisor(self) -> None:
        incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "wrong-batch-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "subway_operations",
                "description": "Q delay in Upper Manhattan.",
                "affected_route_ids": ["Q"],
                "affected_batch_ids": ["upper-manhattan"],
            }
        )

        result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["warnings"][0]["affected_candidate_route_ids"], [])
        self.assertFalse(result["warnings"][0]["advisor_eligible"])

    async def test_batch_only_incident_is_a_warning_not_advisor_evidence(self) -> None:
        incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "batch-only-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "nearby",
                "description": "Police activity in the area.",
                "affected_batch_ids": ["central-south-brooklyn"],
            }
        )

        result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["scan_metadata"]["warning_count"], 1)

    async def test_unconfirmed_and_stale_records_remain_warnings(self) -> None:
        unconfirmed_id = incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "unconfirmed-1",
                "state": "unconfirmed",
                "advisor_eligible": True,
                "impact_scope": "station_access",
                "description": "Unverified report.",
                "affected_stop_ids": ["D24"],
                "affected_batch_ids": ["central-south-brooklyn"],
            }
        )
        stale_id = incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "stale-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "station_access",
                "description": "Expired confirmed report.",
                "affected_stop_ids": ["D24"],
                "affected_batch_ids": ["central-south-brooklyn"],
            }
        )
        _expire_record(_incident_key(stale_id))

        result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(len(result["warnings"]), 2)
        states = {warning["state"] for warning in result["warnings"]}
        self.assertEqual(states, {"unconfirmed", "stale"})
        self.assertTrue(unconfirmed_id in {w["incident_id"] for w in result["warnings"]})

    async def test_rejected_and_resolved_records_stay_filtered(self) -> None:
        rejected_id = incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "rejected-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "station_access",
                "description": "Rejected report.",
                "affected_stop_ids": ["D24"],
                "affected_batch_ids": ["central-south-brooklyn"],
            }
        )
        incident_index.set_incident_state(rejected_id, "rejected")
        resolved_id = incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "resolved-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "station_access",
                "description": "Resolved report.",
                "affected_stop_ids": ["D24"],
                "affected_batch_ids": ["central-south-brooklyn"],
            }
        )
        incident_index.set_incident_state(resolved_id, "resolved")

        result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["warnings"], [])

    async def test_confirmed_eligible_usable_when_overall_coverage_is_partial(self) -> None:
        overlap = _context(stop_id="W4", name="Overlap", latitude=40.72, longitude=-73.96)
        incident_index.set_coverage({"coverage_id": "lower-manhattan", "coverage_status": "current"})
        incident_index.set_coverage(
            {"coverage_id": "downtown-northwest-brooklyn", "coverage_status": "current"}
        )
        incident_index.set_coverage({"coverage_id": "western-queens", "coverage_status": "unavailable"})
        incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "partial-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "impact_scope": "subway_operations",
                "description": "Confirmed indexed incident.",
                "affected_stop_ids": ["W4"],
                "affected_route_ids": ["Q"],
                "affected_batch_ids": ["lower-manhattan"],
            }
        )

        result = await incidents.scan_route_incidents([overlap])

        self.assertEqual(len(result["incidents"]), 1)
        metadata = result["scan_metadata"]
        self.assertEqual(metadata["coverage_status"], "partial")
        self.assertEqual(metadata["status"], "partial")
        self.assertTrue(incidents.incident_lookup_succeeded(metadata))
        self.assertFalse(incidents.incident_scan_is_complete(metadata))

    async def test_truthful_empty_and_coverage_states(self) -> None:
        unscanned = await incidents.scan_route_incidents([_context()])
        self.assertEqual(unscanned["scan_metadata"]["coverage_status"], "unscanned")
        self.assertEqual(unscanned["scan_metadata"]["status"], "unscanned")
        self.assertEqual(unscanned["incidents"], [])

        incident_index.set_coverage(
            {"coverage_id": "central-south-brooklyn", "coverage_status": "current"}
        )
        current = await incidents.scan_route_incidents([_context()])
        self.assertEqual(current["scan_metadata"]["coverage_status"], "current")
        self.assertEqual(current["scan_metadata"]["status"], "complete")
        self.assertEqual(current["incidents"], [])

        incident_index.set_coverage(
            {"coverage_id": "central-south-brooklyn", "coverage_status": "unavailable"}
        )
        unavailable = await incidents.scan_route_incidents([_context()])
        self.assertEqual(unavailable["scan_metadata"]["coverage_status"], "unavailable")
        self.assertEqual(unavailable["scan_metadata"]["status"], "unavailable")

        incident_index.set_coverage(
            {"coverage_id": "central-south-brooklyn", "coverage_status": "current"}
        )
        _expire_record(f"{incident_index.COVERAGE_PREFIX}central-south-brooklyn")
        stale = await incidents.scan_route_incidents([_context()])
        self.assertEqual(stale["scan_metadata"]["coverage_status"], "stale")
        self.assertEqual(stale["scan_metadata"]["status"], "stale")

    async def test_empty_context_is_unscanned_and_malformed_entries_are_ignored(self) -> None:
        empty = await incidents.scan_route_incidents([])
        self.assertEqual(empty["scan_metadata"]["status"], "unscanned")
        self.assertEqual(empty["scan_metadata"]["coverage_status"], "unscanned")
        self.assertEqual(empty["incidents"], [])
        self.assertEqual(empty["scan_metadata"]["requested_coverage_ids"], [])

        mixed = await incidents.scan_route_incidents(
            [object(), {"type": "SUBWAY", "route_id": "Q"}, "junk", _context()]
        )
        self.assertEqual(mixed["scan_metadata"]["requested_coverage_ids"], ["central-south-brooklyn"])
        self.assertEqual(mixed["scan_metadata"]["status"], "unscanned")

    async def test_index_exception_degrades_without_any_cold_scan(self) -> None:
        with patch.object(
            incidents.incident_index,
            "lookup_incidents_async",
            side_effect=RuntimeError("cache unavailable"),
        ):
            result = await incidents.scan_route_incidents([_context()])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["warnings"], [])
        metadata = result["scan_metadata"]
        self.assertEqual(metadata["lookup_status"], "failed")
        self.assertEqual(metadata["coverage_status"], "unavailable")
        self.assertEqual(metadata["status"], "unavailable")
        self.assertFalse(incidents.incident_lookup_succeeded(metadata))
        self.assertFalse(incidents.incident_scan_is_complete(metadata))
        self.assertEqual(
            metadata["sources"],
            {"attempted": ["incident_index"], "completed": []},
        )

    def test_normal_rider_modules_have_no_provider_scan_path(self) -> None:
        for module in (incidents, incident_index_adapter):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for forbidden in (
                "incident_monitor",
                "get_incidents",
                "x_search",
                "web_search",
                "incident_scan_cache",
                "_inflight_scans",
                "asyncio",
            ):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
