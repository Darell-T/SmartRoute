"""Shared scripted fake for `anthropic.AsyncAnthropic`, used by the agent
Layer-1 tests (test_agent_loop.py, test_agent_tools.py). Not a test module
itself -- no `Test*`/`test_*` names, so pytest never collects it.

Usage: build a list of "round specs" (one per expected `.messages.stream()`
call) and pass them to `make_fake_anthropic_module(rounds=...)`, then patch
`sys.modules["anthropic"]` with the result before importing/reloading any
module that does `import anthropic`.

A round spec is a dict:
    {
        "text": ["chunk1", "chunk2", ...],       # streamed as token deltas
        "tool_use": [{"id": "...", "name": "...", "input": {...}}, ...],
        "stop_reason": "tool_use" | "end_turn" | "max_tokens",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
"""

from __future__ import annotations

import types


class FakeContentBlock:
    def __init__(self, type_: str, **kwargs):
        self.type = type_
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeUsage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 10):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeMessage:
    def __init__(self, content: list, stop_reason: str, usage: FakeUsage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class FakeStreamContext:
    def __init__(self, round_spec: dict):
        self._round_spec = round_spec or {}

    async def __aenter__(self):
        if self._round_spec.get("raise"):
            raise RuntimeError(self._round_spec.get("raise_message", "simulated upstream failure"))
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def _iter_text(self):
        for chunk in self._round_spec.get("text", []):
            yield chunk

    @property
    def text_stream(self):
        return self._iter_text()

    async def get_final_message(self) -> FakeMessage:
        blocks: list = []
        joined_text = "".join(self._round_spec.get("text", []))
        if joined_text:
            blocks.append(FakeContentBlock("text", text=joined_text))
        for tool_use in self._round_spec.get("tool_use", []):
            blocks.append(
                FakeContentBlock(
                    "tool_use",
                    id=tool_use["id"],
                    name=tool_use["name"],
                    input=tool_use.get("input", {}),
                )
            )
        usage = FakeUsage(**self._round_spec.get("usage", {}))
        stop_reason = self._round_spec.get("stop_reason", "end_turn")
        return FakeMessage(blocks, stop_reason, usage)


class FakeMessagesAPI:
    def __init__(self, rounds: list[dict]):
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        # Snapshot `messages` (and the list-of-blocks `system`) at call time --
        # the caller's loop keeps mutating the same list object across rounds
        # (appending assistant/tool_result turns), so without a shallow copy
        # here every recorded call would retroactively show the *final*
        # message list instead of what was actually sent for that round.
        recorded = dict(kwargs)
        if "messages" in recorded:
            recorded["messages"] = list(recorded["messages"])
        if "system" in recorded and isinstance(recorded["system"], list):
            recorded["system"] = list(recorded["system"])
        self.calls.append(recorded)
        round_spec = self._rounds.pop(0) if self._rounds else {"text": [], "stop_reason": "end_turn"}
        return FakeStreamContext(round_spec)


class FakeAsyncAnthropic:
    def __init__(self, rounds: list[dict] | None = None, api_key: str | None = None):
        self.api_key = api_key
        self.messages = FakeMessagesAPI(rounds or [])


def make_fake_anthropic_module(rounds: list[dict] | None = None, client_holder: list | None = None):
    """Builds a fake `anthropic` module. If `client_holder` is a list, the
    constructed FakeAsyncAnthropic client is appended to it, so the caller
    can inspect `.messages.calls` after the code under test runs."""
    fake_anthropic = types.ModuleType("anthropic")

    class _FakeAPIStatusError(Exception):
        status_code = 500

    def _async_anthropic(api_key: str | None = None) -> FakeAsyncAnthropic:
        client = FakeAsyncAnthropic(rounds=rounds, api_key=api_key)
        if client_holder is not None:
            client_holder.append(client)
        return client

    fake_anthropic.AsyncAnthropic = _async_anthropic
    fake_anthropic.APIStatusError = _FakeAPIStatusError
    return fake_anthropic
