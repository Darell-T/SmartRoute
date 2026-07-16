"""Shared single-request JSON fetch for tools that hit one REST endpoint
(poi_search: Places, event_lookup: Ticketmaster, accessibility_status: the
MTA ENE feed). Fail-open: every branch returns `(payload, None)` on success
or `(None, short_reason)` on failure -- nothing here ever raises. Callers
wrap the reason into whatever return shape their own executor needs
(`ToolResult(ok=False, error=...)` or a bare `None`).
"""

from __future__ import annotations

import httpx


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
) -> tuple[dict | list | None, str | None]:
    """GET or POST `url` and return its parsed JSON body. `log_tag` (e.g.
    "agent-poi_search") prefixes the diagnostic line logged on failure;
    `what` (e.g. "place search") names the operation in both that line and
    the returned rider-facing reason string."""
    # Only pass through kwargs each call actually uses -- matches each tool's
    # original client.get/post call shape exactly (e.g. event_lookup never
    # sent headers, accessibility_status sends neither params nor headers).
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
            response.raise_for_status()
            return response.json(), None
    except httpx.TimeoutException:
        print(f"[{log_tag}] {what} timed out")
        return None, f"{what} timed out"
    except httpx.HTTPStatusError as exc:
        print(f"[{log_tag}] {what} HTTP {exc.response.status_code}")
        return None, f"{what} failed"
    except httpx.RequestError as exc:
        print(f"[{log_tag}] {what} request failed: {type(exc).__name__}")
        return None, f"{what} failed"
    except (ValueError, TypeError) as exc:
        print(f"[{log_tag}] {what} invalid JSON: {exc!r}")
        return None, f"{what} returned an unexpected response"
