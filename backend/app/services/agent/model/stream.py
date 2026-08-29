"""Immediate model-round streaming with bounded server-tool progress."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

import anthropic

from app.services.agent import events as agent_events
from app.services.agent.model import request as model_request

MODEL_ATTEMPT_TIMEOUT_S = max(
    1.0, float(os.getenv("AGENT_MODEL_ATTEMPT_TIMEOUT_S", "15"))
)

_SUSPICIOUS_TEXT = re.compile(
    r"[*_`~]|\bcard\s*$|\b(?:rc|mock|cd|cs|pl|ds)[_-]|\bChIJ|"
    r"\b(?:prepare_route_options|present_route|get_place_details|"
    r"search_local_places|destination_place_id|place_id|candidate_id|"
    r"candidate_set_id|discovery_set_id|tool_use|tool_result)\b|"
    r"\b(?:give\s+me\s+(?:a\s+)?moment|"
    r"waiting\s+for\s+(?:the\s+)?results|results\s+shortly|let\s+me\s+call)",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class ModelCallCompleted:
    final_message: object | None
    error: agent_events.ErrorEvent | None
    attempts: int
    web_search_ms: float = 0.0
    server_tool_call_count: int = 0
    first_token_ms: float | None = None
    web_timed_out: bool = False
    web_used: bool = False
    web_succeeded: bool = False
    web_sources: tuple[dict[str, str], ...] = ()


@dataclasses.dataclass
class _AttemptState:
    """Mutable observations from one provider stream attempt."""

    sanitizer: _RiderTextSanitizer
    call_started: float
    saw_text: bool = False
    saw_provider_event: bool = False
    text_indexes: set[int] = dataclasses.field(default_factory=set)
    web_started: dict[str, float] = dataclasses.field(default_factory=dict)
    web_results: list[bool] = dataclasses.field(default_factory=list)
    web_search_ms: float = 0.0
    server_tool_calls: int = 0
    first_token_ms: float | None = None

    def web_flags(self, *, timed_out: bool = False) -> dict[str, bool]:
        used = self.server_tool_calls > 0 or timed_out or bool(self.web_results)
        succeeded = (
            bool(self.web_results)
            and all(self.web_results)
            and not self.web_started
            and not timed_out
        )
        return {
            "web_used": used,
            "web_succeeded": succeeded,
            "web_timed_out": timed_out,
        }


RetryMode = Literal["none", "immediate", "backoff"]


@dataclasses.dataclass(frozen=True)
class _AttemptCompleted:
    state: _AttemptState
    final_message: object | None
    error: agent_events.ErrorEvent | None
    retry_mode: RetryMode = "none"
    web_timed_out: bool = False

    def public_result(self, attempts: int) -> ModelCallCompleted:
        return ModelCallCompleted(
            final_message=self.final_message,
            error=self.error,
            attempts=attempts,
            web_search_ms=self.state.web_search_ms,
            server_tool_call_count=self.state.server_tool_calls,
            first_token_ms=self.state.first_token_ms,
            web_sources=_web_sources(self.final_message),
            **self.state.web_flags(timed_out=self.web_timed_out),
        )


class _RiderTextSanitizer:
    """Stream normal prose immediately and hold only suspicious formatting.

    The model prompt forbids Markdown and internal identifiers. Holding the
    exceptional block lets the existing boundary sanitizer remove either
    without turning every ordinary model round back into a buffered response.
    """

    def __init__(self, sanitize: Callable[[str], str]) -> None:
        self._sanitize = sanitize
        self._pending = ""

    def feed(self, text: str) -> str:
        candidate = self._pending + text
        if self._pending or _SUSPICIOUS_TEXT.search(candidate):
            self._pending = candidate
            return ""
        return self._sanitize(candidate)

    def flush(self) -> str:
        if not self._pending:
            return ""
        text = self._sanitize(self._pending)
        self._pending = ""
        return text


def _first_byte_timeout_s(remaining_s: float | None) -> float:
    if remaining_s is None:
        return MODEL_ATTEMPT_TIMEOUT_S
    return min(MODEL_ATTEMPT_TIMEOUT_S, remaining_s)


def _remaining_deadline_s(deadline_monotonic: float | None) -> float | None:
    if deadline_monotonic is None:
        return None
    return max(0.0, deadline_monotonic - time.monotonic())


class WebResearchTimeoutError(TimeoutError):
    """Native web_search exceeded the bounded research allowance."""


async def _paced_provider_iter(
    iterator: AsyncIterator[Any],
    *,
    first_byte_s: float,
    deadline_monotonic: float | None,
    web_started: dict[str, float] | None = None,
    web_timeout_s: float | None = None,
) -> AsyncIterator[Any]:
    """Fail fast only while waiting for the first provider event.

    After the stream has started, wait until the turn deadline. An in-flight
    native web search may use a tighter bound so structured evidence can
    continue if research stalls.
    """

    try:
        async with asyncio.timeout(first_byte_s):
            first = await iterator.__anext__()
    except StopAsyncIteration:
        return
    yield first
    while True:
        timeout_s = _remaining_deadline_s(deadline_monotonic)
        if web_started and web_timeout_s:
            oldest = min(web_started.values())
            web_remaining = max(0.0, oldest + web_timeout_s - time.monotonic())
            timeout_s = web_remaining if timeout_s is None else min(timeout_s, web_remaining)
        try:
            async with asyncio.timeout(timeout_s):
                item = await iterator.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError:
            if web_started and web_timeout_s:
                oldest = min(web_started.values())
                if time.monotonic() >= oldest + web_timeout_s:
                    raise WebResearchTimeoutError() from None
            raise
        yield item


def _web_result_ok(content: object) -> bool:
    """True unless a web result block reports a server tool error.

    Accepts both Anthropic SDK objects and dict-shaped test doubles. An empty
    result list is a successful search (nothing to report), never a failure.
    Malformed scalar content (such as None) is not a parseable result block
    and fails safely instead of reading as a successful search.
    """
    items = content if isinstance(content, list) else [content]
    for item in items:
        if isinstance(item, dict):
            item_type = item.get("type")
        elif item is None or not hasattr(item, "type"):
            return False
        else:
            item_type = item.type
        if item_type == "web_search_tool_result_error":
            return False
    return True


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _web_sources(final_message: object | None) -> tuple[dict[str, str], ...]:
    """Extract the original pages cited by Anthropic native web search."""

    content = _field(final_message, "content") if final_message is not None else None
    if not isinstance(content, (list, tuple)):
        return ()

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in content:
        if _field(block, "type") != "text":
            continue
        citations = _field(block, "citations")
        if not isinstance(citations, (list, tuple)):
            continue
        for citation in citations:
            if _field(citation, "type") != "web_search_result_location":
                continue
            candidate = agent_events.normalized_source(
                {
                    "title": str(_field(citation, "title") or "Web source"),
                    "url": str(_field(citation, "url") or ""),
                }
            )
            if candidate is None or candidate["url"] in seen:
                continue
            seen.add(candidate["url"])
            sources.append(candidate)
            if len(sources) == 8:
                return tuple(sources)
    return tuple(sources)


async def _stream_provider_events(
    stream: object,
    state: _AttemptState,
    *,
    first_byte_s: float,
    deadline_monotonic: float | None,
    web_timeout_s: float | None,
) -> AsyncIterator[agent_events.AgentEvent]:
    """Translate one Anthropic stream into safe rider-visible events."""

    async for event in _paced_provider_iter(
        stream.__aiter__(),
        first_byte_s=first_byte_s,
        deadline_monotonic=deadline_monotonic,
        web_started=state.web_started,
        web_timeout_s=web_timeout_s,
    ):
        state.saw_provider_event = True
        event_type = getattr(event, "type", "")
        block = getattr(event, "content_block", None)
        block_type = getattr(block, "type", "")
        delta = getattr(event, "delta", None)
        if event_type == "content_block_start" and block_type == "text":
            state.text_indexes.add(int(getattr(event, "index", -1)))
        elif (
            event_type == "content_block_start"
            and block_type == "server_tool_use"
            and getattr(block, "name", "") == "web_search"
        ):
            state.server_tool_calls += 1
            tool_id = str(getattr(block, "id", "web-search"))
            state.web_started[tool_id] = time.monotonic()
            yield agent_events.ToolStartEvent(
                tool_call_id=tool_id,
                tool="web_search",
                label="Researching current recommendations…",
            )
        elif (
            event_type == "content_block_start"
            and block_type == "web_search_tool_result"
        ):
            tool_id = str(getattr(block, "tool_use_id", "web-search"))
            started = state.web_started.pop(tool_id, time.monotonic())
            duration_ms = (time.monotonic() - started) * 1000
            state.web_search_ms += duration_ms
            ok = _web_result_ok(getattr(block, "content", None))
            state.web_results.append(ok)
            yield agent_events.ToolEndEvent(
                tool_call_id=tool_id,
                tool="web_search",
                ok=ok,
                duration_ms=round(duration_ms),
                summary=(
                    "Current place information checked"
                    if ok
                    else "Current place search was unavailable"
                ),
            )
        elif event_type == "content_block_delta" and getattr(delta, "type", "") == (
            "text_delta"
        ):
            state.saw_text = True
            if state.first_token_ms is None:
                state.first_token_ms = (
                    time.monotonic() - state.call_started
                ) * 1000
            text = state.sanitizer.feed(str(getattr(delta, "text", "")))
            if text:
                yield agent_events.TokenEvent(text=text)
        elif event_type == "content_block_stop" and int(
            getattr(event, "index", -1)
        ) in state.text_indexes:
            state.text_indexes.discard(int(getattr(event, "index", -1)))
            text = state.sanitizer.flush()
            if text:
                yield agent_events.TokenEvent(text=text)


async def stream_model_call(
    *,
    client: Any,
    stream_kwargs: dict,
    log_tag: str,
    retry_count: int,
    sanitize_text: Callable[[str], str],
    deadline_monotonic: float | None = None,
    web_timeout_s: float | None = None,
) -> AsyncIterator[agent_events.AgentEvent | ModelCallCompleted]:
    attempts = max(1, int(retry_count) + 1)
    for attempt in range(1, attempts + 1):
        remaining_s = _remaining_deadline_s(deadline_monotonic)
        if remaining_s is not None and remaining_s <= 0:
            yield _deadline_completion(attempt - 1)
            return

        completed: _AttemptCompleted | None = None
        async for event in _stream_attempt(
            client=client,
            stream_kwargs=stream_kwargs,
            log_tag=log_tag,
            sanitize_text=sanitize_text,
            deadline_monotonic=deadline_monotonic,
            web_timeout_s=web_timeout_s,
            remaining_s=remaining_s,
            attempt=attempt,
            attempts=attempts,
        ):
            if isinstance(event, _AttemptCompleted):
                completed = event
            else:
                yield event
        if completed is None:
            raise RuntimeError("model attempt ended without a completion record")
        if completed.retry_mode == "immediate" and attempt < attempts:
            continue
        if completed.retry_mode == "backoff" and attempt < attempts:
            retry_delay = _retry_delay_s(attempt, deadline_monotonic)
            if retry_delay <= 0:
                yield _deadline_from_attempt(completed, attempt)
                return
            await asyncio.sleep(retry_delay)
            continue
        yield completed.public_result(attempt)
        return


async def _stream_attempt(
    *,
    client: Any,
    stream_kwargs: dict,
    log_tag: str,
    sanitize_text: Callable[[str], str],
    deadline_monotonic: float | None,
    web_timeout_s: float | None,
    remaining_s: float | None,
    attempt: int,
    attempts: int,
) -> AsyncIterator[agent_events.AgentEvent | _AttemptCompleted]:
    state = _AttemptState(
        sanitizer=_RiderTextSanitizer(sanitize_text),
        call_started=time.monotonic(),
    )
    pending_summary = "Current place search did not complete"
    record_duration = True
    try:
        async with client.messages.stream(**stream_kwargs) as stream:
            async for event in _stream_provider_events(
                stream,
                state,
                first_byte_s=_first_byte_timeout_s(remaining_s),
                deadline_monotonic=deadline_monotonic,
                web_timeout_s=web_timeout_s,
            ):
                yield event
            async with asyncio.timeout(_remaining_deadline_s(deadline_monotonic)):
                trailing = state.sanitizer.flush()
                if trailing:
                    yield agent_events.TokenEvent(text=trailing)
                final_message = await stream.get_final_message()
        completed = _AttemptCompleted(state=state, final_message=final_message, error=None)
    except WebResearchTimeoutError:
        pending_summary = "Current recommendations were unavailable"
        record_duration = False
        completed = _AttemptCompleted(
            state=state,
            final_message=None,
            error=None,
            web_timed_out=True,
        )
    except TimeoutError:
        trailing = state.sanitizer.flush()
        if trailing:
            yield agent_events.TokenEvent(text=trailing)
        pending_summary = "Current place search was interrupted"
        record_duration = False
        retry_mode: RetryMode = (
            "immediate"
            if _silent_retry_allowed(state, deadline_monotonic)
            else "none"
        )
        completed = _AttemptCompleted(
            state=state,
            final_message=None,
            error=_deadline_error(),
            retry_mode=retry_mode,
        )
    except (anthropic.APIError, OSError, RuntimeError, TypeError, ValueError) as exc:
        trailing = state.sanitizer.flush()
        if trailing:
            yield agent_events.TokenEvent(text=trailing)
        pending_summary = "Current place search was interrupted"
        model_request.log_provider_failure(
            exc=exc,
            kwargs=stream_kwargs,
            log_tag=log_tag,
            attempt=attempt,
            attempts=attempts,
        )
        retry_mode = (
            "backoff"
            if not state.saw_text and model_request.should_retry(exc)
            else "none"
        )
        completed = _AttemptCompleted(
            state=state,
            final_message=None,
            error=model_request.error_event_for(exc),
            retry_mode=retry_mode,
        )
    for event in _pending_web_events(
        state,
        summary=pending_summary,
        record_duration=record_duration,
    ):
        yield event
    yield completed


def _pending_web_events(
    state: _AttemptState,
    *,
    summary: str,
    record_duration: bool = False,
) -> list[agent_events.ToolEndEvent]:
    events: list[agent_events.ToolEndEvent] = []
    for tool_id, started in state.web_started.items():
        duration_ms = (time.monotonic() - started) * 1000
        if record_duration:
            state.web_search_ms += duration_ms
        events.append(
            agent_events.ToolEndEvent(
                tool_call_id=tool_id,
                tool="web_search",
                ok=False,
                duration_ms=round(duration_ms),
                summary=summary,
            )
        )
    return events


def _silent_retry_allowed(
    state: _AttemptState,
    deadline_monotonic: float | None,
) -> bool:
    deadline_open = deadline_monotonic is None or time.monotonic() < deadline_monotonic
    return not state.saw_provider_event and not state.saw_text and deadline_open


def _retry_delay_s(attempt: int, deadline_monotonic: float | None) -> float:
    delay = min(0.15 * attempt, 0.45)
    remaining_s = _remaining_deadline_s(deadline_monotonic)
    return delay if remaining_s is None or delay < remaining_s else 0.0


def _deadline_error() -> agent_events.ErrorEvent:
    return agent_events.ErrorEvent(
        code="deadline",
        message="The response took too long. Please try again.",
        retryable=True,
    )


def _deadline_completion(attempts: int) -> ModelCallCompleted:
    return ModelCallCompleted(
        final_message=None,
        error=_deadline_error(),
        attempts=attempts,
    )


def _deadline_from_attempt(
    completed: _AttemptCompleted,
    attempt: int,
) -> ModelCallCompleted:
    return dataclasses.replace(completed, error=_deadline_error()).public_result(attempt)
