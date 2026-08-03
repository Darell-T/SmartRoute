"""Shared types for agent tool executors.

Leaf module: no imports from sibling `agent.tools` modules, so both
`tools/__init__.py` (the registry) and each tool module can depend on it
without a circular import.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.agent.events import ProgressStage, ProgressStatus


@dataclasses.dataclass
class ToolContext:
    """Per-turn context threaded into every tool executor."""

    gtfs: Any = None
    session: dict | None = None
    turn_id: str = ""
    now_et: str = ""
    origin: dict | None = None
    telemetry: dict[str, Any] = dataclasses.field(default_factory=dict)
    # The agent loop owns these values. Keeping them turn-scoped prevents the
    # route tool from falling back to the REST advisor's pinned model.
    agent_mode: str = ""
    agent_model: str = ""
    agent_explanation_style: str = ""
    progress_sink: Callable[[ProgressStage, ProgressStatus], Awaitable[None]] | None = None

    async def emit_progress(self, stage: ProgressStage, status: ProgressStatus) -> None:
        if self.progress_sink is not None:
            await self.progress_sink(stage, status)


@dataclasses.dataclass
class ToolResult:
    """What a tool executor hands back to the loop.

    `data` is the compact, model-facing digest (wrapped by the loop as
    `{"source": tool, "data": data, "untrusted": true}` before it goes back
    to the model -- never raw route geometry). `events` are SSE events to
    stream immediately (e.g. `route_card`). `session_route_cards` are the
    compact card records persisted into the session for future-turn
    `<context>` digests.
    """

    ok: bool
    data: Any = None
    summary: str = ""
    error: str | None = None
    events: list = dataclasses.field(default_factory=list)
    session_route_cards: list = dataclasses.field(default_factory=list)
    timings: dict[str, float] = dataclasses.field(default_factory=dict)
