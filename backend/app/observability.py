"""Fail-open, privacy-minimized telemetry.dev integration."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from collections.abc import Iterator, Mapping
from typing import Any

_SDK: Any = None
_MAX_TEXT = 80
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class _NoopSpan:
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        return None

    def end(self, **_fields: object) -> None:
        return None

    def traceparent(self) -> None:
        return None


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def initialize(*, span_exporter: object | None = None, api_key: str | None = None) -> None:
    global _SDK
    try:
        import telemetry_dev

        options: dict[str, Any] = {
            "api_key": api_key,
            "environment": _env("TELEMETRY_DEV_ENVIRONMENT", "development"),
            "service_name": _env("OTEL_SERVICE_NAME", "smartroute-backend"),
            "register_global": False,
            "capture_input": False,
            "capture_output": False,
            "disable_atexit": True,
        }
        if span_exporter is not None:
            options["span_exporter"] = span_exporter
            options["export_mode"] = "immediate"
        telemetry_dev.init(**options)
        _SDK = telemetry_dev
    except BaseException:
        _SDK = None


def shutdown() -> None:
    global _SDK
    sdk = _SDK
    _SDK = None
    if sdk is None:
        return
    try:
        sdk.flush(timeout_s=1.0)
    except BaseException:
        pass
    try:
        sdk.shutdown(timeout_s=1.0)
    except BaseException:
        pass


def _text(value: object, *, default: str = "unknown") -> str:
    return " ".join(str(value or "").split()).strip()[:_MAX_TEXT] or default


def _session_hash(session_id: object) -> str:
    return hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()[:16]


def _start(name: str, span_type: str, attributes: Mapping[str, object]) -> Any:
    sdk = _SDK
    if sdk is None:
        return _NoopSpan()
    try:
        return sdk.start_span(
            name,
            type=span_type,
            attributes=dict(attributes),
            capture_input=False,
            capture_output=False,
        )
    except BaseException:
        return _NoopSpan()


def wrap_anthropic(client: Any) -> Any:
    """Wrap only real SDK clients; deterministic fakes remain untouched."""

    sdk = _SDK
    if sdk is None or not getattr(sdk.get_client(), "enabled", False):
        return client
    try:
        import anthropic
        from anthropic.resources.messages import AsyncMessages
        from telemetry_dev_anthropic import wrap_anthropic as provider_wrap

        if not isinstance(client, anthropic.AsyncAnthropic):
            return client
        if not isinstance(getattr(client, "messages", None), AsyncMessages):
            return client
        return provider_wrap(client)
    except BaseException:
        return client


def _trace_id(span: Any) -> str | None:
    try:
        traceparent = span.traceparent()
    except BaseException:
        return None
    if not isinstance(traceparent, str):
        return None
    parts = traceparent.split("-")
    trace_id = parts[1] if len(parts) > 1 else ""
    return trace_id if _TRACE_ID_RE.fullmatch(trace_id) else None


def start_turn(ctx: Any, *, turn_id: str, mode: str) -> Any:
    span = _start(
        "smartroute.agent.turn",
        "agent",
        {
            "smartroute.turn_id": _text(turn_id),
            "smartroute.session_hash": _session_hash(getattr(ctx, "session_id", "")),
            "smartroute.mode": _text(mode),
        },
    )
    trace_id = _trace_id(span)
    if trace_id and isinstance(getattr(ctx, "telemetry", None), dict):
        ctx.telemetry["trace_id"] = trace_id
    return span


def _finish(span: Any, attributes: Mapping[str, object]) -> None:
    try:
        span.end(attributes=dict(attributes))
    except BaseException:
        pass


def finish_turn(span: Any, telemetry: Mapping[str, object]) -> None:
    attributes: dict[str, object] = {
        "smartroute.outcome": _text(telemetry.get("turn_resolution"), default="incomplete"),
        "smartroute.selection_source": _text(telemetry.get("selection_source"), default="none"),
    }
    goal_states = telemetry.get("goal_states")
    if isinstance(goal_states, Mapping):
        kinds = sorted(
            {
                str(value.get("kind"))[:_MAX_TEXT]
                for value in goal_states.values()
                if isinstance(value, Mapping) and value.get("kind")
            }
        )
        if kinds:
            attributes["smartroute.goal_kinds"] = kinds[:8]
    diagnostics = telemetry.get("route_candidate_diagnostics")
    if isinstance(diagnostics, Mapping):
        count = diagnostics.get("final_structurally_unique_candidate_count")
        if isinstance(count, int) and not isinstance(count, bool):
            attributes["smartroute.candidate_family_count"] = max(0, min(count, 100))
    _finish(span, attributes)


def start_tool(ctx: Any, name: str) -> Any:
    return _start(
        "smartroute.agent.tool",
        "tool",
        {
            "smartroute.turn_id": _text(getattr(ctx, "turn_id", "")),
            "smartroute.session_hash": _session_hash(getattr(ctx, "session_id", "")),
            "smartroute.capability": _text(name),
        },
    )


def finish_tool(span: Any, *, ok: bool, error: BaseException | None = None) -> None:
    attributes = {"smartroute.outcome": "ok" if ok else "error"}
    if error is not None:
        attributes["smartroute.error_class"] = type(error).__name__[:_MAX_TEXT]
    _finish(span, attributes)


@contextlib.contextmanager
def activate(span: Any) -> Iterator[Any]:
    """Activate a span without allowing SDK exception capture to leak payloads."""

    entered = False
    try:
        span.__enter__()
        entered = True
        yield span
    except BaseException as exc:
        _finish(
            span,
            {
                "smartroute.outcome": "error",
                "smartroute.error_class": type(exc).__name__[:_MAX_TEXT],
            },
        )
        raise
    finally:
        if entered:
            span.__exit__(None, None, None)
