"""Focused contract tests for the bounded xAI incident researcher."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.services import incident_monitor
from app.services.trips import incidents as trip_incidents
from app.services.trips.incident_context import (
    CandidateStopAssociation,
    CandidateStopContext,
)


class _Response:
    def __init__(self, content: str, *, citations: list[dict] | None = None) -> None:
        self.content = content
        self.citations = citations or []
        self.tool_calls = [
            SimpleNamespace(call_type="x_search_tool"),
            SimpleNamespace(call_type="web_search_tool"),
        ]


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.appended: list[object] = []
        self.samples = 0

    def append(self, value: object) -> None:
        self.appended.append(value)

    async def sample(self) -> _Response:
        self.samples += 1
        return self.responses.pop(0)


class _Chat:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.session


def _context(*, latitude: float = 40.650, longitude: float = -73.963) -> CandidateStopContext:
    return CandidateStopContext(
        "D24",
        "Church Av",
        latitude,
        longitude,
        [
            CandidateStopAssociation(
                "candidate-0",
                mode="subway",
                route_id="Q",
                segment_context="Church Av -> Atlantic Av-Barclays Ctr",
            )
        ],
    )


class IncidentMonitorTests(unittest.IsolatedAsyncioTestCase):
    def test_corridors_include_every_candidate_stop_in_contiguous_segments(self):
        contexts = [_context(), CandidateStopContext(
            "D26",
            "Prospect Park",
            40.661,
            -73.962,
            [CandidateStopAssociation(
                "candidate-0",
                mode="subway",
                route_id="Q",
                segment_context="Church Av -> Atlantic Av-Barclays Ctr",
            )],
        )]

        corridors = json.loads(incident_monitor._route_corridors(contexts))

        self.assertEqual(corridors[0]["candidate_route_id"], "candidate-0")
        self.assertEqual(
            [stop["name"] for stop in corridors[0]["corridors"][0]["stops"]],
            ["Church Av", "Prospect Park"],
        )
        self.assertTrue(
            all(stop["stop_ref"].startswith("sr_") for stop in corridors[0]["corridors"][0]["stops"])
        )

    def test_build_context_includes_static_intermediate_stops(self):
        rows = [
            {"stop_id": "D24", "name": "Church Av", "lat": 40.650, "lng": -73.963},
            {"stop_id": "D25", "name": "Beverley Rd", "lat": 40.645, "lng": -73.961},
            {"stop_id": "D26", "name": "Cortelyou Rd", "lat": 40.640, "lng": -73.961},
        ]
        gtfs = SimpleNamespace(_pattern_index=SimpleNamespace(
            get_intermediate_stops_with_coords=lambda *_args: (rows, {})
        ))
        routes = [[{
            "type": "SUBWAY",
            "route_id": "Q",
            "departure_stop": "Church Av",
            "arrival_stop": "Cortelyou Rd",
            "departure_coords": {"lat": 40.650, "lng": -73.963},
            "arrival_coords": {"lat": 40.640, "lng": -73.961},
        }]]

        contexts = trip_incidents.build_candidate_stop_context(gtfs, routes)

        self.assertEqual(
            {context.stop_name for context in contexts},
            {"Church Av", "Beverley Rd", "Cortelyou Rd"},
        )

    def test_normalization_requires_exact_stop_reference_nyc_freshness_and_citation(self):
        now = datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
        citation = "https://example.test/report"
        context = _context()
        valid = {
            "location": "Church Avenue",
            "stop_ref": context.stop_reference,
            "nearby_station": "Church Av",
            "severity": "high",
            "description": "Station access is restricted.",
            "impact_scope": "station_access",
            "evidence": [{
                "source_type": "web_search",
                "source_url": citation,
                "source_origin": "Example News",
                "observed_at": now.isoformat(),
            }],
        }
        result = incident_monitor._normalize_incident_payload(
            {"incidents": [
                valid,
                {**valid, "stop_ref": "sr_unknown"},
                {**valid, "evidence": [{**valid["evidence"][0], "observed_at": (now - timedelta(hours=7)).isoformat()}]},
            ]},
            [context],
            {citation},
            now=now,
        )
        outside_nyc = incident_monitor._normalize_incident_payload(
            {"incidents": [{**valid, "stop_ref": _context(latitude=41.0).stop_reference}]},
            [_context(latitude=41.0)],
            {citation},
            now=now,
        )

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["advisor_eligible"])
        self.assertEqual(outside_nyc, [])

    def test_duplicate_declared_origin_cannot_make_an_advisor_incident(self):
        now = datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
        first = "https://x.com/original/status/1"
        second = "https://news.example.test/report"
        context = _context()
        incident = {
            "location": "Church Avenue",
            "stop_ref": context.stop_reference,
            "nearby_station": "Church Av",
            "severity": "high",
            "description": "A verified station-access restriction.",
            "impact_scope": "station_access",
            "evidence": [
                {"source_type": "x_search", "source_url": first, "source_origin": "@original", "observed_at": now.isoformat()},
                {"source_type": "web_search", "source_url": second, "source_origin": "@original", "observed_at": now.isoformat()},
            ],
        }
        duplicate = incident_monitor._normalize_incident_payload(
            {"incidents": [incident]}, [context], {first, second}, now=now
        )[0]
        independently_reported = incident_monitor._normalize_incident_payload(
            {"incidents": [{**incident, "evidence": [
                incident["evidence"][0],
                {**incident["evidence"][1], "source_origin": "Independent Outlet"},
            ]}]},
            [context],
            {first, second},
            now=now,
        )[0]

        self.assertEqual(len(duplicate["evidence"]), 2)
        self.assertTrue(duplicate["corroborated"])
        self.assertFalse(duplicate["advisor_eligible"])
        self.assertTrue(independently_reported["corroborated"])
        self.assertTrue(independently_reported["advisor_eligible"])

    def test_evidence_identity_rejects_relabels_and_source_type_mismatches(self):
        now = datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
        x_url = "https://x.com/citydesk/status/1"
        web_url = "https://news.example.test/report"
        context = _context()
        base = {
            "location": "Church Avenue",
            "stop_ref": context.stop_reference,
            "nearby_station": "Church Av",
            "severity": "high",
            "description": "Station access is restricted.",
            "impact_scope": "station_access",
        }
        relabeled = incident_monitor._normalize_incident_payload(
            {"incidents": [{**base, "evidence": [
                {"source_type": "x_search", "source_url": x_url, "source_origin": "@citydesk", "observed_at": now.isoformat()},
                {"source_type": "x_search", "source_url": x_url, "source_origin": "Independent Outlet", "observed_at": now.isoformat()},
            ]}]},
            [context],
            {x_url},
            now=now,
        )[0]
        mismatched = incident_monitor._normalize_incident_payload(
            {"incidents": [{**base, "evidence": [
                {"source_type": "x_search", "source_url": web_url, "source_origin": "News", "observed_at": now.isoformat()},
            ]}]},
            [context],
            {web_url},
            now=now,
        )

        self.assertEqual(len(relabeled["evidence"]), 1)
        self.assertFalse(relabeled["advisor_eligible"])
        self.assertEqual(mismatched, [])

    def test_exact_reference_and_scope_filter_prevent_cross_association(self):
        now = datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
        first = CandidateStopContext(
            "Q01",
            "Main St",
            40.650,
            -73.963,
            [
                CandidateStopAssociation("candidate-subway", mode="subway", route_id="Q"),
                CandidateStopAssociation("candidate-bus", mode="bus", route_id="B35"),
                CandidateStopAssociation("candidate-walk", mode="walk"),
            ],
        )
        same_name_elsewhere = CandidateStopContext(
            "R99",
            "Main St",
            40.720,
            -73.990,
            [CandidateStopAssociation("candidate-other", mode="subway", route_id="R")],
        )
        evidence = [
            {"source_type": "x_search", "source_url": "https://x.com/nycdesk/status/9", "source_origin": "@nycdesk", "observed_at": now.isoformat()},
            {"source_type": "web_search", "source_url": "https://news.example.test/9", "source_origin": "Independent News", "observed_at": now.isoformat()},
        ]
        citations = {entry["source_url"] for entry in evidence}

        def normalize(scope: str) -> dict:
            return incident_monitor._normalize_incident_payload(
                {"incidents": [{
                    "location": "Main Street",
                    "stop_ref": first.stop_reference,
                    "nearby_station": "Main St",
                    "severity": "high",
                    "description": "A verified restriction.",
                    "impact_scope": scope,
                    "evidence": evidence,
                }]},
                [first, same_name_elsewhere],
                citations,
                now=now,
            )[0]

        subway = normalize("subway_operations")
        bus = normalize("bus_corridor")
        walking = normalize("walking")
        nearby = normalize("nearby")

        self.assertEqual(subway["affected_candidate_route_ids"], ["candidate-subway"])
        self.assertEqual(bus["affected_candidate_route_ids"], ["candidate-bus"])
        self.assertEqual(walking["affected_candidate_route_ids"], ["candidate-walk"])
        self.assertFalse(nearby["advisor_eligible"])
        self.assertNotIn("candidate-other", subway["affected_candidate_route_ids"])

    async def test_agent_uses_parallel_x_and_web_tools_with_two_turn_bound(self):
        now = datetime.now(timezone.utc)
        source = "https://x.com/citysource/status/1"
        context = _context()
        response = _Response(json.dumps({"incidents": [{
            "location": "Church Avenue",
            "stop_ref": context.stop_reference,
            "nearby_station": "Church Av",
            "severity": "high",
            "description": "Station access is restricted.",
            "impact_scope": "station_access",
            "evidence": [
                {"source_type": "x_search", "source_url": source, "source_origin": "@citysource", "observed_at": now.isoformat()},
                {"source_type": "web_search", "source_url": "https://news.example.test/current", "source_origin": "Independent Outlet", "observed_at": now.isoformat()},
            ],
        }]}), citations=[{"url": source}, {"url": "https://news.example.test/current"}])
        session = _Session([response])
        chat = _Chat(session)
        fake_client = SimpleNamespace(chat=chat)
        x_kwargs: dict = {}
        web_kwargs: dict = {}

        with patch.object(incident_monitor, "client", fake_client), patch.object(
            incident_monitor, "system", lambda value: value
        ), patch.object(incident_monitor, "user", lambda value: value), patch.object(
            incident_monitor, "get_tool_call_type", lambda call: call.call_type
        ), patch.object(
            incident_monitor, "x_search", lambda **kwargs: x_kwargs.update(kwargs) or "x"
        ), patch.object(
            incident_monitor, "web_search", lambda **kwargs: web_kwargs.update(kwargs) or "web"
        ):
            result = await incident_monitor._run_incident_agent([context])

        self.assertEqual(result["scan_metadata"]["status"], "complete")
        self.assertEqual(result["scan_metadata"]["tool_rounds"], 1)
        self.assertEqual(session.samples, 1)
        self.assertTrue(chat.kwargs["parallel_tool_calls"])
        self.assertEqual(chat.kwargs["max_turns"], 2)
        self.assertLessEqual(x_kwargs["to_date"] - x_kwargs["from_date"], timedelta(hours=6))
        self.assertEqual(web_kwargs["user_location_city"], "New York")
        self.assertTrue(result["incidents"][0]["advisor_eligible"])

    async def test_missing_provider_returns_disabled_contract(self):
        with patch.object(incident_monitor, "client", None):
            result = await incident_monitor.get_incidents([_context()])

        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["scan_metadata"]["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
