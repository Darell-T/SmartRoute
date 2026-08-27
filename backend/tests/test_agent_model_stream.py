from __future__ import annotations

import asyncio
import time
import types
import unittest
from unittest import mock

import pytest
from app.services.agent import events
from app.services.agent.model import stream as model_stream
from app.services.agent.turn import stream as turn_stream


class SimulatedStreamFailureError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("simulated stream failure")


class DeadlineShouldCancelError(AssertionError):
    def __init__(self) -> None:
        super().__init__("deadline should cancel before final message")


def _event(event_type: str, **values):
    return types.SimpleNamespace(type=event_type, **values)


class _RawStream:
    def __init__(
        self,
        *,
        release: asyncio.Event,
        fail_after_first: bool = False,
        web_result_content: list[object] | None = None,
        final_content: list[object] | None = None,
    ):
        self._release = release
        self._fail_after_first = fail_after_first
        self._web_result_content = web_result_content or []
        self._final_content = final_content or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def __aiter__(self):
        yield _event(
            "content_block_start",
            index=0,
            content_block=_event("text"),
        )
        yield _event(
            "content_block_delta",
            index=0,
            delta=_event("text_delta", text="Useful context first. "),
        )
        if self._fail_after_first:
            raise SimulatedStreamFailureError()
        await self._release.wait()
        yield _event("content_block_stop", index=0)
        yield _event(
            "content_block_start",
            index=1,
            content_block=_event(
                "server_tool_use",
                id="web-1",
                name="web_search",
            ),
        )
        yield _event(
            "content_block_start",
            index=2,
            content_block=_event(
                "web_search_tool_result",
                tool_use_id="web-1",
                content=self._web_result_content,
            ),
        )
        yield _event(
            "content_block_start",
            index=3,
            content_block=_event("text"),
        )
        yield _event(
            "content_block_delta",
            index=3,
            delta=_event("text_delta", text="Grounded answer."),
        )
        yield _event("content_block_stop", index=3)

    async def get_final_message(self):
        return types.SimpleNamespace(
            content=self._final_content,
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=3, output_tokens=5),
        )


class _Messages:
    def __init__(self, stream):
        self._stream = stream

    def stream(self, **_kwargs):
        return self._stream


class _SequenceMessages:
    def __init__(self, streams):
        self._streams = list(streams)
        self.calls = 0

    def stream(self, **_kwargs):
        stream = self._streams[self.calls]
        self.calls += 1
        return stream


class _NoFirstByteStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def __aiter__(self):
        await asyncio.Event().wait()
        yield None

    async def get_final_message(self):
        raise DeadlineShouldCancelError()


class _WebStallStream(_NoFirstByteStream):
    async def __aiter__(self):
        yield _event("content_block_start", index=0, content_block=_event("server_tool_use", id="web-1", name="web_search"))
        await asyncio.Event().wait()


class _RetryableFailureStream:
    async def __aenter__(self):
        error = RuntimeError("retryable")
        error.status_code = 503
        raise error

    async def __aexit__(self, *_args):
        return False


class _AckThenSlowCompletionStream:
    """First token immediately, then a pause longer than the first-byte timeout."""

    def __init__(self, pause_s: float):
        self._pause_s = pause_s

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def __aiter__(self):
        yield _event("content_block_start", index=0, content_block=_event("text"))
        yield _event(
            "content_block_delta",
            index=0,
            delta=_event("text_delta", text="I'll route you to Sottocasa. "),
        )
        await asyncio.sleep(self._pause_s)
        yield _event("content_block_stop", index=0)
        yield _event("content_block_start", index=1, content_block=_event("text"))
        yield _event(
            "content_block_delta",
            index=1,
            delta=_event("text_delta", text="Comparing live routes now."),
        )
        yield _event("content_block_stop", index=1)

    async def get_final_message(self):
        return types.SimpleNamespace(
            content=[],
            stop_reason="tool_use",
            usage=types.SimpleNamespace(input_tokens=8, output_tokens=12),
        )


class ModelStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_that_acked_may_finish_after_first_byte_timeout(self):
        messages = _Messages(_AckThenSlowCompletionStream(pause_s=0.08))
        with mock.patch.object(model_stream, "MODEL_ATTEMPT_TIMEOUT_S", 0.03):
            items = [
                item
                async for item in model_stream.stream_model_call(
                    client=types.SimpleNamespace(messages=messages),
                    stream_kwargs={},
                    log_tag="test",
                    retry_count=0,
                    sanitize_text=lambda value: value,
                    deadline_monotonic=time.monotonic() + 0.4,
                )
            ]
        tokens = [
            item.text for item in items if isinstance(item, events.TokenEvent)
        ]
        outcome = next(
            item for item in items if isinstance(item, model_stream.ModelCallCompleted)
        )
        assert outcome.error is None
        assert "I'll route you to Sottocasa. " in tokens
        assert "Comparing live routes now." in tokens

    async def test_silent_attempt_timeout_retries_within_turn(self):
        release = asyncio.Event()
        release.set()
        messages = _SequenceMessages(
            [_NoFirstByteStream(), _RawStream(release=release)]
        )
        with mock.patch.object(model_stream, "MODEL_ATTEMPT_TIMEOUT_S", 0.01):
            items = [
                item
                async for item in model_stream.stream_model_call(
                    client=types.SimpleNamespace(messages=messages),
                    stream_kwargs={},
                    log_tag="test",
                    retry_count=1,
                    sanitize_text=lambda value: value,
                    deadline_monotonic=time.monotonic() + 0.2,
                )
            ]
        outcome = next(
            item for item in items if isinstance(item, model_stream.ModelCallCompleted)
        )
        assert outcome.error is None
        assert outcome.attempts == 2
        assert messages.calls == 2

    async def test_timeout_after_streamed_text_does_not_retry(self):
        release = asyncio.Event()
        messages = _SequenceMessages(
            [_RawStream(release=release), _RawStream(release=release)]
        )
        with mock.patch.object(model_stream, "MODEL_ATTEMPT_TIMEOUT_S", 0.01):
            items = [
                item
                async for item in model_stream.stream_model_call(
                    client=types.SimpleNamespace(messages=messages),
                    stream_kwargs={},
                    log_tag="test",
                    retry_count=1,
                    sanitize_text=lambda value: value,
                    deadline_monotonic=time.monotonic() + 0.2,
                )
            ]
        outcome = next(
            item for item in items if isinstance(item, model_stream.ModelCallCompleted)
        )
        assert outcome.error.code == "deadline"
        assert outcome.attempts == 1
        assert messages.calls == 1

    async def test_deadline_before_first_byte_is_typed_and_bounded(self):
        client = types.SimpleNamespace(messages=_Messages(_NoFirstByteStream()))
        started = time.monotonic()
        items = [
            item
            async for item in model_stream.stream_model_call(
                client=client,
                stream_kwargs={},
                log_tag="test",
                retry_count=0,
                sanitize_text=lambda value: value,
                deadline_monotonic=time.monotonic() + 0.001,
            )
        ]
        assert time.monotonic() - started < 0.25
        outcome = next(item for item in items if isinstance(item, model_stream.ModelCallCompleted))
        assert outcome.error.code == "deadline"

    async def test_web_search_progress_is_balanced_when_deadline_cancels_stream(self):
        client = types.SimpleNamespace(messages=_Messages(_WebStallStream()))
        items = [item async for item in model_stream.stream_model_call(client=client, stream_kwargs={}, log_tag="test", retry_count=0, sanitize_text=lambda value: value, deadline_monotonic=time.monotonic() + 0.01)]
        progress = [item for item in items if isinstance(item, events.ToolEndEvent)]
        assert len(progress) == 1
        assert not progress[0].ok
        assert next(item for item in items if isinstance(item, model_stream.ModelCallCompleted)).error.code == "deadline"

    async def test_cancellation_propagates_without_synthetic_terminal_event(self):
        client = types.SimpleNamespace(messages=_Messages(_NoFirstByteStream()))
        stream = model_stream.stream_model_call(client=client, stream_kwargs={}, log_tag="test", retry_count=0, sanitize_text=lambda value: value)
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_retry_backoff_is_cut_off_by_turn_deadline(self):
        class Messages:
            def __init__(self): self.calls = 0
            def stream(self, **_kwargs):
                self.calls += 1
                return _RetryableFailureStream()
        messages = Messages()
        started = time.monotonic()
        items = [item async for item in model_stream.stream_model_call(client=types.SimpleNamespace(messages=messages), stream_kwargs={}, log_tag="test", retry_count=2, sanitize_text=lambda value: value, deadline_monotonic=time.monotonic() + 0.01)]
        assert time.monotonic() - started < 0.25
        outcome = next(item for item in items if isinstance(item, model_stream.ModelCallCompleted))
        assert outcome.error.code == "deadline"
        assert outcome.attempts == 1
        assert messages.calls == 1

    async def test_deadline_cancels_a_stalled_stream_with_typed_outcome(self):
        release = asyncio.Event()
        client = types.SimpleNamespace(messages=_Messages(_RawStream(release=release)))
        started = time.monotonic()
        items = [
            item
            async for item in model_stream.stream_model_call(
                client=client,
                stream_kwargs={},
                log_tag="test",
                retry_count=1,
                sanitize_text=lambda value: value,
                deadline_monotonic=time.monotonic() + 0.02,
            )
        ]
        assert time.monotonic() - started < 0.25
        outcome = next(item for item in items if isinstance(item, model_stream.ModelCallCompleted))
        assert outcome.error.code == "deadline"
        assert outcome.attempts == 1

    async def test_text_arrives_before_round_completion_and_resumes_after_web_search(self):
        release = asyncio.Event()
        client = types.SimpleNamespace(
            messages=_Messages(_RawStream(release=release))
        )
        stream = model_stream.stream_model_call(
            client=client,
            stream_kwargs={},
            log_tag="test",
            retry_count=0,
            sanitize_text=lambda value: value,
        )

        first = await anext(stream)
        assert isinstance(first, events.TokenEvent)
        assert first.text == "Useful context first. "

        release.set()
        remaining = [item async for item in stream]
        assert [item.type for item in remaining if not isinstance(item, model_stream.ModelCallCompleted)] == ["tool_start", "tool_end", "token"]
        assert next(item.text for item in remaining if isinstance(item, events.TokenEvent)) == "Grounded answer."
        outcome = next(
            item
            for item in remaining
            if isinstance(item, model_stream.ModelCallCompleted)
        )
        assert outcome.server_tool_call_count == 1
        assert outcome.error is None

    async def test_web_search_result_error_marks_progress_failed(self):
        release = asyncio.Event()
        release.set()
        client = types.SimpleNamespace(
            messages=_Messages(
                _RawStream(
                    release=release,
                    web_result_content=[
                        _event(
                            "web_search_tool_result_error",
                            error_code="unavailable",
                        )
                    ],
                )
            )
        )

        items = [
            item
            async for item in model_stream.stream_model_call(
                client=client,
                stream_kwargs={},
                log_tag="test",
                retry_count=0,
                sanitize_text=lambda value: value,
            )
        ]

        progress = next(item for item in items if isinstance(item, events.ToolEndEvent))
        assert not progress.ok
        assert progress.summary == "Current place search was unavailable"

    async def test_web_search_citations_become_safe_deduplicated_sources(self):
        release = asyncio.Event()
        release.set()
        client = types.SimpleNamespace(
            messages=_Messages(
                _RawStream(
                    release=release,
                    final_content=[
                        _event(
                            "text",
                            text="Grounded answer.",
                            citations=[
                                _event(
                                    "web_search_result_location",
                                    title="Current venue details",
                                    url="https://EXAMPLE.com/venue#hours",
                                ),
                                {
                                    "type": "web_search_result_location",
                                    "title": "Duplicate",
                                    "url": "https://example.com/venue#different",
                                },
                                _event(
                                    "web_search_result_location",
                                    title="Unsafe",
                                    url="http://example.com/venue",
                                ),
                                _event(
                                    "char_location",
                                    title="Not a web citation",
                                    url="https://ignored.example/",
                                ),
                            ],
                        )
                    ],
                )
            )
        )

        items = [
            item
            async for item in model_stream.stream_model_call(
                client=client,
                stream_kwargs={},
                log_tag="test",
                retry_count=0,
                sanitize_text=lambda value: value,
            )
        ]

        outcome = next(
            item for item in items if isinstance(item, model_stream.ModelCallCompleted)
        )
        assert outcome.web_sources == (
            {
                "title": "Current venue details",
                "url": "https://example.com/venue",
            },
        )

    async def test_turn_stream_emits_web_citations_for_the_active_turn(self):
        citation = {
            "title": "Current venue details",
            "url": "https://example.com/venue",
        }

        async def completed_model_call(**_kwargs):
            yield model_stream.ModelCallCompleted(
                final_message=types.SimpleNamespace(content=[]),
                error=None,
                attempts=1,
                web_sources=(citation,),
            )

        state = types.SimpleNamespace(
            dependencies=types.SimpleNamespace(
                client=object(),
                sanitize_rider_text=lambda value: value,
            ),
            mode_policy=types.SimpleNamespace(
                retry_count=0,
                web_research_timeout_s=None,
            ),
            deadline_monotonic=None,
            tool_failures=0,
            turn_id="turn-1",
        )
        capture = turn_stream._StreamCapture()
        with mock.patch.object(
            model_stream,
            "stream_model_call",
            completed_model_call,
        ):
            items = [
                item
                async for item in turn_stream._capture_model_events(
                    state,
                    {},
                    time.monotonic(),
                    capture,
                )
            ]

        assert capture.outcome is not None
        assert items == [
            events.SourcesEvent(turn_id="turn-1", sources=(citation,))
        ]

    async def test_partial_text_is_preserved_when_the_stream_fails(self):
        release = asyncio.Event()
        client = types.SimpleNamespace(
            messages=_Messages(
                _RawStream(release=release, fail_after_first=True)
            )
        )
        items = [
            item
            async for item in model_stream.stream_model_call(
                client=client,
                stream_kwargs={},
                log_tag="test",
                retry_count=2,
                sanitize_text=lambda value: value,
            )
        ]

        tokens = [item.text for item in items if isinstance(item, events.TokenEvent)]
        assert tokens == ["Useful context first. "]
        outcome = next(
            item
            for item in items
            if isinstance(item, model_stream.ModelCallCompleted)
        )
        assert outcome.error is not None
        assert outcome.attempts == 1
