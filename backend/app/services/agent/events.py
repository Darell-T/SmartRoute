"""SSE event types streamed to the frontend by the conversational agent.

Each event is a small frozen dataclass with a fixed `type` and a `to_data()`
method producing the JSON-serializable payload. `sse_format()` renders any of
them to a standard SSE frame (`event: <type>\\ndata: <one-line-json>\\n\\n`).

Order contract (enforced by loop.py, not this module): `meta` first, `done`
always last -- even after an `error`.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Union


@dataclasses.dataclass(frozen=True)
class MetaEvent:
    session_id: str
    turn_id: str
    type: str = "meta"

    def to_data(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "turn_id": self.turn_id}


@dataclasses.dataclass(frozen=True)
class TokenEvent:
    text: str
    type: str = "token"

    def to_data(self) -> dict[str, Any]:
        return {"text": self.text}


@dataclasses.dataclass(frozen=True)
class ToolStartEvent:
    tool_call_id: str
    tool: str
    label: str
    type: str = "tool_start"

    def to_data(self) -> dict[str, Any]:
        return {"tool_call_id": self.tool_call_id, "tool": self.tool, "label": self.label}


@dataclasses.dataclass(frozen=True)
class ToolEndEvent:
    tool_call_id: str
    tool: str
    ok: bool
    duration_ms: int
    summary: str | None = None
    type: str = "tool_end"

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "tool": self.tool,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
        }
        if self.summary is not None:
            data["summary"] = self.summary
        return data


@dataclasses.dataclass(frozen=True)
class RouteCardEvent:
    card_id: str
    turn_id: str
    role: str  # "recommended" | "alternative"
    origin: dict
    destination: dict
    summary: dict
    route: list
    alerts: list
    leg_label: str | None = None
    depart_iso: str | None = None
    # Canonical seconds-based itinerary (Task 2+). Optional for back-compat
    # with mocks / older session digests; plan_trip always populates it.
    itinerary: dict | None = None
    selection_decision: dict | None = None
    type: str = "route_card"

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "card_id": self.card_id,
            "turn_id": self.turn_id,
            "role": self.role,
            "origin": self.origin,
            "destination": self.destination,
            "summary": self.summary,
            "route": self.route,
            "alerts": self.alerts,
        }
        if self.leg_label is not None:
            data["leg_label"] = self.leg_label
        if self.depart_iso is not None:
            data["depart_iso"] = self.depart_iso
        if self.itinerary is not None:
            data["itinerary"] = self.itinerary
        if self.selection_decision is not None:
            data["selection_decision"] = self.selection_decision
        return data


@dataclasses.dataclass(frozen=True)
class ArrivalCardEvent:
    turn_id: str
    route_id: str
    stop: dict
    directions: list
    updated_at: str
    source_status: str
    resolution_status: str
    catchability: dict | None = None
    ambiguity: list | None = None
    evidence: dict | None = None
    type: str = "arrival_card"

    @classmethod
    def from_lookup(cls, turn_id: str, payload: dict) -> "ArrivalCardEvent":
        source_status = str(payload.get("source_status") or "provider_unavailable")
        ambiguity = payload.get("ambiguity")
        if source_status == "stop_not_resolved":
            resolution_status = "ambiguous" if ambiguity else "location_required"
        elif source_status == "provider_unavailable":
            resolution_status = "provider_unavailable"
        elif source_status == "no_predictions":
            resolution_status = "no_predictions"
        else:
            resolution_status = "resolved"
        return cls(
            turn_id=turn_id,
            route_id=str(payload.get("route_id") or ""),
            stop=dict(payload.get("stop") or {}),
            directions=list(payload.get("directions") or []),
            updated_at=str(payload.get("updated_at") or ""),
            source_status=source_status,
            resolution_status=resolution_status,
            catchability=payload.get("catchability"),
            ambiguity=ambiguity,
            evidence=payload.get("evidence"),
        )

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "turn_id": self.turn_id,
            "route_id": self.route_id,
            "stop": self.stop,
            "directions": self.directions,
            "updated_at": self.updated_at,
            "source_status": self.source_status,
            "resolution_status": self.resolution_status,
        }
        if self.catchability is not None:
            data["catchability"] = self.catchability
        if self.ambiguity is not None:
            data["ambiguity"] = self.ambiguity
        if self.evidence is not None:
            data["evidence"] = self.evidence
        return data


@dataclasses.dataclass(frozen=True)
class ErrorEvent:
    code: str  # rate_limited|budget_exceeded|session_expired|invalid_request|provider_configuration|upstream_error|deadline|internal
    message: str
    retryable: bool
    type: str = "error"

    def to_data(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclasses.dataclass(frozen=True)
class DoneEvent:
    session_id: str
    turn_id: str
    stop_reason: str  # end_turn|clarification_required|max_rounds|deadline|error
    usage: dict
    type: str = "done"

    @property
    def terminal_state(self) -> str:
        if self.stop_reason == "clarification_required":
            return "clarification_required"
        if self.stop_reason == "end_turn":
            return "completed"
        return "failed"

    def to_data(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "stop_reason": self.stop_reason,
            "terminal_state": self.terminal_state,
            "usage": self.usage,
        }


AgentEvent = Union[
    MetaEvent,
    TokenEvent,
    ToolStartEvent,
    ToolEndEvent,
    RouteCardEvent,
    ArrivalCardEvent,
    ErrorEvent,
    DoneEvent,
]


def sse_format(event: AgentEvent) -> str:
    payload = json.dumps(event.to_data(), separators=(",", ":"), default=str)
    return f"event: {event.type}\ndata: {payload}\n\n"
