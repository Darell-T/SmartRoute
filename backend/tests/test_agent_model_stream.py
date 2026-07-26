from __future__ import annotations

import asyncio
import types
import unittest

from app.services.agent import events
from app.services.agent import model_stream


def _event(event_type: str, **values):
    return types.SimpleNamespace(type=event_type, **values)


class _RawStream:
    def __init__(
        self,
        *,
        release: asyncio.Event,
        fail_after_first: bool = False,
        web_result_content: list[object] | None = None,
    ):
        self._release = release
        self._fail_after_first = fail_after_first
        self._web_result_content = web_result_content or []

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
            raise RuntimeError("simulated stream failure")
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
            content=[],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=3, output_tokens=5),
        )


class _Messages:
    def __init__(self, stream):
        self._stream = stream

    def stream(self, **_kwargs):
        return self._stream


class ModelStreamTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertIsInstance(first, events.TokenEvent)
        self.assertEqual(first.text, "Useful context first. ")

        release.set()
        remaining = [item async for item in stream]
        self.assertEqual(
            [
                item.type
                for item in remaining
                if not isinstance(item, model_stream.ModelCallCompleted)
            ],
            ["tool_start", "tool_end", "token"],
        )
        self.assertEqual(
            next(
                item.text
                for item in remaining
                if isinstance(item, events.TokenEvent)
            ),
            "Grounded answer.",
        )
        outcome = next(
            item
            for item in remaining
            if isinstance(item, model_stream.ModelCallCompleted)
        )
        self.assertEqual(outcome.server_tool_call_count, 1)
        self.assertIsNone(outcome.error)

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
        self.assertFalse(progress.ok)
        self.assertEqual(progress.summary, "Current place search was unavailable")

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
        self.assertEqual(tokens, ["Useful context first. "])
        outcome = next(
            item
            for item in items
            if isinstance(item, model_stream.ModelCallCompleted)
        )
        self.assertIsNotNone(outcome.error)
        self.assertEqual(outcome.attempts, 1)
