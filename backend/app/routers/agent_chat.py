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
from pydantic import BaseModel, Field, field_validator

from app.services.agent import events as agent_events
from app.services.agent import loop as agent_loop
from app.services.agent import session as session_module
from app.utils.geo import NYC_BOUNDS

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
HEARTBEAT_INTERVAL_S = 15

# The agent requires Redis-backed sessions in production -- utils/cache.py's
# in-memory fallback does not survive a process restart or scale beyond one
# worker. Override for local dev/tests only.
AGENT_ALLOW_MEMORY_SESSIONS = os.getenv("AGENT_ALLOW_MEMORY_SESSIONS", "0").strip() == "1"


class AgentOrigin(BaseModel):
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
    session_id: str | None = None
    message: str = Field(..., min_length=1, max_length=500)
    origin: AgentOrigin | None = None
    selected_card_id: str | None = None
    response_presentation: str = "auto"

    @field_validator("response_presentation", mode="before")
    @classmethod
    def _normalize_response_presentation(cls, value: object) -> str:
        return "quick" if str(value or "").strip().lower() == "quick" else "auto"


def _log_sess(session_id: str) -> str:
    return session_id[:6] if session_id else ""


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
            yield agent_events.sse_format(event)
    finally:
        # Always persist, including on client disconnect / task cancellation --
        # whatever route cards, history, and slots accumulated before the
        # drop should survive for the rider's next message.
        session_module.save_session(session_id, session)


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

    session_load_started = time.monotonic()
    if payload.session_id:
        session = session_module.load_session(payload.session_id)
        if session is None:
            return StreamingResponse(
                _expired_session_stream(payload.session_id),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )
        session_id = payload.session_id
    else:
        session_id, session = session_module.new_session()
    trace = agent_loop.TurnTrace(
        stage_ms={
            "session_load_ms": (time.monotonic() - session_load_started) * 1000,
        }
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

    return StreamingResponse(
        _sse_stream(
            request,
            session_id,
            session,
            turn_id,
            payload.message,
            now_et,
            gtfs,
            origin,
            payload.selected_card_id,
            payload.response_presentation,
            trace,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
