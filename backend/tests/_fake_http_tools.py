"""Shared scaffolding for the agent tool tests (test_agent_tools*.py): a
`ToolContext` factory and a fake httpx client used to mock each P1/P2 tool's
single external GET/POST call without a live network request. The shared
HTTP boundary is patched through the
`app.services.agent.tools.provider_http.fetch_json` helper every tool's fetch now
goes through (patch `_http.httpx.AsyncClient`, not the calling tool's own
`httpx` attribute).
"""

from __future__ import annotations

from typing import ClassVar

from app.services.agent.tools._types import ToolContext

DEFAULT_NOW_ET = "2026-07-15T21:00:00-04:00"


def make_tool_ctx(origin: dict | None = None, *, gtfs=None, session: dict | None = None) -> ToolContext:
    return ToolContext(gtfs=gtfs, session=session if session is not None else {}, turn_id="t1", now_et=DEFAULT_NOW_ET, origin=origin)


class FakeHttpResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("bad status", request=object(), response=self)  # type: ignore[arg-type]

    def json(self):
        return self._payload


def recording_get_client(payload, status_code: int = 200):
    """A fake `httpx.AsyncClient` whose `get()` records every call (for
    request-shape assertions) and returns `payload`/`status_code`."""

    class _Client:
        requests: ClassVar[list[dict]] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, params=None):
            type(self).requests.append({"url": url, "params": params})
            return FakeHttpResponse(payload, status_code)

    _Client.requests = []
    return _Client


def recording_post_client(payload, status_code: int = 200):
    """Same as `recording_get_client`, for tools that POST (poi_search)."""

    class _Client:
        requests: ClassVar[list[dict]] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json=None, headers=None):
            type(self).requests.append({"url": url, "json": json, "headers": headers})
            return FakeHttpResponse(payload, status_code)

    _Client.requests = []
    return _Client
