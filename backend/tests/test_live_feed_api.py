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
    fake_pydantic.ConfigDict = dict

    fake_geo = types.ModuleType("app.utils.geo")
    fake_geo.find_nearest_stops = lambda *_args, **_kwargs: []

    fake_mta_feed = types.ModuleType("app.services.mta_feed")
    fake_mta_feed.get_all_subway_vehicle_positions = AsyncMock(return_value=[])

    with patch.dict(
        sys.modules,
        {
            "fastapi": fake_fastapi,
            "fastapi.responses": fake_responses,
            "pydantic": fake_pydantic,
            "app.utils.geo": fake_geo,
            "app.services.mta_feed": fake_mta_feed,
        },
    ):
        # Re-import the live_feed router and helpers fresh so the stubbed
        # realtime dependencies bind at module load.
        for _m in [k for k in list(sys.modules) if k == "app.routers.live_feed" or k.startswith("app.services.live_feed")]:
            sys.modules.pop(_m, None)
        return importlib.import_module("app.routers.live_feed")


def _mint_ticket(
    live_feed,
    path: str,
    exp: int,
    app_key: str = "test-key",
    nonce: str = "nonce-for-test-ticket",
    principal: str = "v1.test-principal-opaque-123456",
) -> str:
    import hashlib
    import hmac

    message = f"{exp}.{path}.{nonce}.{principal}"
    sig = hmac.new(app_key.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{nonce}.{principal.removeprefix('v1.')}.{sig}"


class LiveFeedApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.live_feed = _load_live_feed_module()

    async def test_ws_ticket_accepts_valid_path_bound_signature(self):
        exp = int(self.live_feed.time.time()) + 90
        ticket = _mint_ticket(self.live_feed, "/ws/live-feed", exp)

        with patch.dict(os.environ, {"APP_KEY": "test-key"}), patch.object(
            self.live_feed.admission.runtime, "allows_mock_modes", return_value=True
        ):
            self.assertEqual(
                await self.live_feed._verify_ws_ticket(ticket, "/ws/live-feed"),
                ("v1.test-principal-opaque-123456", False),
            )

    async def test_ws_ticket_rejects_expired_bad_and_path_mismatched_tickets(self):
        now = int(self.live_feed.time.time())
        valid_for_alerts = _mint_ticket(self.live_feed, "/ws/service-alerts", now + 90)
        expired = _mint_ticket(self.live_feed, "/ws/live-feed", now - 1)
        far_future = _mint_ticket(self.live_feed, "/ws/live-feed", now + 121, nonce="far-future-token-123")
        malformed = f"{now + 90}.not-a-real-signature"

        with patch.dict(os.environ, {"APP_KEY": "test-key"}), patch.object(
            self.live_feed.admission.runtime, "allows_mock_modes", return_value=True
        ):
            self.assertEqual(await self.live_feed._verify_ws_ticket(expired, "/ws/live-feed"), (None, False))
            self.assertEqual(await self.live_feed._verify_ws_ticket(malformed, "/ws/live-feed"), (None, False))
            self.assertEqual(await self.live_feed._verify_ws_ticket(far_future, "/ws/live-feed"), (None, False))
            self.assertEqual(await self.live_feed._verify_ws_ticket(valid_for_alerts, "/ws/live-feed"), (None, False))

    async def test_ws_ticket_rejects_an_explicitly_oversized_value(self):
        with patch.dict(os.environ, {"APP_KEY": "test-key"}), patch.object(
            self.live_feed.admission.runtime, "allows_mock_modes", return_value=True
        ):
            self.assertEqual(
                await self.live_feed._verify_ws_ticket("x" * 513, "/ws/live-feed"),
                (None, False),
            )

    async def test_ws_ticket_replay_race_consumes_exactly_once(self):
        exp = int(self.live_feed.time.time()) + 90
        ticket = _mint_ticket(self.live_feed, "/ws/live-feed", exp, nonce="race-nonce-token-123")
        with patch.dict(os.environ, {"APP_KEY": "test-key"}), patch.object(
            self.live_feed.admission.runtime, "allows_mock_modes", return_value=True
        ):
            first, second = await asyncio.gather(
                self.live_feed._verify_ws_ticket(ticket, "/ws/live-feed"),
                self.live_feed._verify_ws_ticket(ticket, "/ws/live-feed"),
            )
        self.assertEqual(sum(result[0] is not None for result in (first, second)), 1)
        self.assertEqual({first[1], second[1]}, {False})

    async def test_ticket_store_failure_closes_before_accept_with_1013(self):
        class Socket:
            query_params = {"ticket": "unused"}
            url = SimpleNamespace(path="/ws/live-feed")
            app = SimpleNamespace(state=SimpleNamespace(gtfs=object()))

            def __init__(self):
                self.codes = []

            async def close(self, code):
                self.codes.append(code)

            async def accept(self):
                raise AssertionError("ticket-store failure must not accept")

        socket = Socket()
        with patch.object(self.live_feed, "_verify_ws_ticket", AsyncMock(return_value=(None, True))):
            await self.live_feed.live_feed_socket(socket)
        self.assertEqual(socket.codes, [1013])

    async def test_lease_guard_refresh_failure_closes_idle_socket(self):
        class Socket:
            def __init__(self):
                self.codes = []

            async def close(self, code):
                self.codes.append(code)

        socket = Socket()
        lease = self.live_feed.admission.AdmissionLease("v1.test-principal-opaque-123456", "ws", "lease")
        owner = asyncio.create_task(asyncio.sleep(60))
        with patch.object(self.live_feed, "LEASE_GUARD_INTERVAL_S", 0), patch.object(
            self.live_feed.admission, "refresh", AsyncMock(return_value=False)
        ):
            await self.live_feed._guard_socket_lease(socket, lease, asyncio.Event(), owner)
        self.assertTrue(owner.cancelled() or owner.cancelling())
        self.assertEqual(socket.codes, [1013])

    async def test_lease_guard_cancels_owner_when_close_raises(self):
        class Socket:
            async def close(self, code):
                raise RuntimeError("already closing")

        owner = asyncio.create_task(asyncio.sleep(60))
        lease = self.live_feed.admission.AdmissionLease("v1.test-principal-opaque-123456", "ws", "lease")
        with patch.object(self.live_feed, "LEASE_GUARD_INTERVAL_S", 0), patch.object(
            self.live_feed.admission, "refresh", AsyncMock(return_value=False)
        ):
            await self.live_feed._guard_socket_lease(Socket(), lease, asyncio.Event(), owner)
        self.assertTrue(owner.cancelled() or owner.cancelling())

    async def test_service_alerts_releases_once_when_guard_close_fails(self):
        class Socket:
            query_params = {"ticket": "valid"}
            url = SimpleNamespace(path="/ws/service-alerts")
            app = SimpleNamespace(state=SimpleNamespace(gtfs=None))

            async def accept(self):
                return None

            async def close(self, code):
                raise RuntimeError("already closing")

            async def send_json(self, _payload):
                return None

        lease = self.live_feed.admission.AdmissionLease(
            "v1.test-principal-opaque-123456", "ws", "lease"
        )
        with patch.object(
            self.live_feed, "_verify_ws_ticket", AsyncMock(return_value=(lease.principal, False))
        ), patch.object(
            self.live_feed.admission, "acquire", AsyncMock(return_value=lease)
        ), patch.object(
            self.live_feed.admission, "release", AsyncMock()
        ) as release, patch.object(
            self.live_feed.admission, "refresh", AsyncMock(return_value=False)
        ), patch.object(
            self.live_feed, "LEASE_GUARD_INTERVAL_S", 0
        ), patch.object(
            self.live_feed, "_service_alerts_payload", AsyncMock(return_value={"alerts": [], "updated_at": 0})
        ):
            await self.live_feed.service_alerts_socket(Socket())
        release.assert_awaited_once_with(lease)

    async def test_both_socket_handlers_accept_valid_ticket_and_release_once(self):
        class Socket:
            query_params = {"ticket": "valid"}

            def __init__(self, path, gtfs=None):
                self.url = SimpleNamespace(path=path)
                self.app = SimpleNamespace(state=SimpleNamespace(gtfs=gtfs))
                self.accepted = 0
                self.codes = []

            async def accept(self): self.accepted += 1
            async def close(self, code): self.codes.append(code)
            async def send_json(self, _payload): return None

        lease = self.live_feed.admission.AdmissionLease("v1.test-principal-opaque-123456", "ws", "lease")
        service_socket = Socket("/ws/service-alerts")
        live_socket = Socket("/ws/live-feed", gtfs=None)
        with patch.object(self.live_feed, "_verify_ws_ticket", AsyncMock(return_value=(lease.principal, False))), patch.object(
            self.live_feed.admission, "acquire", AsyncMock(return_value=lease)
        ), patch.object(self.live_feed.admission, "release", AsyncMock()) as release, patch.object(
            self.live_feed, "_service_alerts_payload", AsyncMock(side_effect=self.live_feed.WebSocketDisconnect())
        ):
            await self.live_feed.service_alerts_socket(service_socket)
            await self.live_feed.live_feed_socket(live_socket)
        self.assertEqual(service_socket.accepted, 1)
        self.assertEqual(live_socket.accepted, 1)
        self.assertEqual(live_socket.codes, [1011])
        self.assertEqual(release.await_count, 2)

    async def test_socket_admission_denial_closes_before_accept_for_both_handlers(self):
        class Socket:
            query_params = {"ticket": "valid"}
            def __init__(self, path):
                self.url = SimpleNamespace(path=path)
                self.app = SimpleNamespace(state=SimpleNamespace(gtfs=None))
                self.accepted, self.codes = 0, []
            async def accept(self): self.accepted += 1
            async def close(self, code): self.codes.append(code)

        denied = self.live_feed.admission.AdmissionDenied(503, "busy", 1)
        with patch.object(self.live_feed, "_verify_ws_ticket", AsyncMock(return_value=("v1.test-principal-opaque-123456", False))), patch.object(
            self.live_feed.admission, "acquire", AsyncMock(side_effect=denied)
        ):
            for handler, path in ((self.live_feed.service_alerts_socket, "/ws/service-alerts"), (self.live_feed.live_feed_socket, "/ws/live-feed")):
                socket = Socket(path)
                await handler(socket)
                self.assertEqual(socket.accepted, 0)
                self.assertEqual(socket.codes, [1013])

    async def test_bounded_ws_parser_accepts_exact_4096_byte_text_and_bytes_frames(self):
        class Socket:
            def __init__(self, frame): self.frame = frame
            async def receive(self): return self.frame

        location = b'{"type":"location","lat":40.7,"lng":-73.9}'
        for frame in ({"text": location.decode()}, {"bytes": location}):
            parsed = await self.live_feed._receive_bounded_ws_json(Socket(frame))
            self.assertEqual(parsed["type"], "location")
        padded = location + b" " * (self.live_feed.MAX_WS_MESSAGE_BYTES - len(location))
        self.assertEqual(len(padded), 4096)
        for frame in ({"text": padded.decode()}, {"bytes": padded}):
            parsed = await self.live_feed._receive_bounded_ws_json(Socket(frame))
            self.assertEqual(parsed["type"], "location")

    async def test_bounded_ws_parser_rejects_4097_byte_text_and_bytes_frames(self):
        class Socket:
            def __init__(self, frame): self.frame = frame
            async def receive(self): return self.frame

        location = b'{"type":"location","lat":40.7,"lng":-73.9}'
        oversized = location + b" " * (self.live_feed.MAX_WS_MESSAGE_BYTES + 1 - len(location))
        self.assertEqual(len(oversized), 4097)
        for frame in ({"text": oversized.decode()}, {"bytes": oversized}):
            with self.subTest(frame_type=next(iter(frame))), self.assertRaises(ValueError):
                await self.live_feed._receive_bounded_ws_json(Socket(frame))

    async def test_bounded_ws_parser_rejects_unknown_key_out_of_metro_location_and_excess_routes(self):
        class Socket:
            def __init__(self, frame): self.frame = frame
            async def receive(self): return self.frame

        invalid = (
            {"text": '{"type":"location","lat":42,"lng":-73.9}'},
            {"text": '{"type":"location","lat":40.7,"lng":-73.9,"extra":1}'},
            {"text": '{"type":"vehicle_scope","selected_route_ids":["A","B","C","D","E","F","G","H","J","L","M","N","Q"]}'},
        )
        for frame in invalid:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                await self.live_feed._receive_bounded_ws_json(Socket(frame))

    async def test_invalid_live_feed_frame_does_not_build_a_snapshot(self):
        class Socket:
            query_params = {"ticket": "valid"}
            url = SimpleNamespace(path="/ws/live-feed")
            app = SimpleNamespace(state=SimpleNamespace(gtfs=object()))

            def __init__(self):
                self.codes = []

            async def accept(self):
                return None

            async def close(self, code):
                self.codes.append(code)

            async def receive(self):
                return {"text": '{"type":"location","lat":42,"lng":-73.9}'}

        lease = self.live_feed.admission.AdmissionLease(
            "v1.test-principal-opaque-123456", "ws", "lease"
        )
        snapshot = AsyncMock()
        socket = Socket()
        with patch.object(
            self.live_feed, "_verify_ws_ticket", AsyncMock(return_value=(lease.principal, False))
        ), patch.object(
            self.live_feed.admission, "acquire", AsyncMock(return_value=lease)
        ), patch.object(
            self.live_feed.admission, "release", AsyncMock()
        ) as release, patch.object(self.live_feed, "_build_live_snapshot", snapshot):
            await self.live_feed.live_feed_socket(socket)
        self.assertEqual(socket.codes, [1008])
        snapshot.assert_not_awaited()
        release.assert_awaited_once_with(lease)

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
