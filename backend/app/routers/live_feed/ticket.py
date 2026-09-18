"""WebSocket ticket verification for the live-feed transport."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from typing import Any

_TICKET_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
_TICKET_SIGNATURE = re.compile(r"[0-9a-f]{64}")


def _ticket_parts(ticket: str) -> tuple[str, str, str, str] | None:
    parts = ticket.split(".")
    if len(parts) != 4:
        return None
    exp_str, nonce, principal_id, signature = parts
    if (
        not exp_str
        or len(exp_str) > 12
        or not exp_str.isdigit()
        or not nonce
        or not _TICKET_TOKEN.fullmatch(nonce)
        or not _TICKET_TOKEN.fullmatch(principal_id)
        or not _TICKET_SIGNATURE.fullmatch(signature)
    ):
        return None
    return exp_str, nonce, principal_id, signature


def _ticket_expiry(
    exp_str: str, now: Callable[[], float]
) -> tuple[int, int] | None:
    try:
        expires_at = int(exp_str)
    except ValueError:
        return None
    current_time = int(now())
    if expires_at < current_time or expires_at > current_time + 120:
        return None
    return expires_at, current_time


def _ticket_signature_matches(
    app_key: str,
    exp_str: str,
    path: str,
    nonce: str,
    principal: str,
    signature: str,
) -> bool:
    expected = hmac.new(
        app_key.encode(),
        f"{exp_str}.{path}.{nonce}.{principal}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


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
    parsed = _ticket_parts(ticket)
    if parsed is None:
        return None, False
    exp_str, nonce, principal_id, signature = parsed
    principal = f"v1.{principal_id}"
    expiry = _ticket_expiry(exp_str, now)
    if expiry is None:
        return None, False
    expires_at, current_time = expiry
    if not _ticket_signature_matches(
        app_key, exp_str, path, nonce, principal, signature
    ):
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
