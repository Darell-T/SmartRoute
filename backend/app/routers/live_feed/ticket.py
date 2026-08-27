"""WebSocket ticket verification for the live-feed transport."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from typing import Any


async def verify_ticket(
    ticket: str,
    path: str,
    *,
    app_key: str,
    now: Callable[[], float],
    admission: Any,
) -> tuple[str | None, bool]:
    """Validate and atomically consume a short-lived, path-bound ticket."""
    if not app_key or not ticket or not path or len(ticket) > 512:
        return None, False
    parts = ticket.split(".")
    if len(parts) != 4:
        return None, False
    exp_str, nonce, principal_id, signature = parts
    principal = f"v1.{principal_id}"
    if (
        not exp_str
        or len(exp_str) > 12
        or not exp_str.isdigit()
        or not nonce
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce)
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", principal_id)
        or not re.fullmatch(r"[0-9a-f]{64}", signature)
    ):
        return None, False
    try:
        expires_at = int(exp_str)
    except ValueError:
        return None, False
    current_time = int(now())
    if expires_at < current_time or expires_at > current_time + 120:
        return None, False
    expected = hmac.new(
        app_key.encode(),
        f"{exp_str}.{path}.{nonce}.{principal}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None, False
    try:
        admission.principal_from_request(principal)
    except admission.AdmissionDenied:
        return None, False
    nonce_result = await admission.consume_nonce(nonce, expires_at - current_time)
    if nonce_result == "unavailable":
        return None, True
    if nonce_result != "consumed":
        return None, False
    return principal, False
