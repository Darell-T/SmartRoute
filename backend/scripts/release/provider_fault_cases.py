"""Offline model-provider fault cases for release validation.

These cases use only the production model-stream and agent-turn seams with
fake provider streams.  They never construct a network-capable provider.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Protocol
from unittest.mock import patch


FIXED_SEEDS = (37, 73, 109)
DEADLINE_SECONDS = 0.02
MAX_DEADLINE_WALL_SECONDS = 0.35
MAX_JITTER_DELAY_SECONDS = 0.002
MAX_JITTER_WALL_SECONDS = 0.25
MODEL_CASES = (
    "invalid_request",
    "invalid_credentials",
    "rate_limited",
    "model_unavailable",
    "deadline_stall",
    "disconnect",
    "stream_jitter",
    "agent_turn_terminal",
)
_FAULT_CASES = (
    "invalid_request",
    "invalid_credentials",
    "rate_limited",
    "model_unavailable",
    "deadline_stall",
    "disconnect",
)


class FaultCaseError(RuntimeError):
    """A fixed model/provider safety contract failed."""


class ModelStreamSeam(Protocol):
    ModelCallCompleted: type

    def stream_model_call(
        self,
        *,
        client: object,
        stream_kwargs: dict[str, object],
        log_tag: str,
        retry_count: int,
        sanitize_text: Callable[[str], str],
        deadline_monotonic: float | None = None,
    ) -> AsyncIterator[object]: ...


class _ProviderError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.request_id = "offline-fault"
        self.body = {"error": {"type": "offline_fault", "message": "offline fault"}}


class _FailingStream:
    def __init__(self, status_code: int) -> None:
        self._error = _ProviderError(status_code)

    async def __aenter__(self) -> "_FailingStream":
        raise self._error

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _StalledStream:
    def __init__(self, started: asyncio.Event | None = None) -> None:
        self._started = started
        self._release = asyncio.Event()

    async def __aenter__(self) -> "_StalledStream":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def __aiter__(self):
        if self._started is not None:
            self._started.set()
        await self._release.wait()
        yield None

    async def get_final_message(self) -> object:
        raise AssertionError("a stalled provider must not reach a final message")


class _JitteredTextStream:
    def __init__(self, chunks: list[str], delays: list[float]) -> None:
        self._chunks = chunks
        self._delays = delays

    async def __aenter__(self) -> "_JitteredTextStream":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def __aiter__(self):
        yield SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text"))
        for delay, chunk in zip(self._delays, self._chunks, strict=True):
            await asyncio.sleep(delay)
            yield SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="text_delta", text=chunk),
            )
        yield SimpleNamespace(type="content_block_stop", index=0)

    async def get_final_message(self) -> object:
        return SimpleNamespace(content=[], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=0, output_tokens=0))


class _Messages:
    def __init__(self, stream: object) -> None:
        self._stream = stream

    def stream(self, **_kwargs: object) -> object:
        return self._stream


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FaultCaseError(reason)


async def _model_fault(
    model_stream: ModelStreamSeam, *, status_code: int, expected_code: str, retryable: bool
) -> None:
    client = SimpleNamespace(messages=_Messages(_FailingStream(status_code)))
    events = [
        event
        async for event in model_stream.stream_model_call(
            client=client, stream_kwargs={}, log_tag="offline provider fault", retry_count=0,
            sanitize_text=lambda value: value,
        )
    ]
    completed = [event for event in events if isinstance(event, model_stream.ModelCallCompleted)]
    _require(len(completed) == 1, "provider fault must emit one terminal outcome")
    outcome = completed[0]
    _require(outcome.error is not None, "provider fault terminal outcome must be an error")
    _require(outcome.error.code == expected_code, "provider status classification changed")
    _require(outcome.error.retryable is retryable, "provider retryability classification changed")


async def _deadline_fault(model_stream: ModelStreamSeam) -> None:
    client = SimpleNamespace(messages=_Messages(_StalledStream()))
    started = time.monotonic()
    events = [
        event
        async for event in model_stream.stream_model_call(
            client=client, stream_kwargs={}, log_tag="offline provider stall", retry_count=0,
            sanitize_text=lambda value: value, deadline_monotonic=time.monotonic() + DEADLINE_SECONDS,
        )
    ]
    completed = [event for event in events if isinstance(event, model_stream.ModelCallCompleted)]
    _require(len(completed) == 1, "stalled provider must emit one terminal outcome")
    _require(completed[0].error is not None and completed[0].error.code == "deadline", "stall must map to deadline")
    _require(time.monotonic() - started <= MAX_DEADLINE_WALL_SECONDS, "stalled provider exceeded deadline wall-time bound")


async def _disconnect_fault(model_stream: ModelStreamSeam) -> None:
    started = asyncio.Event()
    client = SimpleNamespace(messages=_Messages(_StalledStream(started)))
    stream = model_stream.stream_model_call(
        client=client, stream_kwargs={}, log_tag="offline provider disconnect", retry_count=0,
        sanitize_text=lambda value: value,
    )
    task = asyncio.create_task(anext(stream))
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise FaultCaseError("consumer cancellation must propagate")
    await stream.aclose()


def _jitter_schedule(seed: int) -> tuple[str, list[str], list[float]]:
    text = "Grounded route evidence remains stable."
    randomizer = random.Random(seed)
    chunks: list[str] = []
    cursor = 0
    while cursor < len(text):
        size = randomizer.randint(1, 6)
        chunks.append(text[cursor : cursor + size])
        cursor += size
    delays = [randomizer.randint(0, 2) * MAX_JITTER_DELAY_SECONDS / 2 for _chunk in chunks]
    return text, chunks, delays


async def _stream_jitter(model_stream: ModelStreamSeam, seed: int) -> None:
    text, chunks, delays = _jitter_schedule(seed)
    client = SimpleNamespace(messages=_Messages(_JitteredTextStream(chunks, delays)))
    started = time.monotonic()
    events = [
        event
        async for event in model_stream.stream_model_call(
            client=client, stream_kwargs={}, log_tag="offline provider jitter", retry_count=0,
            sanitize_text=lambda value: value,
        )
    ]
    tokens = [event.text for event in events if getattr(event, "type", "") == "token"]
    completed = [event for event in events if isinstance(event, model_stream.ModelCallCompleted)]
    _require("".join(tokens) == text, "jitter changed the ordered provider text")
    _require(len(completed) == 1 and completed[0].error is None, "jitter must keep one successful terminal outcome")
    _require(time.monotonic() - started <= MAX_JITTER_WALL_SECONDS, "jitter exceeded wall-time bound")


async def _agent_turn_deadline_fault() -> None:
    from app.services.agent import loop, session as session_module

    session_id, session = session_module.new_session()
    client = SimpleNamespace(messages=_Messages(_StalledStream()))
    started = time.monotonic()
    with patch.object(loop, "AGENT_MOCK_MODE", False), patch.object(loop, "AGENT_TURN_DEADLINE_S", DEADLINE_SECONDS), patch.object(
        loop, "client", client
    ), patch.object(loop.budget, "agent_enabled", return_value=True), patch.object(
        loop.budget, "check_session_rate_limit", return_value=True
    ), patch.object(loop.budget, "daily_spend_exceeded", return_value=False), patch.object(
        loop.budget, "concurrency_semaphore", return_value=asyncio.Semaphore(1)
    ):
        events = [
            event
            async for event in loop.run_agent_turn(
                session=session, session_id=session_id, turn_id="offline-fault-turn",
                message="Is the subway running tonight?", now_et="2026-07-22T17:30:00-04:00",
            )
        ]
    done = [event for event in events if event.type == "done"]
    errors = [event for event in events if event.type == "error"]
    _require(events[0].type == "meta", "agent turn must begin with metadata")
    _require(len(done) == 1 and events[-1] is done[0], "agent turn must end with one terminal event")
    _require(done[0].stop_reason == "deadline", "stalled provider must produce a deadline terminal event")
    _require(len(errors) == 1 and errors[0].code == "deadline", "stalled provider must expose a deadline error")
    _require(time.monotonic() - started <= MAX_DEADLINE_WALL_SECONDS, "agent turn exceeded deadline wall-time bound")


async def run_model_fault_cases() -> dict[str, object]:
    """Exercise fixed-seed fake-provider faults through production stream seams."""

    from app.services.agent.model import stream as model_stream

    await _agent_turn_deadline_fault()
    for seed in FIXED_SEEDS:
        await _stream_jitter(model_stream, seed)
        for case in random.Random(seed).sample(_FAULT_CASES, k=len(_FAULT_CASES)):
            if case == "invalid_request":
                await _model_fault(model_stream, status_code=400, expected_code="invalid_request", retryable=False)
            elif case == "invalid_credentials":
                await _model_fault(model_stream, status_code=401, expected_code="provider_configuration", retryable=False)
            elif case == "rate_limited":
                await _model_fault(model_stream, status_code=429, expected_code="rate_limited", retryable=True)
            elif case == "model_unavailable":
                await _model_fault(model_stream, status_code=529, expected_code="upstream_error", retryable=True)
            elif case == "deadline_stall":
                await _deadline_fault(model_stream)
            else:
                await _disconnect_fault(model_stream)
    return {
        "seeds": ",".join(str(seed) for seed in FIXED_SEEDS),
        "seeded_case_runs": len(FIXED_SEEDS) * (len(_FAULT_CASES) + 1),
        "deadline_ms": round(DEADLINE_SECONDS * 1000),
        "deadline_wall_bound_ms": round(MAX_DEADLINE_WALL_SECONDS * 1000),
        "jitter_wall_bound_ms": round(MAX_JITTER_WALL_SECONDS * 1000),
    }
