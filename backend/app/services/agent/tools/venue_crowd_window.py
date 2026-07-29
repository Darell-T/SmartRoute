"""venue_crowd_window tool: pure static lookup, no network. Turns confirmed
event start/end timing into conservative pre-event and post-event subway crowd
windows, plus which stations/lines are affected and a plain-language alternate
for "avoid the crowd" requests. Always a heuristic derived from
venues.VENUE_CROWD_TABLE, never a live crowd measurement.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services.agent import venues
from app.services.agent.tools._types import ToolContext, ToolResult

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
                "enum": sorted(venues.VENUE_CROWD_TABLE.keys()),
                "description": "Venue key, typically from a prior event_lookup result's venue_key.",
            },
            "event_end_iso": {
                "type": "string",
                "description": "RFC3339 event end time, e.g. event_lookup's estimated_end_iso.",
            },
            "event_start_iso": {
                "type": "string",
                "description": "Confirmed RFC3339 event start time from event_lookup, if available.",
            },
            "event_status": {
                "type": "string",
                "description": "Ticketmaster event status from event_lookup; do not request a window for unsettled events.",
            },
            "start_time_status": {
                "type": "string",
                "description": "Ticketmaster start_time_status from event_lookup; must be confirmed when supplied.",
            },
        },
        "required": ["venue", "event_end_iso"],
        "additionalProperties": False,
    },
}

_UNSAFE_EVENT_STATUSES = {"canceled", "cancelled", "postponed", "rescheduled"}
_UNSAFE_START_TIME_STATUSES = {"date_tba", "date_tbd", "time_tba", "no_specific_time", "date_only", "unknown"}


def _parse_timestamp(value: object, field_name: str) -> tuple[datetime | None, ToolResult | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, ToolResult(ok=False, error=f"{field_name} is required")
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, ToolResult(ok=False, error=f"{field_name} is not a valid RFC3339 timestamp")
    if timestamp.tzinfo is None:
        return None, ToolResult(ok=False, error=f"{field_name} must include a UTC offset")
    return timestamp, None


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    venue_key = str(tool_input.get("venue") or "").strip().lower()
    row = venues.VENUE_CROWD_TABLE.get(venue_key)
    if row is None:
        return ToolResult(ok=False, error=f"unknown venue '{venue_key}'")

    event_status = str(tool_input.get("event_status") or "").strip().lower()
    start_time_status = str(tool_input.get("start_time_status") or "").strip().lower()
    if event_status in _UNSAFE_EVENT_STATUSES or start_time_status in _UNSAFE_START_TIME_STATUSES:
        return ToolResult(ok=False, error="event timing is not confirmed for a crowd window")

    event_end, end_error = _parse_timestamp(tool_input.get("event_end_iso"), "event_end_iso")
    if end_error is not None:
        return end_error
    assert event_end is not None

    surge_start = event_end + timedelta(minutes=venues.SURGE_START_OFFSET_MIN)
    surge_end = event_end + timedelta(minutes=venues.SURGE_END_OFFSET_MIN)
    pre_event_start = None
    pre_event_end = None
    if tool_input.get("event_start_iso"):
        event_start, start_error = _parse_timestamp(tool_input.get("event_start_iso"), "event_start_iso")
        if start_error is not None:
            return start_error
        assert event_start is not None
        pre_event_start = event_start + timedelta(minutes=venues.PRE_EVENT_START_OFFSET_MIN)
        pre_event_end = event_start + timedelta(minutes=venues.PRE_EVENT_END_OFFSET_MIN)

    data = {
        "venue": venue_key,
        "stations": list(row["stations"]),
        "lines": list(row["lines"]),
        "surge_start_iso": surge_start.isoformat(),
        "surge_end_iso": surge_end.isoformat(),
        "pre_event_start_iso": pre_event_start.isoformat() if pre_event_start else None,
        "pre_event_end_iso": pre_event_end.isoformat() if pre_event_end else None,
        "alternates": row["alternates"],
        "note": row.get("note") or "",
        "is_heuristic": True,
    }
    summary = (
        f"post-event crowd surge near {venue_key} ~{surge_start.strftime('%H:%M')}-"
        f"{surge_end.strftime('%H:%M')} (heuristic)"
    )
    return ToolResult(ok=True, data=data, summary=summary)
