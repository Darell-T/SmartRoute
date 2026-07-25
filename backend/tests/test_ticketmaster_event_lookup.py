"""Focused Ticketmaster Discovery v2 contract tests.

All normal tests mock the HTTP boundary.  The optional live smoke test is
opt-in, skips without a key, and never prints the credential.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx

from app.services.agent.tools import _http, event_lookup, venue_crowd_window
from app.utils import cache
from tests._fake_http_tools import make_tool_ctx as _ctx
from tests._fake_http_tools import recording_get_client as _recording_get_client


def _event(
    event_id: str = "evt-1",
    *,
    name: str = "Knicks vs Celtics",
    status: str = "onsale",
    start: dict | None = None,
    venue: dict | None = None,
) -> dict:
    return {
        "id": event_id,
        "name": name,
        "dates": {
            "start": start if start is not None else {"dateTime": "2026-07-16T23:00:00Z"},
            "status": {"code": status},
            "timezone": "America/New_York",
        },
        "classifications": [{"segment": {"name": "Sports"}, "genre": {"name": "Basketball"}}],
        "_embedded": {
            "venues": [
                venue
                if venue is not None
                else {"name": "Madison Square Garden", "location": {"latitude": "40.7505", "longitude": "-73.9934"}}
            ]
        },
    }


class TicketmasterEventLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()
        event_lookup._inflight_locks.clear()
        self._env = patch.dict(
            os.environ,
            {
                "TICKETMASTER_API_KEY": "ticketmaster-test-key",
                "TICKETMASTER_ENABLED": "true",
                "TICKETMASTER_SEARCH_RADIUS_MILES": "25",
            },
            clear=False,
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    async def test_request_uses_official_endpoint_and_bounded_nyc_geo_filter(self):
        fetch = AsyncMock(return_value=({"_embedded": {"events": [_event()]}}, None))
        with patch.object(event_lookup, "fetch_json", fetch):
            result = await event_lookup.execute({"query": "Knicks", "date": "2026-07-16"}, _ctx())

        self.assertTrue(result.ok)
        args, kwargs = fetch.await_args
        self.assertEqual(args[1], "https://app.ticketmaster.com/discovery/v2/events.json")
        params = kwargs["params"]
        self.assertEqual(params["apikey"], "ticketmaster-test-key")
        self.assertEqual(params["latlong"], "40.7128,-74.0060")
        self.assertEqual(params["radius"], "25")
        self.assertEqual(params["unit"], "miles")
        self.assertEqual(params["includeTBA"], "no")
        self.assertEqual(params["includeTBD"], "no")
        self.assertNotIn("dmaId", params)
        self.assertEqual(params["startDateTime"], "2026-07-16T04:00:00Z")
        self.assertEqual(params["endDateTime"], "2026-07-17T03:59:59Z")
        event = result.data["events"][0]
        self.assertEqual(event["venue_latitude"], 40.7505)
        self.assertEqual(event["venue_longitude"], -73.9934)

    async def test_local_route_hub_filter_uses_the_requested_hub_not_city_center(self):
        sunset_park_venue = {
            "name": "Sunset Park venue",
            "location": {"latitude": "40.6558", "longitude": "-74.0090"},
        }
        fetch = AsyncMock(
            return_value=(
                {"_embedded": {"events": [_event("sunset", venue=sunset_park_venue)]}},
                None,
            )
        )
        with patch.object(event_lookup, "fetch_json", fetch):
            result = await event_lookup.execute(
                {
                    "query": "",
                    "date": "2026-07-16",
                    "latitude": 40.6558,
                    "longitude": -74.0090,
                    "radius_miles": 1.25,
                },
                _ctx(),
            )

        self.assertTrue(result.ok)
        self.assertEqual([event["event_id"] for event in result.data["events"]], ["sunset"])

    async def test_radius_is_clamped_to_a_safe_upper_bound(self):
        fetch = AsyncMock(return_value=({"_embedded": {"events": []}}, None))
        with patch.dict(os.environ, {"TICKETMASTER_SEARCH_RADIUS_MILES": "900"}, clear=False):
            with patch.object(event_lookup, "fetch_json", fetch):
                await event_lookup.execute({"query": "concert"}, _ctx())
        self.assertEqual(fetch.await_args.kwargs["params"]["radius"], "30")

    async def test_non_finite_radius_falls_back_to_the_safe_default(self):
        fetch = AsyncMock(return_value=({"_embedded": {"events": []}}, None))
        with patch.dict(os.environ, {"TICKETMASTER_SEARCH_RADIUS_MILES": "nan"}, clear=False):
            with patch.object(event_lookup, "fetch_json", fetch):
                await event_lookup.execute({"query": "concert"}, _ctx())
        self.assertEqual(fetch.await_args.kwargs["params"]["radius"], "25")

    async def test_empty_or_missing_embedded_events_is_a_clean_empty_result(self):
        for payload in ({}, {"_embedded": {}}, {"_embedded": {"events": []}}):
            cache._mem.clear()
            fetch = AsyncMock(return_value=(payload, None))
            with patch.object(event_lookup, "fetch_json", fetch):
                result = await event_lookup.execute({"query": "concert"}, _ctx())
            self.assertTrue(result.ok)
            self.assertEqual(result.data, {"events": []})

    async def test_missing_venue_or_coordinates_is_safe(self):
        missing_venue = _event("evt-no-venue")
        missing_venue["_embedded"] = {}
        invalid_coordinates = _event("evt-bad-coordinates", venue={"name": "Citi Field", "location": {"latitude": "x"}})
        fetch = AsyncMock(return_value=({"_embedded": {"events": [missing_venue, invalid_coordinates]}}, None))
        with patch.object(event_lookup, "fetch_json", fetch):
            result = await event_lookup.execute({"query": "baseball"}, _ctx())
        first, second = result.data["events"]
        self.assertIsNone(first["venue_name"])
        self.assertIsNone(first["venue_latitude"])
        self.assertIsNone(second["venue_latitude"])
        self.assertIsNone(second["venue_longitude"])

    async def test_local_coordinate_check_keeps_nearby_venues_and_rejects_distant_ones(self):
        metlife_style = _event(
            "nearby-metlife",
            venue={"name": "MetLife Stadium", "location": {"latitude": "40.8135", "longitude": "-74.0745"}},
        )
        boston = _event(
            "distant-boston",
            venue={"name": "TD Garden", "location": {"latitude": "42.3663", "longitude": "-71.0622"}},
        )
        fetch = AsyncMock(return_value=({"_embedded": {"events": [metlife_style, boston]}}, None))
        with patch.object(event_lookup, "fetch_json", fetch):
            result = await event_lookup.execute({"query": "game"}, _ctx())
        self.assertEqual([event["event_id"] for event in result.data["events"]], ["nearby-metlife"])

    async def test_recognized_venue_includes_static_station_and_line_association(self):
        known = _event("msg")
        unknown = _event("unknown", venue={"name": "Some NYC Hall", "location": {"latitude": "40.72", "longitude": "-74.0"}})
        fetch = AsyncMock(return_value=({"_embedded": {"events": [known, unknown]}}, None))
        with patch.object(event_lookup, "fetch_json", fetch):
            result = await event_lookup.execute({"query": "concert"}, _ctx())
        msg, unknown_result = result.data["events"]
        self.assertEqual(msg["nearby_stations"], ["34 St-Penn Station"])
        self.assertEqual(msg["nearby_lines"], ["1", "2", "3", "A", "C", "E"])
        self.assertEqual(unknown_result["nearby_stations"], [])
        self.assertEqual(unknown_result["nearby_lines"], [])

    def test_tba_tbd_and_date_only_events_never_receive_invented_start_or_end_times(self):
        cases = [
            ("tba", {"dateTBA": True}),
            ("tbd", {"dateTBD": True}),
            ("time-tba", {"localDate": "2026-07-16", "timeTBA": True}),
            ("date-only", {"localDate": "2026-07-16"}),
        ]
        for event_id, start in cases:
            parsed = event_lookup._parse_event(_event(event_id, start=start))
            self.assertIsNone(parsed["start_iso"], event_id)
            self.assertIsNone(parsed["estimated_end_iso"], event_id)

    def test_local_event_time_is_converted_using_the_reported_timezone(self):
        parsed = event_lookup._parse_event(
            _event("local-time", start={"localDate": "2026-07-16", "localTime": "20:00:00"})
        )
        self.assertEqual(parsed["start_iso"], "2026-07-17T00:00:00Z")

    async def test_cancelled_events_are_excluded_and_other_unsettled_statuses_have_no_crowd_estimate(self):
        payload = {
            "_embedded": {
                "events": [
                    _event("cancelled", status="cancelled"),
                    _event("postponed", status="postponed"),
                    _event("rescheduled", status="rescheduled"),
                ]
            }
        }
        with patch.object(event_lookup, "fetch_json", AsyncMock(return_value=(payload, None))):
            result = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertEqual([event["event_id"] for event in result.data["events"]], ["postponed", "rescheduled"])
        self.assertTrue(all(event["estimated_end_iso"] is None for event in result.data["events"]))

    async def test_bounded_pagination_deduplicates_by_official_event_id(self):
        responses = [
            ({"page": {"totalPages": 2}, "_embedded": {"events": [_event("one"), _event("cancelled", status="canceled")]}}, None),
            ({"page": {"totalPages": 2}, "_embedded": {"events": [_event("one"), _event("two")]}}, None),
        ]
        fetch = AsyncMock(side_effect=responses)
        with patch.object(event_lookup, "fetch_json", fetch):
            result = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertEqual(fetch.await_count, 2)
        self.assertEqual([call.kwargs["params"]["page"] for call in fetch.await_args_list], ["0", "1"])
        self.assertEqual([event["event_id"] for event in result.data["events"]], ["one", "two"])

    async def test_malformed_payload_is_sanitized(self):
        with patch.object(event_lookup, "fetch_json", AsyncMock(return_value=({"_embedded": {"events": "bad"}}, None))):
            result = await event_lookup.execute({"query": "concert"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "event lookup returned an unexpected response")

    async def test_invalid_key_and_rate_limit_errors_are_generic_and_never_log_the_key(self):
        for status_code in (401, 429):
            cache._mem.clear()
            client_class = _recording_get_client({}, status_code=status_code)
            with patch("builtins.print") as print_mock:
                with patch.object(_http.httpx, "AsyncClient", client_class):
                    result = await event_lookup.execute({"query": f"event-{status_code}"}, _ctx())
            self.assertFalse(result.ok)
            self.assertEqual(
                result.error,
                "event lookup authentication failed"
                if status_code == 401
                else "event lookup rate limited",
            )
            self.assertNotIn("ticketmaster-test-key", " ".join(str(call) for call in print_mock.call_args_list))

    async def test_timeout_is_bounded_and_clean(self):
        class _TimeoutClient:
            def __init__(self, *args, **kwargs):
                self.timeout = kwargs["timeout"]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, *_args, **_kwargs):
                raise httpx.TimeoutException("timeout")

        with patch.object(_http.httpx, "AsyncClient", _TimeoutClient):
            result = await event_lookup.execute({"query": "concert"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "event lookup timed out")

    async def test_cache_and_single_flight_collapse_concurrent_identical_requests(self):
        calls = 0

        async def delayed_fetch(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return {"_embedded": {"events": [_event()]}}, None

        with patch.object(event_lookup, "fetch_json", side_effect=delayed_fetch):
            first, second = await asyncio.gather(
                event_lookup.execute({"query": "Knicks"}, _ctx()),
                event_lookup.execute({"query": "Knicks"}, _ctx()),
            )
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(calls, 1)

    async def test_cache_key_includes_effective_search_radius(self):
        fetch = AsyncMock(return_value=({"_embedded": {"events": [_event()]}}, None))
        with patch.object(event_lookup, "fetch_json", fetch):
            await event_lookup.execute({"query": "Knicks"}, _ctx())
            with patch.dict(os.environ, {"TICKETMASTER_SEARCH_RADIUS_MILES": "5"}, clear=False):
                await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertEqual(fetch.await_count, 2)

    async def test_disabled_state_does_not_attempt_a_request(self):
        with patch.dict(os.environ, {"TICKETMASTER_ENABLED": "false"}, clear=False):
            with patch.object(event_lookup, "fetch_json") as fetch:
                result = await event_lookup.execute({"query": "Knicks"}, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "event lookup is disabled")
        fetch.assert_not_called()


class VenueCrowdWindowTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_event_gets_conservative_pre_and_post_event_windows(self):
        result = await venue_crowd_window.execute(
            {
                "venue": "msg",
                "event_start_iso": "2026-07-16T19:00:00-04:00",
                "event_end_iso": "2026-07-16T22:00:00-04:00",
                "event_status": "onsale",
                "start_time_status": "confirmed",
            },
            _ctx(),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["pre_event_start_iso"], "2026-07-16T18:00:00-04:00")
        self.assertEqual(result.data["pre_event_end_iso"], "2026-07-16T19:15:00-04:00")
        self.assertEqual(result.data["surge_start_iso"], "2026-07-16T21:45:00-04:00")
        self.assertEqual(result.data["surge_end_iso"], "2026-07-16T22:50:00-04:00")

    async def test_unsettled_or_unscheduled_events_never_receive_crowd_windows(self):
        for event_status, start_time_status in (
            ("canceled", "confirmed"),
            ("postponed", "confirmed"),
            ("rescheduled", "confirmed"),
            ("onsale", "date_tba"),
            ("onsale", "date_tbd"),
            ("onsale", "time_tba"),
            ("onsale", "date_only"),
        ):
            result = await venue_crowd_window.execute(
                {
                    "venue": "msg",
                    "event_start_iso": "2026-07-16T19:00:00-04:00",
                    "event_end_iso": "2026-07-16T22:00:00-04:00",
                    "event_status": event_status,
                    "start_time_status": start_time_status,
                },
                _ctx(),
            )
            self.assertFalse(result.ok, (event_status, start_time_status))
            self.assertEqual(result.error, "event timing is not confirmed for a crowd window")


class TicketmasterLiveSmokeTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        os.getenv("TICKETMASTER_LIVE_SMOKE_TEST") == "1",
        "set TICKETMASTER_LIVE_SMOKE_TEST=1 to run the Ticketmaster live smoke test",
    )
    async def test_live_smoke_uses_server_side_key_without_printing_it(self):
        if not os.getenv("TICKETMASTER_API_KEY"):
            self.skipTest("TICKETMASTER_API_KEY is not configured")
        cache._mem.clear()
        with patch("builtins.print") as print_mock:
            result = await event_lookup.execute(
                {
                    "query": "New York",
                    "date": datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
                },
                _ctx(),
            )
        self.assertTrue(result.ok, result.error)
        key = os.environ["TICKETMASTER_API_KEY"]
        internal_output = " ".join(str(call) for call in print_mock.call_args_list)
        self.assertNotIn(key, internal_output)
        smoke_output = f"[ticketmaster-live-smoke] {len(result.data.get('events') or [])} event(s); {result.summary}"
        self.assertIn("event(s)", smoke_output)
        self.assertNotIn(key, smoke_output)
        print(smoke_output)


if __name__ == "__main__":
    unittest.main()
