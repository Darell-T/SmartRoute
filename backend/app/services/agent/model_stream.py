"""Immediate model-round streaming with bounded server-tool progress."""

from __future__ import annotations

import asyncio
import dataclasses
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.services.agent import events as agent_events
from app.services.agent import model_request

_SUSPICIOUS_TEXT = re.compile(
    r"[*_`~]|\bcard\s*$|\b(?:rc|mock)[_-]",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class ModelCallCompleted:
    final_message: object | None
    error: agent_events.ErrorEvent | None
    attempts: int
    web_search_ms: float = 0.0
    server_tool_call_count: int = 0


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


def _web_result_ok(content: object) -> bool:
    if isinstance(content, list):
        return all(getattr(item, "type", "") != "web_search_tool_result_error" for item in content)
    return getattr(content, "type", "") != "web_search_tool_result_error"


async def stream_model_call(
    *,
    client: Any,
    stream_kwargs: dict,
    log_tag: str,
    retry_count: int,
    sanitize_text: Callable[[str], str],
    deadline_monotonic: float | None = None,
) -> AsyncIterator[agent_events.AgentEvent | ModelCallCompleted]:
    attempts = max(1, int(retry_count) + 1)
    for attempt in range(1, attempts + 1):
        remaining_s = (
            None
            if deadline_monotonic is None
            else deadline_monotonic - time.monotonic()
        )
        if remaining_s is not None and remaining_s <= 0:
            yield ModelCallCompleted(
                final_message=None,
                error=agent_events.ErrorEvent(
                    code="deadline",
                    message="The response took too long. Please try again.",
                    retryable=True,
                ),
                attempts=attempt - 1,
            )
            return
        sanitizer = _RiderTextSanitizer(sanitize_text)
        saw_text = False
        text_indexes: set[int] = set()
        web_started: dict[str, float] = {}
        web_search_ms = 0.0
        server_tool_calls = 0
        try:
            async with asyncio.timeout(remaining_s):
                async with client.messages.stream(**stream_kwargs) as stream:
                    if hasattr(stream, "__aiter__"):
                        async for event in stream:
                            event_type = getattr(event, "type", "")
                            if event_type == "content_block_start":
                                block = getattr(event, "content_block", None)
                                block_type = getattr(block, "type", "")
                                if block_type == "text":
                                    text_indexes.add(int(getattr(event, "index", -1)))
                                elif block_type == "server_tool_use" and getattr(
                                    block, "name", ""
                                ) == "web_search":
                                    server_tool_calls += 1
                                    tool_id = str(getattr(block, "id", "web-search"))
                                    web_started[tool_id] = time.monotonic()
                                    yield agent_events.ToolStartEvent(
                                        tool_call_id=tool_id,
                                        tool="web_search",
                                        label="Searching current NYC place information...",
                                    )
                                elif block_type == "web_search_tool_result":
                                    tool_id = str(
                                        getattr(block, "tool_use_id", "web-search")
                                    )
                                    started = web_started.pop(tool_id, time.monotonic())
                                    duration_ms = (time.monotonic() - started) * 1000
                                    web_search_ms += duration_ms
                                    ok = _web_result_ok(getattr(block, "content", None))
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
                            elif event_type == "content_block_delta":
                                delta = getattr(event, "delta", None)
                                if getattr(delta, "type", "") == "text_delta":
                                    saw_text = True
                                    text = sanitizer.feed(str(getattr(delta, "text", "")))
                                    if text:
                                        yield agent_events.TokenEvent(text=text)
                            elif event_type == "content_block_stop":
                                index = int(getattr(event, "index", -1))
                                if index in text_indexes:
                                    text_indexes.discard(index)
                                    text = sanitizer.flush()
                                    if text:
                                        yield agent_events.TokenEvent(text=text)
                    else:
                        async for delta in stream.text_stream:
                            saw_text = True
                            text = sanitizer.feed(str(delta))
                            if text:
                                yield agent_events.TokenEvent(text=text)

                    trailing = sanitizer.flush()
                    if trailing:
                        yield agent_events.TokenEvent(text=trailing)
                    final_message = await stream.get_final_message()

            for tool_id, started in web_started.items():
                duration_ms = (time.monotonic() - started) * 1000
                web_search_ms += duration_ms
                yield agent_events.ToolEndEvent(
                    tool_call_id=tool_id,
                    tool="web_search",
                    ok=False,
                    duration_ms=round(duration_ms),
                    summary="Current place search did not complete",
                )
            yield ModelCallCompleted(
                final_message=final_message,
                error=None,
                attempts=attempt,
                web_search_ms=web_search_ms,
                server_tool_call_count=server_tool_calls,
            )
            return
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            trailing = sanitizer.flush()
            if trailing:
                yield agent_events.TokenEvent(text=trailing)
            for tool_id, started in web_started.items():
                yield agent_events.ToolEndEvent(
                    tool_call_id=tool_id,
                    tool="web_search",
                    ok=False,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    summary="Current place search was interrupted",
                )
            yield ModelCallCompleted(
                final_message=None,
                error=agent_events.ErrorEvent(
                    code="deadline",
                    message="The response took too long. Please try again.",
                    retryable=True,
                ),
                attempts=attempt,
                web_search_ms=web_search_ms,
                server_tool_call_count=server_tool_calls,
            )
            return
        except Exception as exc:
            trailing = sanitizer.flush()
            if trailing:
                saw_text = True
                yield agent_events.TokenEvent(text=trailing)
            for tool_id, started in web_started.items():
                duration_ms = (time.monotonic() - started) * 1000
                web_search_ms += duration_ms
                yield agent_events.ToolEndEvent(
                    tool_call_id=tool_id,
                    tool="web_search",
                    ok=False,
                    duration_ms=round(duration_ms),
                    summary="Current place search was interrupted",
                )
            print(
                model_request.format_failure_log(
                    exc=exc,
                    kwargs=stream_kwargs,
                    log_tag=log_tag,
                    attempt=attempt,
                    attempts=attempts,
                )
            )
            if saw_text or attempt >= attempts or not model_request.should_retry(exc):
                yield ModelCallCompleted(
                    final_message=None,
                    error=model_request.error_event_for(exc),
                    attempts=attempt,
                    web_search_ms=web_search_ms,
                    server_tool_call_count=server_tool_calls,
                )
                return
            retry_delay = min(0.15 * attempt, 0.45)
            if deadline_monotonic is not None:
                retry_delay = min(retry_delay, max(0.0, deadline_monotonic - time.monotonic()))
            if retry_delay <= 0:
                yield ModelCallCompleted(
                    final_message=None,
                    error=agent_events.ErrorEvent(
                        code="deadline",
                        message="The response took too long. Please try again.",
                        retryable=True,
                    ),
                    attempts=attempt,
                    web_search_ms=web_search_ms,
                    server_tool_call_count=server_tool_calls,
                )
                return
            await asyncio.sleep(retry_delay)
    raise AssertionError("model retry loop exited unexpectedly")
