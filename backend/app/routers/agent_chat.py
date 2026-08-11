"""POST /api/agent/chat -- SSE-over-POST endpoint for the conversational
transit agent. Mounted under `protected_api` in main.py (X-App-Key auth
applies automatically, same as every other route).

Budget/rate/kill-switch checks happen inside loop.run_agent_turn() itself,
before any model call -- this router's job is transport: session lookup,
request validation, heartbeats, disconnect handling, and making sure the
session is always saved (including on a client disconnect mid-stream).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.agent import events as agent_events
from app.services.agent import loop as agent_loop
from app.services.agent import session as session_module
from app.services import admission
from app.utils.geo import NYC_BOUNDS

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
HEARTBEAT_INTERVAL_S = 15

# The agent requires Redis-backed sessions in production -- utils/cache.py's
# in-memory fallback does not survive a process restart or scale beyond one
# worker. Override for local dev/tests only.
AGENT_ALLOW_MEMORY_SESSIONS = os.getenv("AGENT_ALLOW_MEMORY_SESSIONS", "0").strip() == "1"


class AgentOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float
    lng: float


    @field_validator("lat")
    @classmethod
    def _lat_in_bounds(cls, value: float) -> float:
        if not (NYC_BOUNDS["min_lat"] <= value <= NYC_BOUNDS["max_lat"]):
            raise ValueError("origin latitude is outside the NYC service area")
        return value

    @field_validator("lng")
    @classmethod
    def _lng_in_bounds(cls, value: float) -> float:
        if not (NYC_BOUNDS["min_lon"] <= value <= NYC_BOUNDS["max_lon"]):
            raise ValueError("origin longitude is outside the NYC service area")
        return value


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    message: str = Field(..., min_length=1, max_length=500)
    origin: AgentOrigin | None = None
    selected_card_id: str | None = None
    response_presentation: str = "auto"


    @field_validator("response_presentation", mode="before")
    @classmethod
    def _normalize_response_presentation(cls, value: object) -> str:
        return "quick" if str(value or "").strip().lower() == "quick" else "auto"

    @field_validator("session_id")
    @classmethod
    def _bounded_session(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 128:
            raise ValueError("session identifier is too long")
        return value

    @field_validator("selected_card_id")
    @classmethod
    def _bounded_card(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 64:
            raise ValueError("card identifier is too long")
        return value


def _log_sess(session_id: str) -> str:
    return session_id[:6] if session_id else ""


def _session_busy_error() -> HTTPException:
    # Bounded retryable rejection for a second simultaneous turn on the same
    # session. Short Retry-After only; never leaks the lock token or any
    # session contents.
    return HTTPException(
        status_code=503,
        detail="That conversation is still processing a previous message. Please wait a moment and try again.",
        headers={"Retry-After": "1"},
    )


async def _release_turn_leases(session_id: str, session_lease_token: str | None, lease: admission.AdmissionLease) -> None:
    """Release the per-session turn lease, then the admission lease. Nested
    finally guarantees both are attempted exactly once even when one raises;
    the last raised exception propagates if both fail."""
    try:
        if session_lease_token is not None:
            session_module.release_session_lease(session_id, session_lease_token)
    finally:
        await admission.release(lease)


async def _expired_session_stream(session_id: str):
    turn_id = "t0"
    yield agent_events.sse_format(agent_events.MetaEvent(session_id=session_id, turn_id=turn_id))
    yield agent_events.sse_format(
        agent_events.ErrorEvent(
            code="session_expired",
            message="That conversation has expired. Starting a new message will begin a fresh session.",
            retryable=True,
        )
    )
    yield agent_events.sse_format(
        agent_events.DoneEvent(
            session_id=session_id, turn_id=turn_id, stop_reason="error", usage={"input_tokens": 0, "output_tokens": 0}
        )
    )


async def _sse_stream(
    request: Request,
    session_id: str,
    session: dict,
    turn_id: str,
    message: str,
    now_et: str,
    gtfs,
    origin: dict | None,
    selected_card_id: str | None,
    response_presentation: str,
    trace: agent_loop.TurnTrace,
    lease: admission.AdmissionLease,
    session_lease_token: str | None = None,
):
    agen = agent_loop.run_agent_turn(
        session=session,
        session_id=session_id,
        turn_id=turn_id,
        message=message,
        now_et=now_et,
        gtfs=gtfs,
        origin=origin,
        selected_card_id=selected_card_id,
        response_presentation=response_presentation,
        trace=trace,
    )
    # The currently in-flight `agen.__anext__()` task, if any. Tracked across
    # the stream's whole lifetime so cleanup can always cancel and drain it
    # before touching the session.
    next_event: asyncio.Future | None = None
    try:
        while True:
            next_event = asyncio.ensure_future(agen.__anext__())
            while True:
                done, _pending = await asyncio.wait({next_event}, timeout=HEARTBEAT_INTERVAL_S)
                if next_event in done:
                    break
                if await request.is_disconnected():
                    next_event.cancel()
                    print(f"[agent-chat] client disconnected sess[{_log_sess(session_id)}] turn={turn_id}")
                    return
                yield ": ping\n\n"
            try:
                event = next_event.result()
            except StopAsyncIteration:
                return
            next_event = None  # this __anext__ finished; nothing is pending
            yield agent_events.sse_format(event)
    finally:
        try:
            # Cancel any still-pending __anext__ and await it so CancelledError
            # reaches run_agent_turn and its finally (turn finalization, which
            # mutates history/telemetry) completes before we persist the
            # session. Only the expected child conditions are suppressed: a
            # caller cancellation stays in flight and keeps propagating after
            # this finally.
            if next_event is not None:
                next_event.cancel()
            try:
                if next_event is not None:
                    await next_event
            except StopAsyncIteration:
                pass
            except asyncio.CancelledError:
                pass
            # No __anext__ is running now; close the generator explicitly so
            # its finally also runs when it was suspended at a yield. No-op
            # when the cancellation above already closed it.
            try:
                await agen.aclose()
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
        finally:
            # Always persist, including on client disconnect / task
            # cancellation -- whatever route cards, history, and slots
            # accumulated before the drop should survive for the rider's next
            # message. Nested finallys keep both releases protected even if
            # the save itself raises: the admission lease exactly once, then
            # the per-session turn lease so the next request for this session
            # can proceed.
            try:
                session_module.save_session(session_id, session)
            finally:
                try:
                    await admission.release(lease)
                finally:
                    session_module.release_session_lease(session_id, session_lease_token)


@router.post("/api/agent/chat")
async def agent_chat(request: Request, payload: AgentChatRequest):
    if not os.getenv("REDIS_URL") and not AGENT_ALLOW_MEMORY_SESSIONS:
        raise HTTPException(
            status_code=503,
            detail=(
                "Chat requires Redis-backed sessions "
                "(REDIS_URL is not set); the in-memory cache fallback is not "
                "durable enough for this feature."
            ),
        )

    try:
        lease = await admission.acquire(
            admission.principal_from_request(request.headers.get("X-SmartRoute-Principal")),
            "chat",
        )
    except admission.AdmissionDenied as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=("Request identity is invalid." if exc.status_code == 403 else "Request admission is temporarily unavailable." if exc.status_code == 503 else "Too many requests."),
            headers={"Retry-After": str(exc.retry_after_s)},
        ) from None

    session_lease_token: str | None = None
    session_load_started = time.monotonic()
    expired_session = False
    try:
        if payload.session_id:
            session_id = payload.session_id
            # One active chat turn per session: claim the turn lease before
            # loading so a busy same-session request fails fast with a
            # retryable response before any load, next-turn, model/tool, or
            # state-mutation work.
            session_lease_token = session_module.acquire_session_lease(session_id)
            if session_lease_token is None:
                raise _session_busy_error()
            session = session_module.load_session(session_id)
            if session is None:
                expired_session = True
        else:
            session_id, session = session_module.new_session()
            # Claim immediately after minting and before next_turn_id: the
            # client learns the session id from early SSE metadata while turn
            # 1 is still running, so a follow-up must serialize against this
            # turn from the start.
            session_lease_token = session_module.acquire_session_lease(session_id)
            if session_lease_token is None:
                raise _session_busy_error()
    except BaseException:
        if session_lease_token is not None:
            await _release_turn_leases(session_id, session_lease_token, lease)
        else:
            # Busy rejection or a mint failure before the claim: only the
            # admission lease is held.
            await admission.release(lease)
        raise
    if expired_session:
        # Release both leases before streaming the expired error. This runs
        # outside the setup try so a raising release can never trigger a
        # second cleanup attempt from the handler above.
        await _release_turn_leases(session_id, session_lease_token, lease)
        return StreamingResponse(
            _expired_session_stream(session_id),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    try:
        trace = agent_loop.TurnTrace(
            stage_ms={"session_load_ms": (time.monotonic() - session_load_started) * 1000}
        )
        gtfs = getattr(request.app.state, "gtfs", None)
        turn_id = session_module.next_turn_id(session)
        now_et = datetime.now(ZoneInfo("America/New_York")).isoformat()
        origin = {"lat": payload.origin.lat, "lng": payload.origin.lng} if payload.origin else None
        print(
            f"[agent-chat] sess[{_log_sess(session_id)}] turn={turn_id} "
            f"msg_len={len(payload.message)} origin_present={'yes' if origin else 'no'} "
            f"selected_card={'yes' if payload.selected_card_id else 'no'}"
            f" presentation={payload.response_presentation}"
        )
        response = StreamingResponse(
            _sse_stream(request, session_id, session, turn_id, payload.message, now_et, gtfs,
                        origin, payload.selected_card_id, payload.response_presentation, trace, lease,
                        session_lease_token),
            media_type="text/event-stream", headers=_SSE_HEADERS,
        )
    except BaseException:
        await _release_turn_leases(session_id, session_lease_token, lease)
        raise
    return response
