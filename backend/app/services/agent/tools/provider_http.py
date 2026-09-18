"""Shared single-request JSON fetch for tools that hit one REST endpoint.

Google Places, Ticketmaster, and the MTA ENE feed use this boundary. Every
branch returns `(payload, None)` on success
or `(None, short_reason)` on failure -- nothing here ever raises. Callers
wrap the reason into whatever return shape their own executor needs
(`ToolResult(ok=False, error=...)` or a bare `None`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

import httpx

_LOGGER = logging.getLogger(__name__)


async def fetch_json(
    method: str,
    url: str,
    *,
    timeout_s: float,
    log_tag: str,
    what: str,
    params: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    on_response: Callable[[int, Mapping[str, str]], None] | None = None,
) -> tuple[dict | list | None, str | None]:
    """GET or POST `url` and return its parsed JSON body. `log_tag` (for
    example, ``agent-place-search``) prefixes the diagnostic line on failure;
    `what` (e.g. "place search") names the operation in both that line and
    the returned rider-facing reason string."""
    kwargs: dict = {}
    if params is not None:
        kwargs["params"] = params
    if headers is not None:
        kwargs["headers"] = headers
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            if method == "GET":
                response = await client.get(url, **kwargs)
            else:
                response = await client.post(url, json=json_body, **kwargs)
            if on_response is not None:
                on_response(response.status_code, response.headers)
            response.raise_for_status()
            return response.json(), None
    except httpx.TimeoutException:
        _LOGGER.warning("[%s] %s timed out", log_tag, what)
        return None, f"{what} timed out"
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        _LOGGER.warning("[%s] %s HTTP %s", log_tag, what, status)
        return None, _http_status_reason(status, what)
    except httpx.RequestError as exc:
        _LOGGER.warning(
            "[%s] %s request failed: %s",
            log_tag,
            what,
            type(exc).__name__,
        )
        return None, f"{what} failed"
    except (ValueError, TypeError) as exc:
        _LOGGER.warning("[%s] %s invalid JSON: %r", log_tag, what, exc)
        return None, f"{what} returned an unexpected response"


def _http_status_reason(status: int, what: str) -> str:
    if status in {401, 403}:
        return f"{what} authentication failed"
    if status == 429:
        return f"{what} rate limited"
    if status in {400, 404, 422}:
        return f"{what} request was invalid"
    if status >= 500:
        return f"{what} is temporarily unavailable"
    return f"{what} failed"
