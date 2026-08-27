"""Anthropic request construction, diagnostics, and retry classification."""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.agent import events as agent_events
from app.services.agent.model import policy as agent_policy

_LOGGER = logging.getLogger(__name__)

_SAMPLING_FIELDS = ("temperature", "top_p", "top_k")
_NON_RETRYABLE_STATUSES = {400, 401, 402, 403, 404}
_TRANSIENT_EXCEPTION_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "OverloadedError",
    "RateLimitError",
}
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_SECRET = re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]+|[A-Fa-f0-9]{32,})\b")
_COORDINATE_PAIR = re.compile(r"(?<!\d)-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}(?!\d)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_QUOTED_VALUE = re.compile(r"""(["'])(.*?)\1""")
_SAFE_QUOTED_IDENTIFIERS = {
    "assistant",
    "budget_tokens",
    "content",
    "enabled",
    "max_tokens",
    "messages",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_k",
    "top_p",
    "type",
    "user",
}


@dataclasses.dataclass(frozen=True)
class ProviderErrorDetails:
    status_code: int | None
    error_type: str
    message: str
    request_id: str


def build_stream_kwargs(
    *,
    messages: list[dict],
    system_blocks: list[dict],
    mode_policy: agent_policy.AgentModePolicy,
    tools: Sequence[dict],
    request_options: Mapping[str, Any] | None = None,
    allow_server_tool_continuation: bool = False,
) -> dict[str, Any]:
    """Build final SDK kwargs and remove fields unsupported by the model."""

    capabilities = agent_policy.request_capabilities(mode_policy.model)
    kwargs: dict[str, Any] = {
        "model": mode_policy.model,
        "messages": messages,
        "system": system_blocks,
        **dict(request_options or {}),
    }
    if not capabilities.supports_manual_thinking:
        thinking = kwargs.get("thinking")
        if isinstance(thinking, Mapping) and thinking.get("type") == "enabled":
            kwargs.pop("thinking", None)
    if not capabilities.supports_non_default_sampling:
        for field in _SAMPLING_FIELDS:
            kwargs.pop(field, None)
    if capabilities.supports_effort:
        kwargs["output_config"] = {"effort": mode_policy.output_effort}

    if (
        not capabilities.supports_assistant_prefill
        and not allow_server_tool_continuation
        and messages
        and messages[-1].get("role") == "assistant"
    ):
        raise ValueError("configured model does not support assistant prefill")

    kwargs["max_tokens"] = mode_policy.max_output_tokens
    kwargs["tools"] = list(tools)
    if tools and "tool_choice" not in kwargs:
        kwargs["tool_choice"] = {"type": "any"}
    return kwargs


def request_diagnostics(kwargs: Mapping[str, Any]) -> str:
    """Return bounded request-shape telemetry without prompt or tool content."""

    tools = kwargs.get("tools")
    tool_count = len(tools) if isinstance(tools, Sequence) else 0
    sampling = ",".join(field for field in _SAMPLING_FIELDS if field in kwargs) or "none"
    return (
        f"model={agent_policy.safe_model_label(str(kwargs.get('model') or ''))} "
        f"tools_supplied={int('tools' in kwargs)} tool_count={tool_count} "
        f"thinking_supplied={int('thinking' in kwargs)} sampling_fields={sampling} "
        f"effort={(kwargs.get('output_config') or {}).get('effort') or 'default'!s} "
        f"max_tokens={int(kwargs.get('max_tokens') or 0)}"
    )


def provider_error_details(exc: Exception) -> ProviderErrorDetails:
    status = getattr(exc, "status_code", None)
    status_code = int(status) if isinstance(status, int) else None
    request_id = _safe_label(getattr(exc, "request_id", None), fallback="none")
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, Mapping) else None
    error_type = (
        error.get("type")
        if isinstance(error, Mapping)
        else body.get("type") if isinstance(body, Mapping) else None
    )
    message = error.get("message") if isinstance(error, Mapping) else None
    return ProviderErrorDetails(
        status_code=status_code,
        error_type=_safe_label(error_type, fallback=type(exc).__name__),
        message=_sanitize_message(message),
        request_id=request_id,
    )


def should_retry(exc: Exception) -> bool:
    details = provider_error_details(exc)
    if details.status_code in _NON_RETRYABLE_STATUSES:
        return False
    if details.status_code == 429 or (
        details.status_code is not None and 500 <= details.status_code <= 599
    ):
        return True
    return type(exc).__name__ in _TRANSIENT_EXCEPTION_NAMES


def error_event_for(exc: Exception) -> agent_events.ErrorEvent:
    status = provider_error_details(exc).status_code
    if status == 400:
        return agent_events.ErrorEvent(
            code="invalid_request",
            message="SmartRoute could not complete that request. Please try again.",
            retryable=False,
        )
    if status in {401, 402, 403, 404}:
        return agent_events.ErrorEvent(
            code="provider_configuration",
            message="Live trip planning is unavailable right now.",
            retryable=False,
        )
    if status == 429:
        return agent_events.ErrorEvent(
            code="rate_limited",
            message="Live trip planning is busy. Please try again shortly.",
            retryable=True,
        )
    return agent_events.ErrorEvent(
        code="upstream_error",
        message="Live trip planning is temporarily unavailable.",
        retryable=True,
    )


def format_failure_log(
    *,
    exc: Exception,
    kwargs: Mapping[str, Any],
    log_tag: str,
    attempt: int,
    attempts: int,
) -> str:
    details = provider_error_details(exc)
    status = details.status_code if details.status_code is not None else "none"
    return (
        f"[agent-loop] {log_tag} failed status={status} "
        f"error_type={details.error_type} message={json.dumps(details.message)} "
        f"request_id={details.request_id} {request_diagnostics(kwargs)} "
        f"attempt={attempt}/{attempts}"
    )


def log_provider_failure(
    *,
    exc: Exception,
    kwargs: Mapping[str, Any],
    log_tag: str,
    attempt: int,
    attempts: int,
) -> None:
    """Record one sanitized provider failure at the request boundary."""

    _LOGGER.warning(
        "%s",
        format_failure_log(
            exc=exc,
            kwargs=kwargs,
            log_tag=log_tag,
            attempt=attempt,
            attempts=attempts,
        ),
    )


def _sanitize_message(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unavailable"
    sanitized = _CONTROL.sub(" ", value)
    sanitized = _URL.sub("[url]", sanitized)
    sanitized = _SECRET.sub("[secret]", sanitized)
    sanitized = _COORDINATE_PAIR.sub("[coordinates]", sanitized)
    sanitized = _QUOTED_VALUE.sub(_sanitize_quoted_value, sanitized)
    return " ".join(sanitized.split())[:320] or "unavailable"


def _sanitize_quoted_value(match: re.Match[str]) -> str:
    quote, value = match.groups()
    if value in _SAFE_QUOTED_IDENTIFIERS:
        return f"{quote}{value}{quote}"
    return "[redacted]"


def _safe_label(value: object, *, fallback: str) -> str:
    label = "".join(
        char for char in str(value or "") if char.isalnum() or char in "._-"
    )[:128]
    return label or fallback
