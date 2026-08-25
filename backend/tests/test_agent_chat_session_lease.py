"""Focused tests for per-session turn serialization (P1 remediation).

One active chat turn per exact ``session_id``: the turn lease is claimed
before loading/mutating an existing session and immediately after minting a
new one (before ``next_turn_id``), held through the SSE stream and the final
save, and released ownership-safely via the shared atomic cache primitives
(``cache_add`` / ``cache_delete_if_value``) so the guarantee holds across
processes under Redis with in-memory parity in dev/tests.

A busy same-session request fails fast with a bounded retryable 503 +
Retry-After before session load, ``next_turn_id``, model/tool execution, or
state mutation, and releases its admission lease. Different sessions stay
fully concurrent, and the duplicate-turn / lost-update overlap is no longer
reachable through the chat boundary.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.routers import agent_chat
from app.services import admission
from app.services.agent import events as agent_events
from app.services.agent import session as session_module
from app.services import cache


PRINCIPAL = "v1.test-principal-opaque-123456"
SESSION_ID = "sess-lease-0001"
SESSION_NEW = "sess-lease-new-1"
SESSION_EXPIRED = "sess-lease-expired-1"
TURN_ID = "t1"
LEASE = admission.AdmissionLease(PRINCIPAL, "chat", "lease-token-1")


def _request(*, disconnected: bool = False):
    return SimpleNamespace(
        headers={"X-SmartRoute-Principal": PRINCIPAL},
        app=SimpleNamespace(state=SimpleNamespace(gtfs=None)),
        is_disconnected=AsyncMock(return_value=disconnected),
    )


def _payload(**overrides):
    fields = {"message": "hello"}
    fields.update(overrides)
    return agent_chat.AgentChatRequest(**fields)


def _event_frame(chunk: str) -> tuple[str, dict]:
    event_line, data_line = chunk.strip().splitlines()[:2]
    event_type = event_line.split("event: ", 1)[1]
    return event_type, json.loads(data_line.split("data: ", 1)[1])


def _event_data(chunk: str) -> dict:
    return _event_frame(chunk)[1]


def _fake_turn(**kw):
    """Loop-compatible fake generator that records history like finalization."""

    async def agen():
        yield agent_events.MetaEvent(session_id=kw["session_id"], turn_id=kw["turn_id"])
        session_module.append_history(kw["session"], "user", kw["message"])
        yield agent_events.DoneEvent(
            session_id=kw["session_id"],
            turn_id=kw["turn_id"],
            stop_reason="end_turn",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    return agen()


def _stream_args(session: dict, *, request=None, session_lease_token=None):
    return dict(
        request=request if request is not None else _request(),
        session_id=SESSION_ID,
        session=session,
        turn_id=TURN_ID,
        message="hello",
        now_et="2026-08-10T12:00:00-04:00",
        gtfs=None,
        origin=None,
        selected_card_id=None,
        response_presentation="auto",
        trace=None,
        lease=LEASE,
        session_lease_token=session_lease_token,
    )


async def _assert_no_owned_pending_tasks(baseline: set[asyncio.Task]) -> None:
    for _ in range(3):
        await asyncio.sleep(0)
    owned = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task not in baseline
    ]
    assert owned == [], f"leaked pending tasks: {owned}"


class SessionLeasePrimitivesTests(unittest.TestCase):
    """Atomic acquire / busy / owner-safe release, in-memory parity path."""

    def setUp(self):
        cache._mem.clear()

    def test_acquire_is_exclusive_per_session(self):
        first = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(first)
        self.assertIsNone(session_module.acquire_session_lease(SESSION_ID))
        # The failed acquire must not have disturbed the held lease.
        self.assertTrue(session_module.release_session_lease(SESSION_ID, first))

    def test_different_sessions_stay_concurrent(self):
        first_a = session_module.acquire_session_lease("sess-a")
        first_b = session_module.acquire_session_lease("sess-b")
        self.assertIsNotNone(first_a)
        self.assertIsNotNone(first_b)
        # Releasing A never touches B.
        self.assertTrue(session_module.release_session_lease("sess-a", first_a))
        self.assertIsNone(session_module.acquire_session_lease("sess-b"))
        self.assertTrue(session_module.release_session_lease("sess-b", first_b))

    def test_release_is_owner_safe(self):
        owner = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(owner)
        # A different token cannot delete the lease.
        self.assertFalse(session_module.release_session_lease(SESSION_ID, "not-the-owner"))
        self.assertIsNone(session_module.acquire_session_lease(SESSION_ID))
        # The real owner can.
        self.assertTrue(session_module.release_session_lease(SESSION_ID, owner))
        self.assertIsNotNone(session_module.acquire_session_lease(SESSION_ID))

    def test_expired_lease_can_be_reacquired(self):
        key = session_module._session_lease_key(SESSION_ID)
        owner = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(owner)
        _value, _expiry = cache._mem[key]
        cache._mem[key] = (_value, 0)  # expired: counts as absent
        # Owner-safe release of an expired lease is a no-op...
        self.assertFalse(session_module.release_session_lease(SESSION_ID, owner))
        # ...and a fresh turn can acquire.
        fresh = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(fresh)
        self.assertTrue(session_module.release_session_lease(SESSION_ID, fresh))

    def test_none_token_release_is_noop(self):
        self.assertFalse(session_module.release_session_lease(SESSION_ID, None))

    def test_session_lease_ttl_covers_fractional_deadline_plus_margin(self):
        self.assertEqual(session_module._session_lease_ttl_s(50.9), 121)
        self.assertEqual(session_module._session_lease_ttl_s(0.1), 120)


class AgentChatSessionLeaseRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()

    async def test_existing_session_busy_rejects_before_load_or_next_turn(self):
        # A turn is already running for this session.
        owner = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(owner)
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()) as release,
            patch.object(agent_chat.session_module, "load_session", Mock()) as load,
            patch.object(agent_chat.session_module, "next_turn_id", Mock()) as next_turn,
            patch.object(agent_chat.session_module, "save_session", Mock()) as save,
        ):
            with self.assertRaises(HTTPException) as error:
                await agent_chat.agent_chat(_request(), _payload(session_id=SESSION_ID))

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.headers.get("Retry-After"), "1")
        # Fail-fast contract: nothing loaded, no turn minted, no state write.
        load.assert_not_called()
        next_turn.assert_not_called()
        save.assert_not_called()
        # The admission lease is released exactly once.
        release.assert_awaited_once_with(LEASE)
        # The original turn's lease is untouched.
        self.assertIsNone(session_module.acquire_session_lease(SESSION_ID))
        self.assertTrue(session_module.release_session_lease(SESSION_ID, owner))

    async def test_new_session_holds_lease_through_stream_then_releases(self):
        _sid, blob = session_module.new_session()
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()) as release,
            patch.object(agent_chat.session_module, "new_session", return_value=(SESSION_NEW, blob)),
            patch.object(agent_chat.session_module, "save_session", wraps=session_module.save_session) as save,
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=_fake_turn),
        ):
            response = await agent_chat.agent_chat(_request(), _payload())

        self.assertIsInstance(response, StreamingResponse)
        # Accepting the prompt starts the inactivity window immediately,
        # before the model/tool turn has produced a response.
        save.assert_called_once_with(SESSION_NEW, blob)
        # The lease is held from before next_turn_id through the stream: a
        # second request for the same (now known) session id is busy.
        self.assertIsNone(session_module.acquire_session_lease(SESSION_NEW))
        with patch.object(agent_chat.admission, "release", release):
            chunks = [chunk async for chunk in response.body_iterator]
            self.assertEqual(_event_data(chunks[0])["turn_id"], "t1")
            release.assert_awaited_once_with(LEASE)
            # Released only after the stream (and its final save) completed.
            fresh = session_module.acquire_session_lease(SESSION_NEW)
            self.assertIsNotNone(fresh)
            self.assertTrue(session_module.release_session_lease(SESSION_NEW, fresh))

    async def test_expired_session_still_streams_error_and_releases_both(self):
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()) as release,
        ):
            response = await agent_chat.agent_chat(_request(), _payload(session_id=SESSION_EXPIRED))

        self.assertIsInstance(response, StreamingResponse)
        chunks = [chunk async for chunk in response.body_iterator]
        self.assertEqual(len(chunks), 3)
        self.assertEqual(_event_data(chunks[1])["code"], "session_expired")
        release.assert_awaited_once_with(LEASE)
        # The session lease claimed for the lookup was released too.
        fresh = session_module.acquire_session_lease(SESSION_EXPIRED)
        self.assertIsNotNone(fresh)
        self.assertTrue(session_module.release_session_lease(SESSION_EXPIRED, fresh))

    async def test_setup_failure_releases_session_and_admission_leases_once(self):
        _sid, blob = session_module.new_session()
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()) as release,
            patch.object(agent_chat.session_module, "new_session", return_value=(SESSION_NEW, blob)),
            patch.object(
                agent_chat.session_module, "next_turn_id", Mock(side_effect=RuntimeError("setup failed"))
            ),
        ):
            with self.assertRaises(RuntimeError):
                await agent_chat.agent_chat(_request(), _payload())

        release.assert_awaited_once_with(LEASE)
        # The claimed session lease was not leaked.
        fresh = session_module.acquire_session_lease(SESSION_NEW)
        self.assertIsNotNone(fresh)
        self.assertTrue(session_module.release_session_lease(SESSION_NEW, fresh))

    async def test_duplicate_turn_lost_update_regression_blocked_at_boundary(self):
        """The reproduced lost-update overlap is unreachable now.

        Before the fix, two same-session requests both loaded turn_seq == 0,
        both minted ``t1``, and the last save clobbered the first turn's
        history (turn_seq stuck at 1). With the lease, the second request is
        rejected while the first runs, and after it completes the retry
        serializes to ``t2`` with both turns' history persisted.
        """
        session_id, session = session_module.new_session()
        session_module.save_session(session_id, session)
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()) as release,
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=_fake_turn),
        ):
            first = await agent_chat.agent_chat(_request(), _payload(session_id=session_id, message="first"))
            self.assertIsInstance(first, StreamingResponse)
            # Simultaneous second request for the same session: busy, before
            # any load/next-turn/mutation.
            with self.assertRaises(HTTPException) as busy:
                await agent_chat.agent_chat(_request(), _payload(session_id=session_id, message="second"))
            self.assertEqual(busy.exception.status_code, 503)
            # First turn still holds the lease.
            self.assertIsNone(session_module.acquire_session_lease(session_id))
            first_chunks = [chunk async for chunk in first.body_iterator]
            # After the first turn completes (save + lease release), the
            # retry serializes to the next turn.
            second = await agent_chat.agent_chat(_request(), _payload(session_id=session_id, message="second"))
            second_chunks = [chunk async for chunk in second.body_iterator]

        turn_ids = [
            frame[1]["turn_id"]
            for chunk in first_chunks + second_chunks
            for frame in [_event_frame(chunk)]
            if frame[0] == "meta"
        ]
        self.assertEqual(turn_ids, ["t1", "t2"])
        loaded = session_module.load_session(session_id)
        self.assertEqual(loaded["turn_seq"], 2)
        self.assertEqual([entry["text"] for entry in loaded["history"]], ["first", "second"])
        # One admission acquire/release per request: the busy rejection also
        # released its admission lease.
        with patch.object(agent_chat.admission, "release", release):
            self.assertEqual(release.await_count, 3)

    async def test_current_location_survives_a_later_request_without_origin(self):
        session_id, session = session_module.new_session()
        session_module.save_session(session_id, session)
        seen_origins = []

        def recording_turn(**kwargs):
            seen_origins.append(kwargs["origin"])
            return _fake_turn(**kwargs)

        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()),
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=recording_turn),
        ):
            first = await agent_chat.agent_chat(
                _request(),
                _payload(
                    session_id=session_id,
                    message="find ramen near me",
                    origin={"lat": 40.6494, "lng": -73.9631},
                ),
            )
            _ = [chunk async for chunk in first.body_iterator]
            second = await agent_chat.agent_chat(
                _request(),
                _payload(session_id=session_id, message="route me to the first one"),
            )
            _ = [chunk async for chunk in second.body_iterator]

        self.assertEqual(
            seen_origins,
            [
                {"lat": 40.6494, "lng": -73.9631},
                {"lat": 40.6494, "lng": -73.9631},
            ],
        )
        self.assertEqual(
            session_module.current_location(session_module.load_session(session_id)),
            {"lat": 40.6494, "lng": -73.9631},
        )

    async def test_setup_session_release_raising_releases_admission_once(self):
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()) as release,
            patch.object(agent_chat.session_module, "load_session", Mock(side_effect=RuntimeError("load failed"))),
            patch.object(agent_chat.session_module, "release_session_lease", Mock(side_effect=RuntimeError("session release failed"))) as session_release,
        ):
            with self.assertRaises(RuntimeError):
                await agent_chat.agent_chat(_request(), _payload(session_id=SESSION_EXPIRED))

        release.assert_awaited_once_with(LEASE)
        session_release.assert_called_once_with(SESSION_EXPIRED, ANY)

    async def test_setup_admission_release_raising_releases_session_once(self):
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock(side_effect=RuntimeError("admission release failed"))) as release,
            patch.object(agent_chat.session_module, "next_turn_id", Mock(side_effect=RuntimeError("setup failed"))),
            patch.object(agent_chat.session_module, "release_session_lease", Mock()) as session_release,
        ):
            with self.assertRaises(RuntimeError) as error:
                await agent_chat.agent_chat(_request(), _payload())

        self.assertEqual(str(error.exception), "admission release failed")
        release.assert_awaited_once_with(LEASE)
        session_release.assert_called_once()

    async def test_expired_session_release_raising_releases_admission_once(self):
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            patch.object(agent_chat.admission, "acquire", AsyncMock(return_value=LEASE)),
            patch.object(agent_chat.admission, "release", AsyncMock()) as release,
            patch.object(agent_chat.session_module, "release_session_lease", Mock(side_effect=RuntimeError("session release failed"))) as session_release,
        ):
            with self.assertRaises(RuntimeError):
                await agent_chat.agent_chat(_request(), _payload(session_id=SESSION_EXPIRED))
            release.assert_awaited_once_with(LEASE)
            session_release.assert_called_once_with(SESSION_EXPIRED, ANY)


class AgentChatSessionLeaseStreamTests(unittest.IsolatedAsyncioTestCase):
    """Lease lifetime and release ordering through the real SSE stream."""

    def setUp(self):
        cache._mem.clear()

    def _spies(self, order: list[str]):
        real_session_release = agent_chat.session_module.release_session_lease
        save = Mock(side_effect=lambda _sid, _sess, **_kwargs: order.append("save"))
        release = AsyncMock(side_effect=lambda _lease: order.append("release"))
        session_release = Mock(
            side_effect=lambda _sid, token: (order.append("session-release"), real_session_release(_sid, token))[1]
        )
        return save, release, session_release

    async def test_normal_completion_releases_session_lease_after_save(self):
        _sid, session = session_module.new_session()
        token = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(token)
        order: list[str] = []
        save, release, session_release = self._spies(order)

        async def agen():
            yield agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)
            yield agent_events.DoneEvent(
                session_id=SESSION_ID, turn_id=TURN_ID, stop_reason="end_turn",
                usage={"input_tokens": 0, "output_tokens": 0},
            )

        with (
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen()),
            patch.object(agent_chat.session_module, "save_session", save),
            patch.object(agent_chat.admission, "release", release),
            patch.object(agent_chat.session_module, "release_session_lease", session_release),
        ):
            chunks = [chunk async for chunk in agent_chat._sse_stream(**_stream_args(session, session_lease_token=token))]

        self.assertEqual(len(chunks), 2)
        self.assertEqual(order, ["save", "release", "session-release"])
        save.assert_called_once()
        self.assertTrue(save.call_args.kwargs["refresh_ttl"])
        release.assert_awaited_once_with(LEASE)
        # The real release ran: the session is usable again.
        self.assertIsNone(cache.cache_get(session_module._session_lease_key(SESSION_ID)))

    async def test_disconnect_releases_session_lease_after_save(self):
        _sid, session = session_module.new_session()
        token = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(token)
        order: list[str] = []
        save, release, session_release = self._spies(order)
        started = asyncio.Event()

        async def agen():
            try:
                yield agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)
                started.set()
                await asyncio.sleep(5)
                yield agent_events.TokenEvent(text="never delivered")
            finally:
                order.append("finalize")

        request = _request(disconnected=True)
        with (
            patch.object(agent_chat, "HEARTBEAT_INTERVAL_S", 0.005),
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen()),
            patch.object(agent_chat.session_module, "save_session", save),
            patch.object(agent_chat.admission, "release", release),
            patch.object(agent_chat.session_module, "release_session_lease", session_release),
        ):
            chunks = [
                chunk
                async for chunk in agent_chat._sse_stream(
                    **_stream_args(session, request=request, session_lease_token=token)
                )
            ]

        self.assertEqual(len(chunks), 1)
        self.assertEqual(order, ["finalize", "save", "release", "session-release"])
        self.assertFalse(save.call_args.kwargs["refresh_ttl"])
        release.assert_awaited_once_with(LEASE)
        self.assertIsNone(cache.cache_get(session_module._session_lease_key(SESSION_ID)))

    async def test_caller_cancellation_releases_session_lease(self):
        _sid, session = session_module.new_session()
        token = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(token)
        order: list[str] = []
        save, release, session_release = self._spies(order)
        started = asyncio.Event()
        baseline = set(asyncio.all_tasks())

        async def agen():
            try:
                yield agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)
                started.set()
                await asyncio.sleep(5)
                yield agent_events.TokenEvent(text="never delivered")
            finally:
                order.append("finalize")

        with (
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen()),
            patch.object(agent_chat.session_module, "save_session", save),
            patch.object(agent_chat.admission, "release", release),
            patch.object(agent_chat.session_module, "release_session_lease", session_release),
        ):
            async def _consume():
                async for _chunk in agent_chat._sse_stream(**_stream_args(session, session_lease_token=token)):
                    pass

            stream_task = asyncio.ensure_future(_consume())
            await started.wait()
            stream_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stream_task

        self.assertTrue(stream_task.cancelled())
        self.assertEqual(order, ["finalize", "save", "release", "session-release"])
        self.assertFalse(save.call_args.kwargs["refresh_ttl"])
        release.assert_awaited_once_with(LEASE)
        self.assertIsNone(cache.cache_get(session_module._session_lease_key(SESSION_ID)))
        await _assert_no_owned_pending_tasks(baseline)

    async def test_save_failure_releases_both_leases_exactly_once(self):
        _sid, session = session_module.new_session()
        token = session_module.acquire_session_lease(SESSION_ID)
        self.assertIsNotNone(token)
        real_session_release = agent_chat.session_module.release_session_lease
        release = AsyncMock()
        session_release = Mock(side_effect=lambda _sid, tok: real_session_release(_sid, tok))

        async def agen():
            yield agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)

        with (
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen()),
            patch.object(
                agent_chat.session_module, "save_session", Mock(side_effect=RuntimeError("save failed"))
            ),
            patch.object(agent_chat.admission, "release", release),
            patch.object(agent_chat.session_module, "release_session_lease", session_release),
        ):
            with self.assertRaises(RuntimeError):
                async for _chunk in agent_chat._sse_stream(**_stream_args(session, session_lease_token=token)):
                    pass

        release.assert_awaited_once_with(LEASE)
        session_release.assert_called_once_with(SESSION_ID, token)
        # The real ownership-safe release ran and the session is usable again.
        self.assertIsNone(cache.cache_get(session_module._session_lease_key(SESSION_ID)))


if __name__ == "__main__":
    unittest.main()
