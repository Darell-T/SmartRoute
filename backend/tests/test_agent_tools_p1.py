"""Layer-1 tests for the P1 agent tools (event_lookup, search_local_places,
venue_crowd_window), the venues.py static data module, and the fixture-replay
dispatch hook in tools/__init__.py.

Follows test_agent_tools.py's convention: real imports work in this
environment, so only the actual I/O boundary each tool touches (httpx) is
mocked -- never the whole module. The shared HTTP boundary is patched with
`patch.object(_http.httpx, "AsyncClient", <fake client class>)` (both tools'
fetches go through the shared
`app.services.agent.tools.provider_http.fetch_json`, so patching `AsyncClient` there
covers either one), never a live network call. The fake client classes
themselves live in tests/_fake_http_tools.py, shared with test_agent_tools_p2.py.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import cache
from app.services.agent import tools as agent_tools
from app.services.agent.tools import provider_http as _http
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.places import search_local_places
from app.services.agent.tools.transit import check_transit
from app.services.agent.tools.transit import venue_crowd_window as venues
from app.services.trips.crowds import event_provider

from tests._fake_http_tools import make_tool_ctx as _ctx
from tests._fake_http_tools import recording_get_client as _recording_get_client
from tests._fake_http_tools import recording_post_client as _recording_post_client


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
            result = await check_transit.execute_event_lookup({"query": "Knicks"}, _ctx())
        assert not result.ok
        assert result.error == "event lookup is not configured"

    async def test_query_required(self):
        result = await check_transit.execute_event_lookup({"query": ""}, _ctx())
        assert not result.ok

    async def test_successful_parse_and_duration_heuristic(self):
        payload = {"_embedded": {"events": [_ticketmaster_event()]}}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await check_transit.execute_event_lookup({"query": "Knicks"}, _ctx())
        assert result.ok
        event = result.data["events"][0]
        assert event["name"] == "Knicks vs Celtics"
        assert event["venue_name"] == "Madison Square Garden"
        assert event["venue_key"] == "msg"
        assert event["start_iso"] == "2026-07-16T23:00:00Z"
        # NBA -> 2h30m
        assert event["estimated_end_iso"] == "2026-07-17T01:30:00Z"
        assert "2h30m" in event["end_estimate_basis"]
        assert "note" in result.data

    async def test_venue_key_normalization_unknown_venue_is_none(self):
        payload = {"_embedded": {"events": [_ticketmaster_event(venue_name="Some Random Bar")]}}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await check_transit.execute_event_lookup({"query": "local show"}, _ctx())
        assert result.data["events"][0]["venue_key"] is None

    async def test_date_maps_to_utc_day_bounds_params(self):
        payload = {"_embedded": {"events": []}}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            await check_transit.execute_event_lookup(
                {"query": "Knicks", "date": "2026-07-16"}, _ctx()
            )
        params = client_class.requests[0]["params"]
        assert "startDateTime" in params
        assert "endDateTime" in params
        # America/New_York midnight on 2026-07-16 is 04:00Z.
        assert params["startDateTime"] == "2026-07-16T04:00:00Z"

    async def test_cache_hit_skips_second_http_call(self):
        payload = {"_embedded": {"events": [_ticketmaster_event()]}}
        client_class = _recording_get_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            first = await check_transit.execute_event_lookup({"query": "Knicks"}, _ctx())
            second = await check_transit.execute_event_lookup({"query": "Knicks"}, _ctx())
        assert first.ok
        assert second.ok
        assert len(client_class.requests) == 1
        assert second.data == first.data

    async def test_malformed_response_is_reported_without_traceback(self):
        client_class = _recording_get_client({"_embedded": {"events": "not-a-list"}})
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await check_transit.execute_event_lookup({"query": "Knicks"}, _ctx())
        assert not result.ok
        assert "Traceback" not in (result.error or "")


class PoiSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env = patch.dict(
            os.environ, {"GOOGLE_PLACES_API_KEY": "places-key", "GOOGLE_ROUTES_API_KEY": ""}, clear=False
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def _place(
        self,
        name="Joe's Pizza",
        lat=40.7308,
        lng=-73.9973,
        open_now=True,
        address="7 Carmine St",
        place_id=None,
        price_level=None,
        rating=None,
        review_count=None,
    ):
        entry = {
            "displayName": {"text": name},
            "formattedAddress": address,
            "location": {"latitude": lat, "longitude": lng},
        }
        if place_id is not None:
            entry["id"] = place_id
        if open_now is not None:
            entry["currentOpeningHours"] = {"openNow": open_now}
        if price_level is not None:
            entry["priceLevel"] = price_level
        if rating is not None:
            entry["rating"] = rating
        if review_count is not None:
            entry["userRatingCount"] = review_count
        return entry

    async def test_unconfigured_when_neither_key_set(self):
        with patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "", "GOOGLE_ROUTES_API_KEY": ""}, clear=False):
            result = await search_local_places.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        assert not result.ok
        assert result.error == "place search is not configured"

    async def test_key_fallback_to_routes_key_when_places_key_unset(self):
        payload = {"places": [self._place()]}
        client_class = _recording_post_client(payload)
        with (
            patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "", "GOOGLE_ROUTES_API_KEY": "routes-key"}, clear=False),
            patch.object(_http.httpx, "AsyncClient", client_class),
        ):
            result = await search_local_places.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        assert result.ok
        assert client_class.requests[0]["headers"]["X-Goog-Api-Key"] == "routes-key"

    async def test_places_key_preferred_over_routes_key(self):
        payload = {"places": [self._place()]}
        client_class = _recording_post_client(payload)
        with (
            patch.dict(os.environ, {"GOOGLE_ROUTES_API_KEY": "routes-key"}, clear=False),
            patch.object(_http.httpx, "AsyncClient", client_class),
        ):
            await search_local_places.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        assert client_class.requests[0]["headers"]["X-Goog-Api-Key"] == "places-key"

    async def test_results_outside_nyc_bounds_are_dropped(self):
        payload = {
            "places": [
                self._place(name="In NYC", lat=40.73, lng=-73.99),
                self._place(name="In Boston", lat=42.36, lng=-71.06),
            ]
        }
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        assert result.ok
        names = [r["name"] for r in result.data["results"]]
        assert names == ["In NYC"]

    async def test_max_results_is_clamped(self):
        payload = {"places": [self._place(name=f"Place {i}") for i in range(10)]}
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute(
                {"query": "pizza", "max_results": 999}, _ctx(origin={"lat": 40.7, "lng": -73.9})
            )
        assert client_class.requests[0]["json"]["pageSize"] == 8
        assert len(result.data["results"]) <= 8

    async def test_open_now_is_none_when_absent(self):
        payload = {"places": [self._place(open_now=None)]}
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        assert result.data["results"][0]["open_now"] is None

    async def test_near_user_without_gps_asks_for_location(self):
        result = await search_local_places.execute({"query": "pizza", "near": "user"}, _ctx(origin=None))
        assert not result.ok
        assert "location" in result.error.lower()


    async def test_field_mask_requests_ranking_fields(self):
        client_class = _recording_post_client({"places": [self._place()]})
        with patch.object(_http.httpx, "AsyncClient", client_class):
            await search_local_places.execute({"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9}))
        mask = client_class.requests[0]["headers"]["X-Goog-FieldMask"]
        assert "places.id" in mask
        assert "places.priceLevel" in mask
        assert "places.rating" in mask
        assert "places.userRatingCount" in mask

    async def test_result_includes_provider_place_id(self):
        payload = {
            "places": [
                self._place(name="Joe's Pizza", place_id="ChIJ-joe"),
                self._place(name="No Id Pizza"),
            ]
        }
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute(
                {"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9})
            )
        assert result.ok
        by_name = {place["name"]: place for place in result.data["results"]}
        assert by_name["Joe's Pizza"]["place_id"] == "ChIJ-joe"
        assert by_name["No Id Pizza"]["place_id"] is None

    async def test_price_level_full_enums_are_normalized(self):
        payload = {
            "places": [
                self._place(name="Free", price_level="PRICE_LEVEL_FREE"),
                self._place(name="Inexpensive", price_level="PRICE_LEVEL_INEXPENSIVE"),
                self._place(name="Moderate", price_level="PRICE_LEVEL_MODERATE"),
                self._place(name="Expensive", price_level="PRICE_LEVEL_EXPENSIVE"),
                self._place(name="Very Expensive", price_level="PRICE_LEVEL_VERY_EXPENSIVE"),
            ]
        }
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute(
                {"query": "pizza", "max_results": 5}, _ctx(origin={"lat": 40.7, "lng": -73.9})
            )
        assert result.ok
        by_name = {place["name"]: place["price_level"] for place in result.data["results"]}
        assert by_name["Free"] == 0
        assert by_name["Inexpensive"] == 1
        assert by_name["Moderate"] == 2
        assert by_name["Expensive"] == 3
        assert by_name["Very Expensive"] == 4

    async def test_price_level_unspecified_unknown_and_numeric_values(self):
        payload = {
            "places": [
                self._place(name="Unspecified", price_level="PRICE_LEVEL_UNSPECIFIED"),
                self._place(name="Bogus", price_level="BOGUS_LEVEL"),
                self._place(name="Numeric", price_level=4),
            ]
        }
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute(
                {"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9})
            )
        assert result.ok
        by_name = {place["name"]: place["price_level"] for place in result.data["results"]}
        assert by_name["Unspecified"] is None
        assert by_name["Bogus"] is None
        assert by_name["Numeric"] == 4

    async def test_ranking_fields_are_normalized(self):
        payload = {
            "places": [
                self._place(name="Ranked Place", price_level=2, rating=4.5, review_count=123)
            ]
        }
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute(
                {"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9})
            )
        assert result.ok
        place = result.data["results"][0]
        assert place["price_level"] == 2
        assert place["rating"] == 4.5
        assert place["review_count"] == 123

    async def test_ranking_fields_are_none_when_absent(self):
        payload = {"places": [self._place()]}
        client_class = _recording_post_client(payload)
        with patch.object(_http.httpx, "AsyncClient", client_class):
            result = await search_local_places.execute(
                {"query": "pizza"}, _ctx(origin={"lat": 40.7, "lng": -73.9})
            )
        assert result.ok
        place = result.data["results"][0]
        assert place["price_level"] is None
        assert place["rating"] is None
        assert place["review_count"] is None


class VenueCrowdWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_window_arithmetic_from_offsets(self):
        result = await venues.execute(
            {"venue": "msg", "event_end_iso": "2026-07-16T22:00:00-04:00"}, _ctx()
        )
        assert result.ok
        assert result.data["surge_start_iso"] == "2026-07-16T21:45:00-04:00"
        assert result.data["surge_end_iso"] == "2026-07-16T22:50:00-04:00"
        assert result.data["pre_event_start_iso"] is None
        assert result.data["pre_event_end_iso"] is None

    async def test_all_six_venues_have_stations_and_lines(self):
        expected = {"msg", "barclays", "yankee_stadium", "citi_field", "penn_station", "port_authority"}
        assert set(venues.VENUE_CROWD_TABLE.keys()) == expected
        for venue_key in expected:
            result = await venues.execute(
                {"venue": venue_key, "event_end_iso": "2026-07-16T22:00:00-04:00"}, _ctx()
            )
            assert result.ok, venue_key
            assert result.data["stations"]
            assert result.data["lines"]

    async def test_unparseable_time_is_an_error(self):
        result = await venues.execute({"venue": "msg", "event_end_iso": "not-a-time"}, _ctx())
        assert not result.ok

    async def test_naive_datetime_is_rejected(self):
        result = await venues.execute(
            {"venue": "msg", "event_end_iso": "2026-07-16T22:00:00"}, _ctx()
        )
        assert not result.ok

    async def test_unknown_venue_is_an_error(self):
        result = await venues.execute(
            {"venue": "not_a_venue", "event_end_iso": "2026-07-16T22:00:00-04:00"}, _ctx()
        )
        assert not result.ok


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
            _duration, basis = event_provider.estimate_event_duration(*classification)
            assert expected_suffix in basis, classification

    def test_venue_name_aliases_normalize(self):
        assert event_provider.normalize_venue_name("Madison Square Garden") == "msg"
        assert event_provider.normalize_venue_name("The Garden") == "msg"
        assert event_provider.normalize_venue_name("MSG") == "msg"
        assert event_provider.normalize_venue_name("Barclays Center") == "barclays"
        assert event_provider.normalize_venue_name("Citi Field") == "citi_field"
        assert event_provider.normalize_venue_name("Some Random Bar") is None
        assert event_provider.normalize_venue_name(None) is None


class FixtureReplayTests(unittest.IsolatedAsyncioTestCase):
    """Tests the dispatch hook directly (agent_tools._with_fixture_replay)
    rather than through TOOL_REGISTRY, so a fake in-memory executor can prove
    whether it was called without needing real tool I/O."""

    async def test_no_fixtures_dir_calls_real_executor(self):
        calls = []

        async def executor(tool_input, _ctx):
            calls.append(tool_input)
            return ToolResult(ok=True, data={"real": True}, summary="ran for real")

        wrapped = agent_tools._with_fixture_replay("fake_tool", executor)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_TOOL_FIXTURES", None)
            result = await wrapped({"a": 1}, _ctx())
        assert result.ok
        assert calls == [{"a": 1}]

    async def test_replay_returns_fixture_without_calling_executor(self):
        calls = []

        async def executor(tool_input, _ctx):
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

        assert calls == []
        assert result.ok
        assert result.data == {"fixture": True}
        assert result.summary == "from fixture"

    async def test_missing_fixture_is_a_loud_error(self):
        async def executor(_tool_input, _ctx):
            return ToolResult(ok=True, data={"real": True})

        with tempfile.TemporaryDirectory() as tmp:
            wrapped = agent_tools._with_fixture_replay("fake_tool", executor)
            with patch.dict(os.environ, {"AGENT_TOOL_FIXTURES": tmp}, clear=False):
                result = await wrapped({"query": "nope"}, _ctx())

        assert not result.ok
        assert result.error == "no fixture for this input"

    async def test_record_mode_calls_real_executor_then_writes_fixture(self):
        calls = []

        async def executor(tool_input, _ctx):
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

            assert calls == [tool_input]
            assert result.ok
            assert result.data == {"real": True}
            assert written_path.exists()
            written = json.loads(written_path.read_text())
            assert written == {"ok": True, "data": {"real": True}, "summary": "ran for real", "error": None}

    async def test_route_preparation_and_transit_snapshot_share_fixture_hook(self):
        # Registered executors are wrapped closures, not the bare module
        # functions -- proves the hook applies uniformly, not just to the
        # P1 tools.
        assert "plan_trip" not in agent_tools.TOOL_REGISTRY
        assert agent_tools.TOOL_REGISTRY["prepare_route_options"].executor != agent_tools.prepare_route_options.execute
        assert agent_tools.INTERNAL_TOOL_REGISTRY["transit_snapshot"].executor != agent_tools.transit_snapshot.execute


if __name__ == "__main__":
    unittest.main()
