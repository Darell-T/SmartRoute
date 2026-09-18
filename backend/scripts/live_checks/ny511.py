"""Opt-in certification of one live 511NY snapshot refresh.

Run only after approval: ``python -m scripts.live_checks.ny511 --live``.
This is deliberately not a test and never runs in CI.  It emits aggregate,
sanitized diagnostics only; in particular it never prints the key, request URL,
or raw provider records.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from app.services.incidents.ny511 import (
    NY511Client,
    NY511Poller,
    NY511Settings,
    SnapshotStore,
)
from app.services.trips.route_incidents.context import (
    CandidateStopAssociation,
    CandidateStopContext,
)
from app.services.trips.route_incidents.matching import Cached511NYSearchTool


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one live 511NY normalization certification.")
    parser.add_argument("--live", action="store_true", help="Explicitly allow one upstream 511NY request.")
    return parser.parse_args()


def _probe_stops() -> list[CandidateStopContext]:
    """A fixed, non-user location only used to exercise local snapshot search."""

    return [
        CandidateStopContext(
            stop_id="validation-stop",
            stop_name="Validation probe",
            latitude=40.7128,
            longitude=-74.0060,
            associations=[CandidateStopAssociation(candidate_route_id="candidate-0", mode="bus")],
        )
    ]


async def certify(settings: NY511Settings | None = None) -> dict[str, Any]:
    """Fetch once, normalize into the production store, then search locally."""

    settings = settings or NY511Settings.from_env()
    if settings.fixture_path:
        return {"status": "skipped", "reason": "fixture mode is configured"}
    if not settings.enabled or not settings.api_key:
        return {"status": "skipped", "reason": "NY511_API_KEY is not configured"}

    client = NY511Client(settings, max_attempts=1)
    upstream_calls = 0
    fetch_events = client.fetch_events

    async def counted_fetch() -> list[Any]:
        nonlocal upstream_calls
        upstream_calls += 1
        return await fetch_events()

    client.fetch_events = counted_fetch  # type: ignore[method-assign]
    poller = NY511Poller(settings, client=client, store=SnapshotStore(settings))
    refreshed = await poller.refresh()
    snapshot = await poller.store.get_snapshot()

    # This is the same local candidate matcher used after a snapshot is read
    # during a route request.  It must not increment the upstream count.
    route_time_calls_before = upstream_calls
    local_result = Cached511NYSearchTool(lambda: snapshot, _probe_stops()).execute(
        {"candidate_route_ids": ["candidate-0"], "radius_miles": 0.5}
    )
    route_time_calls_after = upstream_calls

    return {
        "status": "passed" if refreshed and upstream_calls == 1 and route_time_calls_before == route_time_calls_after else "failed",
        "upstream_fetch_count": upstream_calls,
        "source_record_count": snapshot.source_record_count,
        "nyc_record_count": snapshot.nyc_record_count,
        "invalid_record_count": snapshot.invalid_record_count,
        "snapshot_status": snapshot.status,
        "snapshot_timestamp": snapshot.last_successful_fetch_at.isoformat() if snapshot.last_successful_fetch_at else None,
        "local_candidate_search_status": local_result.get("status"),
        "route_request_made_upstream_fetch": route_time_calls_after != route_time_calls_before,
    }


def main() -> int:
    args = _arguments()
    if not args.live:
        print("SKIPPED: pass --live to allow one sanitized 511NY upstream request")
        return 0
    result = asyncio.run(certify())
    safe_fields = (
        "status", "upstream_fetch_count", "source_record_count", "nyc_record_count",
        "invalid_record_count", "snapshot_status", "snapshot_timestamp",
        "local_candidate_search_status", "route_request_made_upstream_fetch",
    )
    fields = " ".join(f"{key}={result[key]}" for key in safe_fields if key in result)
    if result.get("reason") in {"fixture mode is configured", "NY511_API_KEY is not configured"}:
        fields = f"{fields} reason={result['reason']}"
    print(f"511NY live certification: {fields}")
    return 0 if result["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
