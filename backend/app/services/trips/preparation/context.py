"""Neutral boundaries for the shared route-preparation pipeline.

Agent ``ToolContext`` and ``ToolResult`` stay at the capability boundary.  The
model-free planner uses these small values so direct REST planning can consume
the same computation without importing agent runtime state.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

ProgressSink = Callable[[str, str], Awaitable[None]]


@dataclasses.dataclass
class RoutePreparationContext:
    """Request-scoped values needed by neutral route preparation."""

    gtfs: Any = None
    session: dict | None = None
    session_id: str = ""
    turn_id: str = ""
    now_et: str = ""
    origin: dict | None = None
    telemetry: dict[str, Any] = dataclasses.field(default_factory=dict)
    progress_sink: ProgressSink | None = None

    async def emit_progress(self, stage: str, status: str) -> None:
        if self.progress_sink is not None:
            await self.progress_sink(stage, status)


@dataclasses.dataclass(frozen=True)
class RoutePreparationFailure:
    """Model-free failure that an agent adapter can turn into ``ToolResult``."""

    error: str
    ok: bool = False


def is_route_preparation_failure(value: object) -> bool:
    """Recognize neutral failures and adapter failures without importing agent."""

    if isinstance(value, RoutePreparationFailure):
        return True
    return getattr(value, "ok", True) is False and hasattr(value, "error")


__all__ = (
    "ProgressSink",
    "RoutePreparationContext",
    "RoutePreparationFailure",
    "is_route_preparation_failure",
)
