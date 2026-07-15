"""venue_crowd_window tool: pure static lookup, no network. Turns an event's
(estimated) end time into a post-event subway crowd surge window, plus which
stations/lines are affected and a plain-language alternate -- for "avoid the
crowd" requests. Always a heuristic derived from venues.VENUE_CROWD_TABLE,
never a live crowd measurement (`is_heuristic: true` on every result).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services.agent import venues
from app.services.agent.tools._types import ToolContext, ToolResult

VENUE_CROWD_WINDOW_SCHEMA = {
    "name": "venue_crowd_window",
    "description": (
        "Estimate the post-event subway crowd surge window and affected "
        "stations/lines for a major NYC venue, given the event's estimated "
        "end time. This is a static heuristic, not a live crowd measurement "
        "-- call event_lookup first to get the end time."
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
        },
        "required": ["venue", "event_end_iso"],
        "additionalProperties": False,
    },
}


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    venue_key = str(tool_input.get("venue") or "").strip().lower()
    row = venues.VENUE_CROWD_TABLE.get(venue_key)
    if row is None:
        return ToolResult(ok=False, error=f"unknown venue '{venue_key}'")

    event_end_raw = str(tool_input.get("event_end_iso") or "").strip()
    if not event_end_raw:
        return ToolResult(ok=False, error="event_end_iso is required")
    try:
        event_end = datetime.fromisoformat(event_end_raw.replace("Z", "+00:00"))
    except ValueError:
        return ToolResult(ok=False, error="event_end_iso is not a valid RFC3339 timestamp")
    if event_end.tzinfo is None:
        return ToolResult(ok=False, error="event_end_iso must include a UTC offset")

    surge_start = event_end + timedelta(minutes=venues.SURGE_START_OFFSET_MIN)
    surge_end = event_end + timedelta(minutes=venues.SURGE_END_OFFSET_MIN)

    data = {
        "venue": venue_key,
        "stations": list(row["stations"]),
        "lines": list(row["lines"]),
        "surge_start_iso": surge_start.isoformat(),
        "surge_end_iso": surge_end.isoformat(),
        "alternates": row["alternates"],
        "note": row.get("note") or "",
        "is_heuristic": True,
    }
    summary = (
        f"post-event crowd surge near {venue_key} ~{surge_start.strftime('%H:%M')}-"
        f"{surge_end.strftime('%H:%M')} (heuristic)"
    )
    return ToolResult(ok=True, data=data, summary=summary)
