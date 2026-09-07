"""Concrete realtime MTA service interface.

Consumers import this module when they need a small cross-provider realtime
surface; implementation remains split by feed, vehicle, alert, and BusTime
lifecycle underneath.
"""

from app.services.mta.alerts import (
    fetch_service_alerts,
    filter_alerts_for_routes,
    parse_service_alerts,
)
from app.services.mta.bus import get_stalled_buses
from app.services.mta.bus_updates import (
    cached_nearby_bus_update,
    fetch_nearby_bus_update,
)
from app.services.mta.config import ALL_SUBWAY_ROUTES
from app.services.mta.feeds import (
    fetch_feeds_with_metadata,
    parse_bytes,
)
from app.services.mta.subway import get_stalled_trains

__all__ = [
    "ALL_SUBWAY_ROUTES",
    "cached_nearby_bus_update",
    "fetch_feeds_with_metadata",
    "fetch_nearby_bus_update",
    "fetch_service_alerts",
    "filter_alerts_for_routes",
    "get_stalled_buses",
    "get_stalled_trains",
    "parse_bytes",
    "parse_service_alerts",
]
