"""Production cron entrypoint for one background incident refresh cycle.

This process runs separately from the web service (Render cron service rooted
at ``backend``) and writes the SHARED incident index, so a shared Redis cache
is required: an ephemeral in-memory fallback in this process would be
invisible to the web service. The CLI fails fast when REDIS_URL is missing.

Output is bounded and payload-free. This script never prints connection
details, keys, incident content, or exception messages.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Mapping

# ``python scripts/run_incident_refresh.py`` puts ``scripts/`` on sys.path,
# not the backend root; make ``app`` importable from the backend rootDir.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.services import background_incident_job  # noqa: E402
from app.services.incident_scout_transport import close_incident_scout_client  # noqa: E402

# Explicit allowlist of payload-free metrics the CLI prints. The job's own
# metrics are already bounded (counts, statuses, canonical batch ids), but the
# allowlist guarantees no future field can leak incident content to logs.
_PRINTED_KEYS = (
    "status",
    "coverage",
    "official_sources",
    "incidents_upserted",
    "official_incidents",
    "unique_incident_ids",
    "model_calls",
    "scout_timeouts",
    "duration_ms",
)

_NO_REDIS_MESSAGE = (
    "[incident-refresh] REDIS_URL is required: the cron process must write "
    "the shared incident index that the web service reads"
)


def shared_cache_configured() -> bool:
    """True when a shared Redis cache is configured for the incident index."""
    return bool(os.getenv("REDIS_URL", "").strip())


def print_metrics(metrics: Mapping[str, Any]) -> None:
    """Print only the bounded, payload-free subset of one run's metrics."""
    bounded = {key: metrics.get(key) for key in _PRINTED_KEYS if key in metrics}
    print(f"[incident-refresh] {json.dumps(bounded, separators=(",", ":"), default=str)}")


async def run_once() -> int:
    """Run exactly one refresh cycle and return a process exit code."""
    try:
        metrics = await background_incident_job.run_background_incident_refresh()
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never print the exception: its message can embed provider payloads.
        print("[incident-refresh] cycle failed with an unhandled error", file=sys.stderr)
        return 2
    if not isinstance(metrics, Mapping):
        print("[incident-refresh] cycle returned an invalid result", file=sys.stderr)
        return 2
    print_metrics(metrics)
    status = str(metrics.get("status") or "")
    return 0 if status in {"complete", "partial", "lock_held"} else 1


async def _run_lifecycle() -> int:
    """Run the full CLI lifecycle on one event loop and return the exit code.

    Checks shared-cache configuration, awaits exactly one refresh cycle, and
    releases the shared scout transport in ``finally`` so it closes on the
    same loop that used it.
    """
    exit_code = 2
    try:
        if not shared_cache_configured():
            print(_NO_REDIS_MESSAGE, file=sys.stderr)
            return 3
        exit_code = await run_once()
    except Exception:
        # asyncio.CancelledError is a BaseException, so cancellation still
        # propagates to main() and maps to exit code 2 without this branch.
        print("[incident-refresh] cycle failed with an unhandled error", file=sys.stderr)
        exit_code = 2
    finally:
        try:
            await close_incident_scout_client()
        except Exception:
            # Transport close must never mask the run outcome or leak details.
            pass
    return exit_code


def main() -> int:
    """Run one cycle in a single event loop and return the exit code."""
    try:
        return asyncio.run(_run_lifecycle())
    except asyncio.CancelledError:
        return 2
    except Exception:
        print("[incident-refresh] cycle failed with an unhandled error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
