"""Concrete realtime MTA service interface.

Consumers import this module when they need a small cross-provider realtime
surface; implementation remains split by feed, vehicle, alert, and BusTime
lifecycle underneath.
"""

from app.services.mta.alerts import (
    fetch_service_alerts,  # noqa: F401
    filter_alerts_for_routes,  # noqa: F401
    parse_service_alerts,  # noqa: F401
)
from app.services.mta.bus import get_stalled_buses  # noqa: F401
from app.services.mta.bus_updates import (
    cached_nearby_bus_update,  # noqa: F401
    fetch_nearby_bus_update,  # noqa: F401
)
from app.services.mta.config import ALL_SUBWAY_ROUTES  # noqa: F401
from app.services.mta.feeds import (  # noqa: F401
    fetch_feeds_with_metadata,
    parse_bytes,
)
from app.services.mta.subway import get_stalled_trains  # noqa: F401
