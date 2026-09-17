"""Hidden-agent adapter for neutral venue crowd-window facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.services.agent.tools.base import ToolResult
from app.services.trips.crowds import event_provider

PRE_EVENT_START_OFFSET_MIN = -60
PRE_EVENT_END_OFFSET_MIN = 15
SURGE_START_OFFSET_MIN = -15
SURGE_END_OFFSET_MIN = 50

# Event normalization and duration heuristics remain owned by the trips provider.
VENUE_CROWD_TABLE = event_provider.VENUE_CROWD_TABLE

VENUE_CROWD_WINDOW_SCHEMA = {
    "name": "venue_crowd_window",
    "description": (
        "Estimate conservative pre-event and post-event subway crowd windows "
        "for a major NYC venue using confirmed event start and estimated end "
        "times. This is a static heuristic, not a live crowd measurement."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "venue": {
                "type": "string",
                "enum": sorted(VENUE_CROWD_TABLE),
                "description": "Venue key returned by current event evidence.",
            },
            "event_end_iso": {
                "type": "string",
                "description": "RFC3339 estimated event end time.",
            },
            "event_start_iso": {
                "type": "string",
                "description": "Confirmed RFC3339 event start time, when available.",
            },
            "event_status": {
                "type": "string",
                "description": "Current event status; unsettled events cannot produce a window.",
            },
            "start_time_status": {
                "type": "string",
                "description": "Start-time status; it must be confirmed when supplied.",
            },
        },
        "required": ["venue", "event_end_iso"],
        "additionalProperties": False,
    },
}

_UNSAFE_EVENT_STATUSES = {"canceled", "cancelled", "postponed", "rescheduled"}
_UNSAFE_START_TIME_STATUSES = {
    "date_tba",
    "date_tbd",
    "time_tba",
    "no_specific_time",
    "date_only",
    "unknown",
}


@dataclass(frozen=True)
class _VenueWindowAdmission:
    venue_key: str
    row: dict[str, Any]
    event_end: datetime
    event_start: datetime | None


def _parse_timestamp(
    value: object, field_name: str
) -> tuple[datetime | None, ToolResult | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, ToolResult(ok=False, error=f"{field_name} is required")
    try:
        timestamp = datetime.fromisoformat(raw)
    except ValueError:
        return None, ToolResult(
            ok=False,
            error=f"{field_name} is not a valid RFC3339 timestamp",
        )
    if timestamp.tzinfo is None:
        return None, ToolResult(
            ok=False,
            error=f"{field_name} must include a UTC offset",
        )
    return timestamp, None


def _event_timing_unconfirmed(tool_input: dict) -> bool:
    event_status = str(tool_input.get("event_status") or "").strip().lower()
    start_time_status = str(tool_input.get("start_time_status") or "").strip().lower()
    return (
        event_status in _UNSAFE_EVENT_STATUSES
        or start_time_status in _UNSAFE_START_TIME_STATUSES
    )


def _admit_venue_window(tool_input: dict) -> _VenueWindowAdmission | ToolResult:
    venue_key = str(tool_input.get("venue") or "").strip().lower()
    row = VENUE_CROWD_TABLE.get(venue_key)
    if row is None:
        return ToolResult(ok=False, error=f"unknown venue '{venue_key}'")
    if _event_timing_unconfirmed(tool_input):
        return ToolResult(
            ok=False,
            error="event timing is not confirmed for a crowd window",
        )
    event_end, end_error = _parse_timestamp(
        tool_input.get("event_end_iso"), "event_end_iso"
    )
    if event_end is None:
        return end_error or ToolResult(ok=False, error="event_end_iso is required")
    event_start = None
    if tool_input.get("event_start_iso"):
        event_start, start_error = _parse_timestamp(
            tool_input.get("event_start_iso"), "event_start_iso"
        )
        if event_start is None:
            return start_error or ToolResult(
                ok=False, error="event_start_iso is not a valid RFC3339 timestamp"
            )
    return _VenueWindowAdmission(venue_key, row, event_end, event_start)


def _crowd_window_result(admitted: _VenueWindowAdmission) -> ToolResult:
    surge_start = admitted.event_end + timedelta(minutes=SURGE_START_OFFSET_MIN)
    surge_end = admitted.event_end + timedelta(minutes=SURGE_END_OFFSET_MIN)
    pre_start = (
        admitted.event_start + timedelta(minutes=PRE_EVENT_START_OFFSET_MIN)
        if admitted.event_start is not None
        else None
    )
    pre_end = (
        admitted.event_start + timedelta(minutes=PRE_EVENT_END_OFFSET_MIN)
        if admitted.event_start is not None
        else None
    )
    data = {
        "venue": admitted.venue_key,
        "stations": list(admitted.row["stations"]),
        "lines": list(admitted.row["lines"]),
        "surge_start_iso": surge_start.isoformat(),
        "surge_end_iso": surge_end.isoformat(),
        "pre_event_start_iso": pre_start.isoformat() if pre_start else None,
        "pre_event_end_iso": pre_end.isoformat() if pre_end else None,
        "alternates": admitted.row["alternates"],
        "note": admitted.row.get("note") or "",
        "is_heuristic": True,
    }
    summary = (
        f"post-event crowd surge near {admitted.venue_key} ~{surge_start.strftime('%H:%M')}-"
        f"{surge_end.strftime('%H:%M')} (heuristic)"
    )
    return ToolResult(ok=True, data=data, summary=summary)


async def execute(tool_input: dict, ctx: Any):
    """Project confirmed event timing into conservative venue crowd windows."""

    del ctx
    admitted = _admit_venue_window(tool_input)
    if isinstance(admitted, ToolResult):
        return admitted
    return _crowd_window_result(admitted)
