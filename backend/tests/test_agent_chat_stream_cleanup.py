"""Focused tests for agent-chat SSE stream cleanup ordering.

Proves that on client disconnect or caller cancellation the in-flight
``run_agent_turn`` ``__anext__`` task is cancelled and awaited so the
generator's ``finally`` (turn finalization) completes *before* the session is
saved, and the admission lease is released only after the save. Uses real
async generators with real ``finally`` blocks plus ordered side-effect spies
to prove ordering, and bounded task-baseline assertions to prove no owned
pending task leaks.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.routers import agent_chat
from app.services import admission
from app.services.agent import events as agent_events


PRINCIPAL = "v1.test-principal-opaque-123456"
SESSION_ID = "sess-test-0001"
TURN_ID = "turn-test-1"
LEASE = admission.AdmissionLease(PRINCIPAL, "chat", "lease-token-1")


def _request(*, disconnected: bool = False):
    return SimpleNamespace(
        is_disconnected=AsyncMock(return_value=disconnected),
    )


def _args(session: dict, *, request=None):
    return dict(
        request=request if request is not None else _request(),
        session_id=SESSION_ID,
        session=session,
        turn_id=TURN_ID,
        message="hello",
        now_et="2026-08-09T12:00:00-04:00",
        gtfs=None,
        origin=None,
        selected_card_id=None,
        response_presentation="auto",
        trace=None,
        lease=LEASE,
    )


def _pending_generator(session: dict, order: list[str], *, started=None, pending_s: float):
    """Real async generator that blocks mid-turn and records its finally."""

    async def agen():
        try:
            yield agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)
            if started is not None:
                started.set()
            await asyncio.sleep(pending_s)
            yield agent_events.TokenEvent(text="never delivered")
        finally:
            session["finalized_at"] = "yes"
            order.append("finalize")

    return agen()


async def _assert_no_owned_pending_tasks(baseline: set[asyncio.Task]) -> None:
    # Let the loop settle a few turns so genuinely-cancelled tasks surface
    # deterministically before the bounded baseline comparison.
    for _ in range(3):
        await asyncio.sleep(0)
    owned = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task not in baseline
    ]
    assert owned == [], f"leaked pending tasks: {owned}"


class AgentChatStreamCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_awaits_generator_before_save_before_release(self):
        session: dict = {}
        order: list[str] = []
        baseline = set(asyncio.all_tasks())
        request = _request(disconnected=True)
        agen = _pending_generator(session, order, pending_s=0.5)

        with (
            patch.object(agent_chat, "HEARTBEAT_INTERVAL_S", 0.005),
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen),
            patch.object(agent_chat.session_module, "save_session", Mock(side_effect=lambda _sid, _sess, **_kwargs: order.append("save"))) as save,
            patch.object(agent_chat.admission, "release", AsyncMock(side_effect=lambda _lease: order.append("release"))) as release,
        ):
            chunks = [chunk async for chunk in agent_chat._sse_stream(**_args(session, request=request))]

        # The heartbeat fired while the second __anext__ was pending, the
        # stream detected the disconnect, and cleanup drained the generator
        # before persisting.
        self.assertEqual(chunks, [agent_events.sse_format(agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID))])
        self.assertTrue(request.is_disconnected.await_count >= 1)
        self.assertEqual(order, ["finalize", "save", "release"])
        # The finalizer's mutation was visible to the save (finalize ran first).
        self.assertEqual(save.call_args.args[0], SESSION_ID)
        self.assertEqual(save.call_args.args[1]["finalized_at"], "yes")
        self.assertFalse(save.call_args.kwargs["refresh_ttl"])
        save.assert_called_once()
        release.assert_awaited_once_with(LEASE)
        self.assertIsNone(agen.ag_frame, "generator must be closed after disconnect cleanup")
        await _assert_no_owned_pending_tasks(baseline)

    async def test_caller_cancellation_drains_child_and_preserves_cancelled_error(self):
        session: dict = {}
        order: list[str] = []
        baseline = set(asyncio.all_tasks())
        started = asyncio.Event()
        agen = _pending_generator(session, order, started=started, pending_s=5)

        with (
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen),
            patch.object(agent_chat.session_module, "save_session", Mock(side_effect=lambda _sid, _sess, **_kwargs: order.append("save"))) as save,
            patch.object(agent_chat.admission, "release", AsyncMock(side_effect=lambda _lease: order.append("release"))) as release,
        ):
            # _sse_stream is an async generator; run the consumer that iterates
            # it (mirrors StreamingResponse's task) and cancel that task.
            async def _consume():
                async for _chunk in agent_chat._sse_stream(**_args(session)):
                    pass

            stream_task = asyncio.ensure_future(_consume())
            await started.wait()  # generator is inside the pending __anext__
            stream_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stream_task
        self.assertTrue(stream_task.cancelled())
        self.assertEqual(order, ["finalize", "save", "release"])
        self.assertEqual(save.call_args.args[1]["finalized_at"], "yes")
        self.assertFalse(save.call_args.kwargs["refresh_ttl"])
        save.assert_called_once()
        release.assert_awaited_once_with(LEASE)
        self.assertIsNone(agen.ag_frame, "generator must be closed after caller cancellation")
        await _assert_no_owned_pending_tasks(baseline)

    async def test_normal_exhaustion_saves_once_and_releases_once(self):
        session: dict = {}
        order: list[str] = []
        baseline = set(asyncio.all_tasks())

        async def agen():
            try:
                yield agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)
                yield agent_events.TokenEvent(text="done talking")
            finally:
                order.append("finalize")

        with (
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen()),
            patch.object(agent_chat.session_module, "save_session", Mock(side_effect=lambda _sid, _sess, **_kwargs: order.append("save"))) as save,
            patch.object(agent_chat.admission, "release", AsyncMock(side_effect=lambda _lease: order.append("release"))) as release,
        ):
            chunks = [chunk async for chunk in agent_chat._sse_stream(**_args(session))]

        self.assertEqual(
            chunks,
            [
                agent_events.sse_format(agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)),
                agent_events.sse_format(agent_events.TokenEvent(text="done talking")),
            ],
        )
        self.assertEqual(order, ["finalize", "save", "release"])
        self.assertFalse(save.call_args.kwargs["refresh_ttl"])
        save.assert_called_once()
        release.assert_awaited_once_with(LEASE)
        await _assert_no_owned_pending_tasks(baseline)

    async def test_inner_failure_still_finalizes_saves_and_releases(self):
        session: dict = {}
        order: list[str] = []
        baseline = set(asyncio.all_tasks())

        async def agen():
            try:
                yield agent_events.MetaEvent(session_id=SESSION_ID, turn_id=TURN_ID)
                raise RuntimeError("provider blew up")
            finally:
                session["finalized_at"] = "yes"
                order.append("finalize")

        with (
            patch.object(agent_chat.agent_loop, "run_agent_turn", new=lambda **kw: agen()),
            patch.object(agent_chat.session_module, "save_session", Mock(side_effect=lambda _sid, _sess, **_kwargs: order.append("save"))) as save,
            patch.object(agent_chat.admission, "release", AsyncMock(side_effect=lambda _lease: order.append("release"))) as release,
        ):
            with self.assertRaises(RuntimeError):
                async for _chunk in agent_chat._sse_stream(**_args(session)):
                    pass

        self.assertEqual(order, ["finalize", "save", "release"])
        self.assertEqual(save.call_args.args[1]["finalized_at"], "yes")
        self.assertFalse(save.call_args.kwargs["refresh_ttl"])
        save.assert_called_once()
        release.assert_awaited_once_with(LEASE)
        await _assert_no_owned_pending_tasks(baseline)
