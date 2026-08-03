"""Compatibility facade for MTA realtime services.

The implementation now lives under ``app.services.mta`` split by domain:
configuration, GTFS realtime feed fetching/parsing, service alerts, subway
vehicles, and BusTime. Existing routers and tests still import this module, so
it deliberately re-exports the previous API while callers migrate.
"""

from app.services.mta.alerts import (
    _english_text,
    _parse_service_alerts,
    _period_bounds,
    _period_is_active,
    _period_is_today_or_unexpired,
    fetch_service_alerts,
    filter_alerts_for_routes,
    parse_service_alerts,
    parse_service_alerts_for_service_board,
)
from app.services.mta.bus import (
    _as_list,
    _bus_api_key,
    _bus_stop_record,
    _first_text,
    _parse_siri_time,
    _route_ids_for_bus_stop,
    _stops_for_location_list,
    _strip_mta_bus_prefix,
    fetch_bus_positions,
    fetch_bus_stop_monitoring,
    fetch_nearby_bus_stops,
    get_stalled_buses,
    parse_bus_stop_monitoring,
)
from app.services.mta.bus_runtime import close_bus_client, start_bus_client
from app.services.mta.bus_updates import (
    cached_nearby_bus_update,
    fetch_nearby_bus_arrivals,
    fetch_nearby_bus_update,
)
from app.services.mta.config import (
    ALERTS_URL,
    ALL_SUBWAY_ROUTES,
    BASE_URL,
    BUS_STOP_MONITORING_URL,
    BUS_STOPS_FOR_LOCATION_URL,
    BUS_URL,
    MTA_COLORS,
    NYC_TZ,
    get_route_color,
    route_to_feed,
)
from app.services.mta.feeds import (
    _feed_url_for_suffix,
    _log_fetch_failure,
    _log_fetch_summary,
    _routes_for_suffix,
    fetch_feeds,
    fetch_feeds_with_metadata,
    parse_bytes,
)
from app.services.mta.subway import (
    _build_subway_vehicle_positions,
    _log_vehicle_diagnostics,
    _vehicle_status_name,
    get_all_subway_vehicle_positions,
    get_stalled_trains,
    parse_vehicle_positions,
)

