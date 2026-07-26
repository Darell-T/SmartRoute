"""Opt-in sanitized replay of crowd planning and arrival follow-up."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay the Columbus Circle crowd and arrival scenario."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow live route, transit, crowd, and model providers.",
    )
    return parser.parse_args()


def _stream_events(client: Any, payload: dict[str, Any], app_key: str) -> list[dict]:
    events: list[dict] = []
    event_type = ""
    with client.stream(
        "POST",
        "/api/agent/chat",
        headers={"X-App-Key": app_key},
        json=payload,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
                events.append({"type": event_type, "data": data})
    return events


def _event_data(events: list[dict], event_type: str) -> list[dict]:
    return [
        event["data"]
        for event in events
        if event.get("type") == event_type and isinstance(event.get("data"), dict)
    ]


def main() -> int:
    args = _arguments()
    if not args.live:
        print("SKIPPED: pass --live to allow the sanitized end-to-end replay")
        return 0
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    app_key = os.getenv("APP_KEY", "").strip()
    if not app_key:
        print("Agent replay: status=skipped reason=APP_KEY_not_configured")
        return 0

    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import agent_chat

    agent_chat.AGENT_ALLOW_MEMORY_SESSIONS = True
    with TestClient(app) as client:
        crowd_started = time.monotonic()
        crowd_events = _stream_events(
            client,
            {
                "message": (
                    "I want to head to Columbus Circle later and want to avoid "
                    "crowds on both the street and subway."
                ),
                "origin": {"lat": 40.6505, "lng": -73.9628},
                "response_presentation": "auto",
            },
            app_key,
        )
        crowd_ms = round((time.monotonic() - crowd_started) * 1000)
        metadata = _event_data(crowd_events, "meta")
        session_id = str(metadata[0].get("session_id") or "") if metadata else ""
        crowd_done = _event_data(crowd_events, "done")
        crowd_errors = _event_data(crowd_events, "error")
        route_cards = _event_data(crowd_events, "route_card")

        arrival_started = time.monotonic()
        arrival_events = _stream_events(
            client,
            {
                "session_id": session_id,
                "message": "when is the next arrival",
                "origin": {"lat": 40.6505, "lng": -73.9628},
                "response_presentation": "auto",
            },
            app_key,
        )
        arrival_ms = round((time.monotonic() - arrival_started) * 1000)

    arrival_cards = _event_data(arrival_events, "arrival_card")
    arrival_tools = [
        data.get("tool")
        for data in _event_data(arrival_events, "tool_start")
    ]
    arrival_done = _event_data(arrival_events, "done")
    arrival_errors = _event_data(arrival_events, "error")
    route_id = str(arrival_cards[0].get("route_id") or "") if arrival_cards else ""
    stop_name = ""
    if arrival_cards:
        stop = arrival_cards[0].get("stop")
        if isinstance(stop, dict):
            stop_name = str(stop.get("name") or "")

    passed = bool(
        session_id
        and route_cards
        and crowd_done
        and not crowd_errors
        and arrival_cards
        and arrival_done
        and not arrival_errors
        and arrival_tools == ["lookup_arrivals"]
    )
    print(
        "Agent replay: "
        f"status={'passed' if passed else 'failed'} "
        f"crowd_ms={crowd_ms} route_cards={len(route_cards)} "
        f"crowd_errors={len(crowd_errors)} arrival_ms={arrival_ms} "
        f"arrival_tools={arrival_tools} arrival_cards={len(arrival_cards)} "
        f"arrival_route={route_id or 'none'} arrival_stop={stop_name or 'none'} "
        f"arrival_errors={len(arrival_errors)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
