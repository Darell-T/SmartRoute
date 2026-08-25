"""Opt-in, sanitized Grok web/X crowd-search certification."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one live Grok web/X crowd-search certification."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly allow one bounded Grok request.",
    )
    return parser.parse_args()


async def certify() -> dict[str, Any]:
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "XAI_API_KEY is not configured"}
    if os.getenv("GROK_CROWD_SEARCH_ENABLED", "1").strip().casefold() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return {"status": "skipped", "reason": "Grok crowd search is disabled"}

    # Import after environment setup because the production provider constructs
    # its bounded SDK client from server-side configuration at module import.
    from app.services.trips.crowds import search as crowd_search
    from app.services.trips.crowds.hotspots import HotspotHit

    travel_at = datetime.now().astimezone()
    hit = HotspotHit(
        route_index=0,
        hotspot_key="columbus_lincoln",
        hotspot_name="Columbus Circle and Lincoln Center",
        station_name="59 St-Columbus Circle",
        latitude=40.7681,
        longitude=-73.9819,
        expected_at=travel_at,
        route_id="A",
    )
    try:
        result = await asyncio.wait_for(
            crowd_search._run_search(
                {hit.hotspot_key: hit},
                travel_at,
            ),
            timeout=7.0,
        )
    except Exception:
        return {"status": "failed", "reason": "Grok request or normalization failed"}

    provider_status = str(result.get("status") or "unavailable")
    events = [event for event in result.get("events") or [] if isinstance(event, dict)]
    source_classes = sorted(
        {
            str(event.get("source_class"))
            for event in events
            if event.get("source_class")
        }
    )
    return {
        "status": "passed" if provider_status == "complete" else "failed",
        "provider_status": provider_status,
        "normalized_event_count": len(events),
        "completed_sources": sorted(result.get("completed_sources") or []),
        "source_classes": source_classes,
        "failure_phase": result.get("failure_phase"),
        "error_type": result.get("error_type"),
        "status_code": result.get("status_code"),
        "grpc_code": result.get("grpc_code"),
    }


def main() -> int:
    args = _arguments()
    if not args.live:
        print("SKIPPED: pass --live to allow one sanitized Grok request")
        return 0
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    result = asyncio.run(certify())
    safe_fields = (
        "status",
        "provider_status",
        "normalized_event_count",
        "completed_sources",
        "source_classes",
        "failure_phase",
        "error_type",
        "status_code",
        "grpc_code",
    )
    output = " ".join(
        f"{key}={result[key]}" for key in safe_fields if key in result
    )
    if result.get("reason") in {
        "XAI_API_KEY is not configured",
        "Grok crowd search is disabled",
        "Grok request or normalization failed",
    }:
        output += f" reason={result['reason']}"
    print("Grok crowd-search certification: " + output)
    return 0 if result["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
