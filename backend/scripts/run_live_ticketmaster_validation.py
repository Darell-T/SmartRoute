"""Opt-in, one-request Ticketmaster Discovery v2 certification."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.services.agent.tools import event_lookup
from app.services.agent.tools import venue_crowd_window
from app.services.agent.tools._types import ToolContext


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one live Ticketmaster normalization certification.")
    parser.add_argument("--live", action="store_true", help="Explicitly allow one Ticketmaster request.")
    return parser.parse_args()


async def certify() -> dict[str, Any]:
    """Use the production lookup parser while limiting this process to one page."""

    api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "TICKETMASTER_API_KEY is not configured"}
    if os.getenv("TICKETMASTER_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return {"status": "skipped", "reason": "Ticketmaster lookup is disabled"}

    # _lookup_uncached is the production HTTP + parsing path.  Limit only this
    # opt-in process to one page so certification is exactly one bounded NYC
    # request; restore the module constant before returning.
    old_max_pages = event_lookup.EVENT_LOOKUP_MAX_PAGES
    event_lookup.EVENT_LOOKUP_MAX_PAGES = 1
    try:
        try:
            result = await event_lookup._lookup_uncached(
                "New York", None, None, api_key, event_lookup.EVENT_LOOKUP_DEFAULT_RADIUS_MILES
            )
        except Exception:
            return {"status": "failed", "reason": "Ticketmaster request or normalization failed"}
    finally:
        event_lookup.EVENT_LOOKUP_MAX_PAGES = old_max_pages

    if not result.ok:
        return {"status": "failed", "reason": "Ticketmaster request or normalization failed"}
    events = result.data.get("events", []) if isinstance(result.data, dict) else []
    normalized_with_coordinates = sum(
        1
        for event in events
        if isinstance(event, dict)
        and event.get("venue_latitude") is not None
        and event.get("venue_longitude") is not None
    )
    confirmed_times = sum(
        1 for event in events if isinstance(event, dict) and event.get("start_time_status") == "confirmed"
    )
    crowd_windows_constructed = 0
    for event in events:
        if not isinstance(event, dict) or not event.get("venue_key") or not event.get("estimated_end_iso"):
            continue
        window = await venue_crowd_window.execute(
            {
                "venue": event["venue_key"],
                "event_end_iso": event["estimated_end_iso"],
                "event_start_iso": event.get("start_iso"),
                "event_status": event.get("status"),
                "start_time_status": event.get("start_time_status"),
            },
            ToolContext(),
        )
        crowd_windows_constructed += int(window.ok)
    return {
        "status": "passed",
        "normalized_event_count": len(events),
        "events_with_venue_coordinates": normalized_with_coordinates,
        "events_with_confirmed_time": confirmed_times,
        "crowd_windows_constructed": crowd_windows_constructed,
    }


def main() -> int:
    args = _arguments()
    if not args.live:
        print("SKIPPED: pass --live to allow one sanitized Ticketmaster request")
        return 0
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    result = asyncio.run(certify())
    safe_fields = (
        "status", "normalized_event_count", "events_with_venue_coordinates",
        "events_with_confirmed_time", "crowd_windows_constructed",
    )
    print("Ticketmaster live certification: " + " ".join(
        f"{key}={result[key]}" for key in safe_fields if key in result
    ) + (
        f" reason={result['reason']}"
        if result.get("reason") in {
            "TICKETMASTER_API_KEY is not configured",
            "Ticketmaster lookup is disabled",
            "Ticketmaster request or normalization failed",
        }
        else ""
    ))
    return 0 if result["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
