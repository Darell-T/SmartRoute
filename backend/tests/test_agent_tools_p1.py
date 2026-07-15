"""Layer-1 tests for the P1 agent tools (event_lookup, poi_search,
venue_crowd_window), the venues.py static data module, and the fixture-replay
dispatch hook in tools/__init__.py.

Follows test_agent_tools.py's convention: real imports work in this
environment, so only the actual I/O boundary each tool touches (httpx) is
mocked -- never the whole module. httpx itself is mocked the same way
tests/test_directions.py does it: `patch.object(<module>.httpx, "AsyncClient",
<fake client class>)`, never a live network call.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.agent import tools as agent_tools
from app.services.agent import venues
from app.services.agent.tools import event_lookup, poi_search, venue_crowd_window
from app.services.agent.tools._types import ToolContext, ToolResult
from app.utils import cache


def _ctx(origin=None) -> ToolContext:
    return ToolContext(gtfs=None, session={}, turn_id="t1", now_et="2026-07-15T21:00:00-04:00", origin=origin)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _recording_get_client(payload, status_code: int = 200):
    class _Client:
        requests: list[dict] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, params=None):
            type(self).requests.append({"url": url, "params": params})
            return _FakeResponse(payload, status_code)

    _Client.requests = []
    return _Client


def _recording_post_client(payload, status_code: int = 200):
    class _Client:
        requests: list[dict] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json=None, headers=None):
            type(self).requests.append({"url": url, "json": json, "headers": headers})
            return _FakeResponse(payload, status_code)

    _Client.requests = []
    return _Client


def _ticketmaster_event(
    name="Knicks vs Celtics",
    venue_name="Madison Square Garden",
    date_time="2026-07-16T23:00:00Z",
    segment="Sports",
    genre="Basketball",
    sub_genre="NBA",
):
    return {
        "name": name,
        "dates": {"start": {"dateTime": date_time}},
        "classifications": [
            {
                "segment": {"name": segment},
                "genre": {"name": genre},
                "subGenre": {"name": sub_genre},
            }
        ],
        "_embedded": {"venues": [{"name": venue_name}]},
    }


class EventLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()
        self._env = patch.dict(os.environ, {"TICKETMASTER_API_KEY": "tm-key"}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    async def test_unconfigured_key_returns_clean_error(self):
        with patch.dict(os.environ, {"TICKETMASTER_API_KEY": ""}, clear=False):
            result = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "event lookup is not configured")

    async def test_query_required(self):
        result = await event_lookup.execute({"query": ""}, _ctx())
        self.assertFalse(result.ok)

    async def test_successful_parse_and_duration_heuristic(self):
        payload = {"_embedded": {"events": [_ticketmaster_event()]}}
        client_class = _recording_get_client(payload)
        with patch.object(event_lookup.httpx, "AsyncClient", client_class):
            result = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertTrue(result.ok)
        event = result.data["events"][0]
        self.assertEqual(event["name"], "Knicks vs Celtics")
        self.assertEqual(event["venue_name"], "Madison Square Garden")
        self.assertEqual(event["venue_key"], "msg")
        self.assertEqual(event["start_iso"], "2026-07-16T23:00:00Z")
        # NBA -> 2h30m
        self.assertEqual(event["estimated_end_iso"], "2026-07-17T01:30:00Z")
        self.assertIn("2h30m", event["end_estimate_basis"])
        self.assertIn("note", result.data)

    async def test_venue_key_normalization_unknown_venue_is_none(self):
        payload = {"_embedded": {"events": [_ticketmaster_event(venue_name="Some Random Bar")]}}
        client_class = _recording_get_client(payload)
        with patch.object(event_lookup.httpx, "AsyncClient", client_class):
            result = await event_lookup.execute({"query": "local show"}, _ctx())
        self.assertIsNone(result.data["events"][0]["venue_key"])

    async def test_date_maps_to_utc_day_bounds_params(self):
        payload = {"_embedded": {"events": []}}
        client_class = _recording_get_client(payload)
        with patch.object(event_lookup.httpx, "AsyncClient", client_class):
            await event_lookup.execute({"query": "Knicks", "date": "2026-07-16"}, _ctx())
        params = client_class.requests[0]["params"]
        self.assertIn("startDateTime", params)
        self.assertIn("endDateTime", params)
        # America/New_York midnight on 2026-07-16 is 04:00Z.
        self.assertEqual(params["startDateTime"], "2026-07-16T04:00:00Z")

    async def test_cache_hit_skips_second_http_call(self):
        payload = {"_embedded": {"events": [_ticketmaster_event()]}}
        client_class = _recording_get_client(payload)
        with patch.object(event_lookup.httpx, "AsyncClient", client_class):
            first = await event_lookup.execute({"query": "Knicks"}, _ctx())
            second = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(len(client_class.requests), 1)
        self.assertEqual(second.data, first.data)

    async def test_malformed_response_is_reported_without_traceback(self):
        client_class = _recording_get_client({"_embedded": {"events": "not-a-list"}})
        with patch.object(event_lookup.httpx, "AsyncClient", client_class):
            result = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertFalse(result.ok)
        self.assertNotIn("Traceback", result.error or "")

    async def test_non_dict_events_payload_is_reported(self):
        client_class = _recording_get_client({"_embedded": {"events": [None]}})
        with patch.object(event_lookup.httpx, "AsyncClient", client_class):
            result = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertFalse(result.ok)


class PoiSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env = patch.dict(
            os.environ, {"GOOGLE_PLACES_API_KEY": "places-key", "GOOGLE_ROUTES_API_KEY": ""}, clear=False
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def _place(self, name="Joe's Pizza", lat=40.7308, lng=-73.9973, open_now=True, address="7 Carmine St"):
        entry = {
            "displayName": {"text": name},
            "formattedAddress": address,
            "location": {"latitude": lat, "longitude": lng},
        }
        if open_now is not None:
            entry["currentOpeningHours"] = {"openNow": open_now}
        return entry

    async def test_unconfigured_when_neither_key_set(self):
        with patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "", "GOOGLE_ROUTES_API_KEY": ""}, clear=False):
            result = await poi_search.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "place search is not configured")

    async def test_key_fallback_to_routes_key_when_places_key_unset(self):
        payload = {"places": [self._place()]}
        client_class = _recording_post_client(payload)
        with patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "", "GOOGLE_ROUTES_API_KEY": "routes-key"}, clear=False):
            with patch.object(poi_search.httpx, "AsyncClient", client_class):
                result = await poi_search.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        self.assertTrue(result.ok)
        self.assertEqual(client_class.requests[0]["headers"]["X-Goog-Api-Key"], "routes-key")

    async def test_places_key_preferred_over_routes_key(self):
        payload = {"places": [self._place()]}
        client_class = _recording_post_client(payload)
        with patch.dict(os.environ, {"GOOGLE_ROUTES_API_KEY": "routes-key"}, clear=False):
            with patch.object(poi_search.httpx, "AsyncClient", client_class):
                await poi_search.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        self.assertEqual(client_class.requests[0]["headers"]["X-Goog-Api-Key"], "places-key")

    async def test_results_outside_nyc_bounds_are_dropped(self):
        payload = {
            "places": [
                self._place(name="In NYC", lat=40.73, lng=-73.99),
                self._place(name="In Boston", lat=42.36, lng=-71.06),
            ]
        }
        client_class = _recording_post_client(payload)
        with patch.object(poi_search.httpx, "AsyncClient", client_class):
            result = await poi_search.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        self.assertTrue(result.ok)
        names = [r["name"] for r in result.data["results"]]
        self.assertEqual(names, ["In NYC"])

    async def test_max_results_is_clamped(self):
        payload = {"places": [self._place(name=f"Place {i}") for i in range(10)]}
        client_class = _recording_post_client(payload)
        with patch.object(poi_search.httpx, "AsyncClient", client_class):
            result = await poi_search.execute(
                {"query": "pizza", "max_results": 999}, _ctx(origin={"lat": 40.7, "lng": -73.9})
            )
        self.assertEqual(client_class.requests[0]["json"]["maxResultCount"], 5)
        self.assertLessEqual(len(result.data["results"]), 5)

    async def test_open_now_is_none_when_absent(self):
        payload = {"places": [self._place(open_now=None)]}
        client_class = _recording_post_client(payload)
        with patch.object(poi_search.httpx, "AsyncClient", client_class):
            result = await poi_search.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        self.assertIsNone(result.data["results"][0]["open_now"])

    async def test_near_user_without_gps_asks_for_location(self):
        result = await poi_search.execute({"query": "pizza", "near": "user"}, _ctx(origin=None))
        self.assertFalse(result.ok)
        self.assertIn("location", result.error.lower())


class VenueCrowdWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_window_arithmetic_from_offsets(self):
        result = await venue_crowd_window.execute(
            {"venue": "msg", "event_end_iso": "2026-07-16T22:00:00-04:00"}, _ctx()
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["surge_start_iso"], "2026-07-16T21:45:00-04:00")
        self.assertEqual(result.data["surge_end_iso"], "2026-07-16T22:50:00-04:00")

    async def test_all_six_venues_have_stations_and_lines(self):
        expected = {"msg", "barclays", "yankee_stadium", "citi_field", "penn_station", "port_authority"}
        self.assertEqual(set(venues.VENUE_CROWD_TABLE.keys()), expected)
        for venue_key in expected:
            result = await venue_crowd_window.execute(
                {"venue": venue_key, "event_end_iso": "2026-07-16T22:00:00-04:00"}, _ctx()
            )
            self.assertTrue(result.ok, venue_key)
            self.assertTrue(result.data["stations"])
            self.assertTrue(result.data["lines"])

    async def test_unparseable_time_is_an_error(self):
        result = await venue_crowd_window.execute({"venue": "msg", "event_end_iso": "not-a-time"}, _ctx())
        self.assertFalse(result.ok)

    async def test_naive_datetime_is_rejected(self):
        result = await venue_crowd_window.execute(
            {"venue": "msg", "event_end_iso": "2026-07-16T22:00:00"}, _ctx()
        )
        self.assertFalse(result.ok)

    async def test_unknown_venue_is_an_error(self):
        result = await venue_crowd_window.execute(
            {"venue": "not_a_venue", "event_end_iso": "2026-07-16T22:00:00-04:00"}, _ctx()
        )
        self.assertFalse(result.ok)

    async def test_is_heuristic_always_true(self):
        result = await venue_crowd_window.execute(
            {"venue": "barclays", "event_end_iso": "2026-07-16T22:00:00-04:00"}, _ctx()
        )
        self.assertIs(result.data["is_heuristic"], True)


class VenuesModuleTests(unittest.TestCase):
    def test_duration_heuristics_per_classification(self):
        cases = [
            (("Sports", "Basketball", "NBA"), "2h30m"),
            (("Sports", "Hockey", "NHL"), "2h30m"),
            (("Sports", "Baseball", "MLB"), "3h"),
            (("Sports", "Football", "NFL"), "3h15m"),
            (("Sports", "Soccer", ""), "2h15m"),
            (("Music", "Rock", ""), "3h"),
            (("", "", ""), "3h"),
        ]
        for classification, expected_suffix in cases:
            _duration, basis = venues.estimate_event_duration(*classification)
            self.assertIn(expected_suffix, basis, classification)

    def test_venue_name_aliases_normalize(self):
        self.assertEqual(venues.normalize_venue_name("Madison Square Garden"), "msg")
        self.assertEqual(venues.normalize_venue_name("The Garden"), "msg")
        self.assertEqual(venues.normalize_venue_name("MSG"), "msg")
        self.assertEqual(venues.normalize_venue_name("Barclays Center"), "barclays")
        self.assertEqual(venues.normalize_venue_name("Citi Field"), "citi_field")
        self.assertIsNone(venues.normalize_venue_name("Some Random Bar"))
        self.assertIsNone(venues.normalize_venue_name(None))


class FixtureReplayTests(unittest.IsolatedAsyncioTestCase):
    """Tests the dispatch hook directly (agent_tools._with_fixture_replay)
    rather than through TOOL_REGISTRY, so a fake in-memory executor can prove
    whether it was called without needing real tool I/O."""

    async def test_no_fixtures_dir_calls_real_executor(self):
        calls = []

        async def executor(tool_input, ctx):
            calls.append(tool_input)
            return ToolResult(ok=True, data={"real": True}, summary="ran for real")

        wrapped = agent_tools._with_fixture_replay("fake_tool", executor)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_TOOL_FIXTURES", None)
            result = await wrapped({"a": 1}, _ctx())
        self.assertTrue(result.ok)
        self.assertEqual(calls, [{"a": 1}])

    async def test_replay_returns_fixture_without_calling_executor(self):
        calls = []

        async def executor(tool_input, ctx):
            calls.append(tool_input)
            return ToolResult(ok=True, data={"real": True}, summary="should not run")

        with tempfile.TemporaryDirectory() as tmp:
            tool_input = {"query": "pizza"}
            fixture_hash = agent_tools._canonical_hash(tool_input)
            fixture_dir = Path(tmp) / "fake_tool"
            fixture_dir.mkdir(parents=True)
            (fixture_dir / f"{fixture_hash}.json").write_text(
                json.dumps({"ok": True, "data": {"fixture": True}, "summary": "from fixture", "error": None})
            )
            wrapped = agent_tools._with_fixture_replay("fake_tool", executor)
            with patch.dict(os.environ, {"AGENT_TOOL_FIXTURES": tmp}, clear=False):
                result = await wrapped(tool_input, _ctx())

        self.assertEqual(calls, [])
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"fixture": True})
        self.assertEqual(result.summary, "from fixture")

    async def test_missing_fixture_is_a_loud_error(self):
        async def executor(tool_input, ctx):
            return ToolResult(ok=True, data={"real": True})

        with tempfile.TemporaryDirectory() as tmp:
            wrapped = agent_tools._with_fixture_replay("fake_tool", executor)
            with patch.dict(os.environ, {"AGENT_TOOL_FIXTURES": tmp}, clear=False):
                result = await wrapped({"query": "nope"}, _ctx())

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "no fixture for this input")

    async def test_record_mode_calls_real_executor_then_writes_fixture(self):
        calls = []

        async def executor(tool_input, ctx):
            calls.append(tool_input)
            return ToolResult(ok=True, data={"real": True}, summary="ran for real")

        with tempfile.TemporaryDirectory() as tmp:
            tool_input = {"query": "pizza"}
            wrapped = agent_tools._with_fixture_replay("fake_tool", executor)
            with patch.dict(
                os.environ, {"AGENT_TOOL_FIXTURES": tmp, "AGENT_TOOL_FIXTURES_RECORD": "1"}, clear=False
            ):
                result = await wrapped(tool_input, _ctx())
                written_path = agent_tools._fixture_path(tmp, "fake_tool", tool_input)

            self.assertEqual(calls, [tool_input])
            self.assertTrue(result.ok)
            self.assertEqual(result.data, {"real": True})
            self.assertTrue(written_path.exists())
            written = json.loads(written_path.read_text())
            self.assertEqual(written, {"ok": True, "data": {"real": True}, "summary": "ran for real", "error": None})

    async def test_plan_trip_and_transit_snapshot_go_through_the_same_hook(self):
        # Registered executors are wrapped closures, not the bare module
        # functions -- proves the hook applies uniformly, not just to the
        # P1 tools.
        self.assertNotEqual(agent_tools.TOOL_REGISTRY["plan_trip"].executor, agent_tools.plan_trip.execute)
        self.assertNotEqual(
            agent_tools.TOOL_REGISTRY["transit_snapshot"].executor, agent_tools.transit_snapshot.execute
        )


class RegistryTests(unittest.TestCase):
    def test_all_five_tools_present_with_strict_schemas(self):
        expected = {"plan_trip", "transit_snapshot", "event_lookup", "poi_search", "venue_crowd_window"}
        self.assertEqual(set(agent_tools.TOOL_REGISTRY.keys()), expected)
        for name, spec in agent_tools.TOOL_REGISTRY.items():
            self.assertTrue(spec.schema.get("strict"), name)
            self.assertFalse(spec.schema["input_schema"].get("additionalProperties", True), name)
            self.assertGreater(spec.timeout_s, 0, name)

    def test_per_tool_timeouts(self):
        self.assertEqual(agent_tools.TOOL_REGISTRY["event_lookup"].timeout_s, 8.0)
        self.assertEqual(agent_tools.TOOL_REGISTRY["poi_search"].timeout_s, 8.0)
        self.assertEqual(agent_tools.TOOL_REGISTRY["venue_crowd_window"].timeout_s, 2.0)


if __name__ == "__main__":
    unittest.main()
