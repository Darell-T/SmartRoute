"""Warm the MTA realtime caches.

Pulled out of the legacy ``mta_feed`` facade so the warm-cache logic lives in
the domain package. Imported by app startup and the background refresh loop.
Kept in its own module because it depends on both ``feeds`` and ``alerts``
(``alerts`` already imports ``feeds``), which would otherwise be a cycle.
"""

from __future__ import annotations

import asyncio

from app.services.mta.alerts import fetch_service_alerts
from app.services.mta.config import ALL_SUBWAY_ROUTES
from app.services.mta.feeds import fetch_feeds_with_metadata


async def warm_realtime_caches() -> None:
    """Warm subway realtime and service-alert caches.

    Run at startup and by the background loop so user-facing snapshots hit a
    warm cache instead of paying upstream fetch latency.
    """
    await asyncio.gather(
        fetch_feeds_with_metadata(ALL_SUBWAY_ROUTES, "warm", force_refresh=True),
        fetch_service_alerts(force_refresh=True),
        return_exceptions=True,
    )
