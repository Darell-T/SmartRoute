import asyncio
import importlib
import json
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _load_live_feed_module():
    fake_fastapi = types.ModuleType("fastapi")
    fake_responses = types.ModuleType("fastapi.responses")

    class _FakeAPIRouter:
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

        def post(self, *_args, **_kwargs):
            return lambda fn: fn

        def websocket(self, *_args, **_kwargs):
            return lambda fn: fn

    class _FakeRequest:
        pass

    class _FakeWebSocket:
        pass

    class _FakeWebSocketDisconnect(Exception):
        pass

    class _FakeJSONResponse:
        def __init__(self, content=None, status_code=200, headers=None):
            self.body = json.dumps(content or {}).encode("utf-8")
            self.status_code = status_code
            self.headers = headers or {}

    fake_fastapi.APIRouter = _FakeAPIRouter
    fake_fastapi.Request = _FakeRequest
    fake_fastapi.WebSocket = _FakeWebSocket
    fake_fastapi.WebSocketDisconnect = _FakeWebSocketDisconnect
    fake_responses.JSONResponse = _FakeJSONResponse

    fake_pydantic = types.ModuleType("pydantic")

    class _FakeBaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    fake_pydantic.BaseModel = _FakeBaseModel

    fake_geo = types.ModuleType("app.utils.geo")
    fake_geo.find_nearest_stops = lambda *_args, **_kwargs: []

    fake_cache = types.ModuleType("app.utils.cache")
    fake_cache.cache_get = lambda *_args, **_kwargs: None
    fake_cache.cache_set = lambda *_args, **_kwargs: None

    fake_mta_feed = types.ModuleType("app.services.mta_feed")
    fake_mta_feed.get_all_subway_vehicle_positions = AsyncMock(return_value=[])

    fake_ai_advisor = types.ModuleType("app.services.ai_advisor")
    fake_ai_advisor.generate_live_network_summary = AsyncMock(return_value={})

    fake_incident_monitor = types.ModuleType("app.services.incident_monitor")
    fake_incident_monitor.get_incidents = AsyncMock(return_value={"incidents": []})

    with patch.dict(
        sys.modules,
        {
            "fastapi": fake_fastapi,
            "fastapi.responses": fake_responses,
            "pydantic": fake_pydantic,
            "app.utils.geo": fake_geo,
            "app.utils.cache": fake_cache,
            "app.services.mta_feed": fake_mta_feed,
            "app.services.ai_advisor": fake_ai_advisor,
            "app.services.incident_monitor": fake_incident_monitor,
        },
    ):
        # Re-import the live_feed router AND its services.live_feed submodules
        # fresh so the stubbed mta_feed/ai_advisor/incident_monitor/cache bind
        # inside the submodules (summary/incidents import those at module load).
        for _m in [k for k in list(sys.modules) if k == "app.routers.live_feed" or k.startswith("app.services.live_feed")]:
            sys.modules.pop(_m, None)
        return importlib.import_module("app.routers.live_feed")


def _mint_ticket(live_feed, path: str, exp: int, app_key: str = "test-key") -> str:
    import hashlib
    import hmac

    message = f"{exp}.{path}"
    sig = hmac.new(app_key.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


class LiveFeedApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.live_feed = _load_live_feed_module()

    def test_ws_ticket_accepts_valid_path_bound_signature(self):
        exp = int(self.live_feed.time.time()) + 90
        ticket = _mint_ticket(self.live_feed, "/ws/live-feed", exp)

        with patch.dict(os.environ, {"APP_KEY": "test-key"}):
            self.assertTrue(self.live_feed._verify_ws_ticket(ticket, "/ws/live-feed"))

    def test_ws_ticket_rejects_expired_bad_and_path_mismatched_tickets(self):
        now = int(self.live_feed.time.time())
        valid_for_alerts = _mint_ticket(self.live_feed, "/ws/service-alerts", now + 90)
        expired = _mint_ticket(self.live_feed, "/ws/live-feed", now - 1)
        malformed = f"{now + 90}.not-a-real-signature"

        with patch.dict(os.environ, {"APP_KEY": "test-key"}):
            self.assertFalse(self.live_feed._verify_ws_ticket(expired, "/ws/live-feed"))
            self.assertFalse(self.live_feed._verify_ws_ticket(malformed, "/ws/live-feed"))
            self.assertFalse(self.live_feed._verify_ws_ticket(valid_for_alerts, "/ws/live-feed"))

    async def test_live_feed_unhandled_errors_return_503_redacted_json(self):
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gtfs=object())))
        payload = self.live_feed.LiveFeedRequest(lat=40.7, lng=-73.9)

        with patch.object(
            self.live_feed,
            "_live_feed_impl",
            AsyncMock(side_effect=RuntimeError("provider secret details")),
        ):
            response = await self.live_feed.live_feed(request, payload)

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(body["error"], "live feed temporarily unavailable")
        self.assertNotIn("provider secret details", response.body.decode("utf-8"))

    async def test_vehicles_unhandled_errors_return_503_redacted_json(self):
        with patch.object(
            self.live_feed.mta_feed,
            "get_all_subway_vehicle_positions",
            AsyncMock(side_effect=RuntimeError("provider secret details")),
        ):
            response = await self.live_feed.vehicles()

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(body["error"], "vehicles temporarily unavailable")
        self.assertNotIn("provider secret details", response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
