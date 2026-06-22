import importlib
import os
import sys
import types
import unittest
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


class DirectionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_route_budget_and_alternative_flag_are_configurable(self):
        directions = _load_directions(
            {
                "GOOGLE_ROUTES_API_KEY": "key",
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


if __name__ == "__main__":
    unittest.main()
