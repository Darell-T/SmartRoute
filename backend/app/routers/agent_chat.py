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
import contextlib
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services import admission
from app.services.agent import events as agent_events
from app.services.agent import loop as agent_loop
from app.services.agent import session as session_module
from app.services.geography import NYC_BOUNDS

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
HEARTBEAT_INTERVAL_S = 15
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


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1, max_length=128)


def _sessions_available() -> bool:
    return bool(os.getenv("REDIS_URL")) or AGENT_ALLOW_MEMORY_SESSIONS


def _log_sess(session_id: str) -> str:
    return session_id[:6] if session_id else ""


def _session_busy_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="That conversation is still processing a previous message. Please wait a moment and try again.",
        headers={"Retry-After": "1"},
    )


def _claim_turn_lease(session_id: str) -> str:
    token = session_module.acquire_session_lease(session_id)
    if token is None:
        raise _session_busy_error()
    return token


async def _release_turn_leases(session_id: str, session_lease_token: str | None, lease: admission.AdmissionLease) -> None:
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
    next_event: asyncio.Future | None = None
    response_succeeded = False
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
            next_event = None
            if isinstance(event, agent_events.DoneEvent):
                response_succeeded = event.stop_reason in {
                    "end_turn",
                    "clarification_required",
                }
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
                with contextlib.suppress(StopAsyncIteration, asyncio.CancelledError):
                    await next_event
            # No __anext__ is running now; close the generator explicitly so
            # its finally also runs when it was suspended at a yield. No-op
            # when the cancellation above already closed it.
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await agen.aclose()
        finally:
            try:
                session_module.save_session(
                    session_id,
                    session,
                    refresh_ttl=response_succeeded,
                )
            finally:
                try:
                    await admission.release(lease)
                finally:
                    session_module.release_session_lease(session_id, session_lease_token)


@router.post("/api/agent/chat")
async def agent_chat(request: Request, payload: AgentChatRequest):
    if not _sessions_available():
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
            session_lease_token = _claim_turn_lease(session_id)
            session = session_module.load_session(session_id)
            if session is None:
                expired_session = True
        else:
            session_id, session = session_module.new_session()
            session_lease_token = _claim_turn_lease(session_id)
    except BaseException:
        if session_lease_token is not None:
            await _release_turn_leases(session_id, session_lease_token, lease)
        else:
            await admission.release(lease)
        raise
    if expired_session:
        await _release_turn_leases(session_id, session_lease_token, lease)
        return StreamingResponse(
            _expired_session_stream(session_id),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    try:
        incoming_origin = (
            {"lat": payload.origin.lat, "lng": payload.origin.lng}
            if payload.origin
            else None
        )
        origin = session_module.update_current_location(session, incoming_origin)
        session_module.save_session(session_id, session)
        trace = agent_loop.TurnTrace(
            stage_ms={"session_load_ms": (time.monotonic() - session_load_started) * 1000}
        )
        gtfs = getattr(request.app.state, "gtfs", None)
        turn_id = session_module.next_turn_id(session)
        now_et = datetime.now(ZoneInfo("America/New_York")).isoformat()
        origin_source = "request" if incoming_origin else "session" if origin else "missing"
        print(
            f"[agent-chat] sess[{_log_sess(session_id)}] turn={turn_id} "
            f"msg_len={len(payload.message)} origin_source={origin_source} "
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


@router.post("/api/agent/chat/session")
async def agent_chat_session_snapshot(payload: SessionRequest):
    """Restore the complete rider-visible transcript for one live session."""
    if not _sessions_available():
        raise HTTPException(status_code=503, detail="Chat sessions are unavailable.")
    session = session_module.load_session(payload.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session_expired")
    snapshot = session_module.transcript_snapshot(session)
    return {"session_id": payload.session_id, **snapshot}


@router.post("/api/agent/chat/session/reset")
async def agent_chat_session_reset(payload: SessionRequest):
    """End a conversation; idempotent so New Trip can always clear locally."""
    if not _sessions_available():
        raise HTTPException(status_code=503, detail="Chat sessions are unavailable.")
    session_module.delete_session(payload.session_id)
    return {"ok": True}
