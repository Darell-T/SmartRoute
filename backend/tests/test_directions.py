import importlib
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _fake_httpx_module():
    fake_httpx = types.ModuleType("httpx")

    class _RequestError(Exception):
        pass

    class _HTTPStatusError(Exception):
        def __init__(self, response):
            self.response = response

    class _TimeoutException(Exception):
        pass

    class _ReadTimeout(_TimeoutException):
        pass

    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    fake_httpx.RequestError = _RequestError
    fake_httpx.HTTPStatusError = _HTTPStatusError
    fake_httpx.TimeoutException = _TimeoutException
    fake_httpx.ReadTimeout = _ReadTimeout
    fake_httpx.AsyncClient = _AsyncClient
    return fake_httpx


def _load_directions(env: dict[str, str]):
    with patch.dict(os.environ, env, clear=False):
        with patch.dict(sys.modules, {"httpx": _fake_httpx_module()}):
            if "app.services.directions" in sys.modules:
                return importlib.reload(sys.modules["app.services.directions"])
            return importlib.import_module("app.services.directions")


def _recording_client_class():
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"routes": []}

    class _Client:
        requests = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json, headers, timeout):
            self.requests.append(
                {
                    "url": url,
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return _Response()

    return _Client


SAMPLE_TRANSIT_LEG = {
    "duration": "2340s",
    "steps": [
        {
            "travelMode": "WALK",
            "startLocation": {"latLng": {"latitude": 40.6501, "longitude": -73.9796}},
            "endLocation": {"latLng": {"latitude": 40.6505, "longitude": -73.9793}},
            "polyline": {"encodedPolyline": "abc123"},
        },
        {
            "travelMode": "TRANSIT",
            "polyline": {"encodedPolyline": "def456"},
            "transitDetails": {
                "stopDetails": {
                    "arrivalStop": {
                        "name": "Times Sq-42 St",
                        "location": {"latLng": {"latitude": 40.7580, "longitude": -73.9855}},
                    },
                    "departureStop": {
                        "name": "Church Av",
                        "location": {"latLng": {"latitude": 40.6505, "longitude": -73.9793}},
                    },
                    "arrivalTime": "2026-03-22T18:30:00Z",
                    "departureTime": "2026-03-22T18:00:00Z",
                },
                "headsign": "Astoria-Ditmars Blvd",
                "transitLine": {
                    "nameShort": "Q",
                    "color": "#FCCC0A",
                    "vehicle": {"type": "SUBWAY"},
                },
                "stopCount": 15,
            },
        },
    ],
}


class TransitRouteParamsTests(unittest.IsolatedAsyncioTestCase):
    # A single reload for the whole class (rather than one per test) avoids
    # churning sys.modules/importlib.reload enough times in one process to
    # trip an unrelated zoneinfo C-extension refcount bug observed under
    # heavy reload cycling in this environment.
    @classmethod
    def setUpClass(cls):
        cls.directions = _load_directions({"GOOGLE_ROUTES_API_KEY": "key"})

    def _client_for(self, directions):
        client_class = _recording_client_class()
        patch.object(directions.httpx, "AsyncClient", client_class).start()
        self.addCleanup(patch.stopall)
        return client_class

    async def test_default_call_omits_new_params_and_matches_prior_body(self):
        directions = self.directions
        client_class = self._client_for(directions)

        await directions.get_transit_route((40.7, -73.9), "Atlantic Terminal")

        body = client_class.requests[0]["json"]
        self.assertEqual(
            body["transitPreferences"],
            {"allowedTravelModes": ["SUBWAY", "BUS"], "routingPreference": "FEWER_TRANSFERS"},
        )
        self.assertNotIn("departureTime", body)

    async def test_allowed_travel_modes_override_is_sent(self):
        directions = self.directions
        client_class = self._client_for(directions)

        await directions.get_transit_route(
            (40.7, -73.9), "Atlantic Terminal", allowed_travel_modes=["SUBWAY"]
        )

        body = client_class.requests[0]["json"]
        self.assertEqual(body["transitPreferences"]["allowedTravelModes"], ["SUBWAY"])

    async def test_invalid_allowed_travel_modes_raises(self):
        directions = self.directions
        self._client_for(directions)

        with self.assertRaises(directions.GoogleRoutesError) as raised:
            await directions.get_transit_route(
                (40.7, -73.9), "Atlantic Terminal", allowed_travel_modes=["FERRY"]
            )
        self.assertEqual(raised.exception.code, "invalid_modes")

    async def test_empty_allowed_travel_modes_raises(self):
        directions = self.directions
        self._client_for(directions)

        with self.assertRaises(directions.GoogleRoutesError) as raised:
            await directions.get_transit_route(
                (40.7, -73.9), "Atlantic Terminal", allowed_travel_modes=[]
            )
        self.assertEqual(raised.exception.code, "invalid_modes")

    async def test_routing_preference_passthrough(self):
        directions = self.directions
        client_class = self._client_for(directions)

        await directions.get_transit_route(
            (40.7, -73.9), "Atlantic Terminal", routing_preference="LESS_WALKING"
        )

        body = client_class.requests[0]["json"]
        self.assertEqual(body["transitPreferences"]["routingPreference"], "LESS_WALKING")

    async def test_invalid_routing_preference_raises(self):
        directions = self.directions
        self._client_for(directions)

        with self.assertRaises(directions.GoogleRoutesError) as raised:
            await directions.get_transit_route(
                (40.7, -73.9), "Atlantic Terminal", routing_preference="FASTEST"
            )
        self.assertEqual(raised.exception.code, "invalid_preference")

    async def test_departure_time_aware_datetime_serializes_to_utc_z(self):
        directions = self.directions
        client_class = self._client_for(directions)

        aware_dt = datetime(2026, 7, 16, 10, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
        await directions.get_transit_route(
            (40.7, -73.9), "Atlantic Terminal", departure_time=aware_dt
        )

        body = client_class.requests[0]["json"]
        self.assertEqual(body["departureTime"], "2026-07-16T14:00:00Z")

    async def test_departure_time_rfc3339_string_is_accepted(self):
        directions = self.directions
        client_class = self._client_for(directions)

        await directions.get_transit_route(
            (40.7, -73.9), "Atlantic Terminal", departure_time="2026-07-16T10:00:00-04:00"
        )

        body = client_class.requests[0]["json"]
        self.assertEqual(body["departureTime"], "2026-07-16T14:00:00Z")

    async def test_departure_time_naive_datetime_is_rejected(self):
        directions = self.directions
        self._client_for(directions)

        with self.assertRaises(directions.GoogleRoutesError) as raised:
            await directions.get_transit_route(
                (40.7, -73.9),
                "Atlantic Terminal",
                departure_time=datetime(2026, 7, 16, 10, 0, 0),
            )
        self.assertEqual(raised.exception.code, "invalid_departure_time")

    async def test_departure_time_unparseable_string_is_rejected(self):
        directions = self.directions
        self._client_for(directions)

        with self.assertRaises(directions.GoogleRoutesError) as raised:
            await directions.get_transit_route(
                (40.7, -73.9), "Atlantic Terminal", departure_time="not-a-time"
            )
        self.assertEqual(raised.exception.code, "invalid_departure_time")

    async def test_departure_time_none_omits_key(self):
        directions = self.directions
        client_class = self._client_for(directions)

        await directions.get_transit_route(
            (40.7, -73.9), "Atlantic Terminal", departure_time=None
        )

        body = client_class.requests[0]["json"]
        self.assertNotIn("departureTime", body)


class ParseLegStepsTests(unittest.TestCase):
    # Parsing is independent of GOOGLE_ROUTES_API_KEY/httpx, so this reuses
    # whatever module instance is already cached in sys.modules instead of
    # reloading (see the comment on TransitRouteParamsTests.setUpClass).
    @classmethod
    def setUpClass(cls):
        cls.directions = importlib.import_module("app.services.directions")

    def test_transit_step_includes_additive_iso_fields_without_removing_existing_ones(self):
        directions = self.directions

        steps = directions._parse_leg_steps(SAMPLE_TRANSIT_LEG)
        transit_step = next(step for step in steps if step["type"] == "SUBWAY")

        self.assertIn("departure_time_iso", transit_step)
        self.assertIn("arrival_time_iso", transit_step)
        self.assertTrue(transit_step["departure_time_iso"].startswith("2026-03-22T"))
        self.assertTrue(transit_step["arrival_time_iso"].startswith("2026-03-22T"))
        self.assertIn("minutes_until_train_arrives", transit_step)
        self.assertIn("minutes_until_arrival", transit_step)
        self.assertEqual(transit_step["departure_stop"], "Church Av")
        self.assertEqual(transit_step["arrival_stop"], "Times Sq-42 St")

    def test_walk_step_is_unchanged(self):
        directions = self.directions

        steps = directions._parse_leg_steps(SAMPLE_TRANSIT_LEG)
        walk_step = next(step for step in steps if step["type"] == "WALK")

        self.assertEqual(
            set(walk_step.keys()),
            {"type", "start_point", "end_point", "route_total_minutes", "polyline"},
        )


class DirectionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_route_budget_and_alternative_flag_are_configurable(self):
        directions = _load_directions(
            {
                "GOOGLE_ROUTES_API_KEY": "  key  \n",
                "GOOGLE_ROUTES_TIMEOUT_S": "7.5",
                "GOOGLE_ROUTES_RETRIES": "1",
                "GOOGLE_ROUTES_ALTERNATIVES": "0",
            }
        )

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"routes": []}

        class _Client:
            requests = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, url, json, headers, timeout):
                self.requests.append(
                    {
                        "url": url,
                        "json": json,
                        "headers": headers,
                        "timeout": timeout,
                    }
                )
                return _Response()

        with patch.object(directions.httpx, "AsyncClient", _Client):
            result = await directions.get_transit_route((40.7, -73.9), "Atlantic Terminal")

        self.assertEqual(result, {"routes": []})
        request = _Client.requests[0]
        self.assertEqual(request["timeout"], 7.5)
        self.assertEqual(request["headers"]["X-Goog-Api-Key"], "key")
        self.assertFalse(request["json"]["computeAlternativeRoutes"])

    async def test_google_route_timeout_retries_are_capped(self):
        directions = _load_directions(
            {
                "GOOGLE_ROUTES_API_KEY": "key",
                "GOOGLE_ROUTES_TIMEOUT_S": "0.01",
                "GOOGLE_ROUTES_RETRIES": "2",
                "GOOGLE_ROUTES_ALTERNATIVES": "1",
            }
        )

        class _Client:
            attempts = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, *_args, **_kwargs):
                type(self).attempts += 1
                raise directions.httpx.ReadTimeout("slow")

        with patch.object(directions.httpx, "AsyncClient", _Client):
            with self.assertRaises(RuntimeError):
                await directions.get_transit_route((40.7, -73.9), "Atlantic Terminal")

        self.assertEqual(_Client.attempts, 2)

    async def test_google_route_requires_api_key_before_request(self):
        directions = _load_directions(
            {
                "GOOGLE_ROUTES_API_KEY": "",
                "GOOGLE_ROUTES_TIMEOUT_S": "7.5",
                "GOOGLE_ROUTES_RETRIES": "1",
            }
        )

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, *_args, **_kwargs):
                raise AssertionError("request should not be sent without a Google API key")

        with patch.object(directions.httpx, "AsyncClient", _Client):
            with self.assertRaisesRegex(RuntimeError, "Google Routes API is not configured"):
                await directions.get_transit_route((40.7, -73.9), "Atlantic Terminal")

    async def test_google_route_request_errors_are_redacted_runtime_errors(self):
        directions = _load_directions(
            {
                "GOOGLE_ROUTES_API_KEY": "key",
                "GOOGLE_ROUTES_TIMEOUT_S": "7.5",
                "GOOGLE_ROUTES_RETRIES": "1",
            }
        )

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, *_args, **_kwargs):
                raise directions.httpx.RequestError("socket provider detail")

        with patch.object(directions.httpx, "AsyncClient", _Client):
            with self.assertRaisesRegex(RuntimeError, "Google Routes API request failed") as raised:
                await directions.get_transit_route((40.7, -73.9), "Atlantic Terminal")

        self.assertNotIn("socket provider detail", str(raised.exception))

    async def test_google_route_bad_json_is_redacted_runtime_error(self):
        directions = _load_directions(
            {
                "GOOGLE_ROUTES_API_KEY": "key",
                "GOOGLE_ROUTES_TIMEOUT_S": "7.5",
                "GOOGLE_ROUTES_RETRIES": "1",
            }
        )

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("bad provider json detail")

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, *_args, **_kwargs):
                return _Response()

        with patch.object(directions.httpx, "AsyncClient", _Client):
            with self.assertRaisesRegex(RuntimeError, "Google Routes API returned invalid JSON") as raised:
                await directions.get_transit_route((40.7, -73.9), "Atlantic Terminal")

        self.assertNotIn("bad provider json detail", str(raised.exception))

    async def test_google_route_http_status_keeps_safe_diagnostic_code(self):
        directions = _load_directions(
            {
                "GOOGLE_ROUTES_API_KEY": "key",
                "GOOGLE_ROUTES_TIMEOUT_S": "7.5",
                "GOOGLE_ROUTES_RETRIES": "1",
            }
        )

        class _Response:
            status_code = 403

            def raise_for_status(self):
                raise directions.httpx.HTTPStatusError(self)

            def json(self):
                return {
                    "error": {
                        "status": "PERMISSION_DENIED",
                        "message": "API key not authorized for Routes API",
                    }
                }

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, *_args, **_kwargs):
                return _Response()

        with patch.object(directions.httpx, "AsyncClient", _Client):
            with self.assertRaises(directions.GoogleRoutesError) as raised:
                await directions.get_transit_route((40.7, -73.9), "Atlantic Terminal")

        self.assertEqual(raised.exception.code, "http_403")
        self.assertEqual(raised.exception.provider_status, 403)
        self.assertIn("PERMISSION_DENIED", raised.exception.provider_summary)


if __name__ == "__main__":
    unittest.main()
