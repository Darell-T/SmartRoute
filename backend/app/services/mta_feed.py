

from google.transit import gtfs_realtime_pb2
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
import asyncio
import math
import os
import time

NYC_TZ = ZoneInfo("America/New_York")

BASE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs"
BUS_URL = "https://bustime.mta.info/api/siri/vehicle-monitoring.json"
BUS_STOPS_FOR_LOCATION_URL = "https://bustime.mta.info/api/where/stops-for-location.json"
BUS_STOP_MONITORING_URL = "https://bustime.mta.info/api/siri/stop-monitoring.json"

route_to_feed = {
    "A": "ace", "C": "ace", "E": "ace",
    "B": "bdfm", "D": "bdfm", "F": "bdfm", "M": "bdfm",
    "G": "g",
    "J": "jz", "Z": "jz",
    "N": "nqrw", "Q": "nqrw", "R": "nqrw", "W": "nqrw",
    "L": "l",
    "1": "", "2": "", "3": "", "4": "", "5": "", "6": "", "7": "",
    "S": "", "FS": "bdfm", "GS": "", "H": "ace",
    "SI": "si",
}

MTA_COLORS = {
    "A": "#0039A6", "C": "#0039A6", "E": "#0039A6",
    "B": "#FF6319", "D": "#FF6319", "F": "#FF6319", "M": "#FF6319", "FX": "#FF6319",
    "G": "#6CBE45",
    "J": "#996633", "Z": "#996633",
    "L": "#A7A9AC",
    "N": "#FCCC0A", "Q": "#FCCC0A", "R": "#FCCC0A", "W": "#FCCC0A",
    "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
    "4": "#00933C", "5": "#00933C", "6": "#00933C", "6X": "#00933C",
    "7": "#B933AD", "7X": "#B933AD",
    "S": "#808183", "FS": "#808183", "GS": "#808183", "H": "#808183",
    "SI": "#00A9CE",
}

_FETCH_FAILURE_LOGS: dict[str, float] = {}
_FETCH_SUMMARY_LOGS: dict[str, float] = {}
_FETCH_FAILURE_LOG_COOLDOWN = 60
_FETCH_SUMMARY_LOG_COOLDOWN = 15


def get_route_color(route_id: str) -> str:
    return MTA_COLORS.get((route_id or "").upper(), "#808183")


def _vehicle_status_name(vehicle) -> str:
    try:
        return gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus.Name(vehicle.current_status)
    except Exception:
        return str(vehicle.current_status)


def _log_fetch_failure(url: str, message: str):
    now = time.monotonic()
    last = _FETCH_FAILURE_LOGS.get(url, 0)
    if now - last < _FETCH_FAILURE_LOG_COOLDOWN:
        return
    _FETCH_FAILURE_LOGS[url] = now
    print(message)


def _feed_url_for_suffix(suffix: str) -> str:
    return f"{BASE_URL}-{suffix}" if suffix else BASE_URL


def _routes_for_suffix(suffix: str, requested_routes: set[str]) -> list[str]:
    return sorted(
        route
        for route, feed_suffix in route_to_feed.items()
        if feed_suffix == suffix and route in requested_routes
    )


# Per-fetch vehicle/arrivals summary dumps are verbose operational telemetry.
# Off by default so the console stays readable; set BACKEND_VERBOSE_LOGS=1 to
# re-enable for debugging feed health.
def _log_fetch_summary(context: str, message: str):
    if os.getenv("BACKEND_VERBOSE_LOGS", "0") != "1":
        return
    now = time.monotonic()
    last = _FETCH_SUMMARY_LOGS.get(context, 0)
    if now - last < _FETCH_SUMMARY_LOG_COOLDOWN:
        return
    _FETCH_SUMMARY_LOGS[context] = now
    print(message)


async def fetch_feeds_with_metadata(
    routes: list, log_context: str | None = None, force_refresh: bool = False
) -> list[dict]:
    from app.utils.cache import cache_get, cache_set

    requested_routes = {str(route).upper() for route in routes}
    unique_suffixes = set()
    for route in routes:
        if route in route_to_feed:
            unique_suffixes.add(route_to_feed[route])

    if not unique_suffixes:
        print("Error: No valid train routes provided.")
        return []

    feed_requests = []
    for suffix in unique_suffixes:
        feed_requests.append({
            "suffix": suffix or "numbered",
            "url": _feed_url_for_suffix(suffix),
            "routes": _routes_for_suffix(suffix, requested_routes),
        })

    results = []
    urls_to_fetch = []

    for feed_request in feed_requests:
        # force_refresh (used by the background warm loop) bypasses the cache so
        # the URL is always re-fetched and the TTL is renewed -- this keeps the
        # cache warm for user-facing requests, which still take the cached path.
        cached = None if force_refresh else cache_get(feed_request["url"])
        if cached:
            results.append({
                **feed_request,
                "content": cached,
                "bytes": len(cached),
                "from_cache": True,
            })
        else:
            urls_to_fetch.append(feed_request)

    if urls_to_fetch:
        # Timeout + return_exceptions so a single slow/dead MTA endpoint
        # doesn't poison the whole live_feed/vehicles request. We log and
        # skip failures rather than letting them propagate as 500s.
        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = [client.get(feed_request["url"]) for feed_request in urls_to_fetch]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
        for feed_request, response in zip(urls_to_fetch, responses):
            url = feed_request["url"]
            if isinstance(response, Exception):
                _log_fetch_failure(
                    url,
                    f"[mta_feed] feed fetch failed for {url}: {type(response).__name__}: {response!r}",
                )
                continue
            if response.status_code != 200:
                _log_fetch_failure(
                    url,
                    f"[mta_feed] feed {url} returned {response.status_code}",
                )
                continue
            cache_set(url, response.content, 30)
            results.append({
                **feed_request,
                "content": response.content,
                "bytes": len(response.content),
                "from_cache": False,
            })

    if log_context:
        ok = ", ".join(
            f"{item['suffix']}:{item['bytes']}b:{'cache' if item['from_cache'] else 'net'}"
            for item in sorted(results, key=lambda x: x["suffix"])
        ) or "none"
        failed = len(feed_requests) - len(results)
        _log_fetch_summary(
            log_context,
            (
                f"[mta_feed][{log_context}] fetch requested_feeds={len(feed_requests)} "
                f"ok={len(results)} failed={failed} routes={sorted(requested_routes)} feeds={ok}"
            ),
        )

    return results


async def fetch_feeds(routes: list, log_context: str | None = None) -> list:
    results = await fetch_feeds_with_metadata(routes, log_context)
    return [item["content"] for item in results]




def parse_bytes(rawBytes: bytes) -> list:
    user_feed = gtfs_realtime_pb2.FeedMessage()
    user_feed.ParseFromString(rawBytes)

    trip_updates  = []


    for entity in user_feed.entity:
        if entity.HasField("trip_update"):
            trip = entity.trip_update

            trip_id = trip.trip.trip_id
            route_id = trip.trip.route_id

            for stop in trip.stop_time_update:
                # NYC subway stop_ids end in N (uptown/northbound) or
                # S (downtown/southbound). Surface that to the rider so
                # the live-feed card doesn't say "Direction pending" for
                # every arrival.
                stop_id = stop.stop_id
                if stop_id.endswith("N"):
                    direction = "Uptown"
                elif stop_id.endswith("S"):
                    direction = "Downtown"
                else:
                    direction = None
                trip_updates.append({"route_id": route_id,
                "trip_id": trip_id,
                "stop_id": stop_id,
                "arrival_time": stop.arrival.time if stop.arrival.time else None,
                "delay": stop.arrival.delay,
                "direction": direction})


    return trip_updates

ALERTS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"


async def fetch_service_alerts(force_refresh: bool = False) -> bytes:
    from app.utils.cache import cache_get, cache_set

    cached = None if force_refresh else cache_get(ALERTS_URL)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(ALERTS_URL)
        if response.status_code != 200:
            print(f"[mta_feed] alerts feed returned {response.status_code}")
            return b""
        cache_set(ALERTS_URL, response.content, 60)
        return response.content
    except Exception as exc:
        print(f"[mta_feed] alerts feed failed: {type(exc).__name__}: {exc!r}")
        return b""


# Every subway route, used by the warm loop to refresh all 8 feed URLs at once.
# The nyct GTFS-RT feeds carry both trip-updates AND vehicle positions, so
# warming these URLs primes the cache for nearby arrivals, network vehicles,
# and the live summary alike.
ALL_SUBWAY_ROUTES = list(route_to_feed.keys())


async def warm_realtime_caches() -> None:
    """Force-refresh the realtime caches (all subway feed URLs + service
    alerts) so live-feed / hub / alerts snapshots are served from a warm cache
    instead of paying upstream MTA latency on each connection. Exceptions are
    swallowed per-source via return_exceptions so one dead feed never stops the
    others from warming."""
    await asyncio.gather(
        fetch_feeds_with_metadata(ALL_SUBWAY_ROUTES, "warm", force_refresh=True),
        fetch_service_alerts(force_refresh=True),
        return_exceptions=True,
    )


def _period_bounds(period) -> tuple[int | None, int | None]:
    start = period.start if period.start else None
    end = period.end if period.end else None
    return start, end


def _period_is_active(start: int | None, end: int | None, now: float) -> bool:
    if start and now < start:
        return False
    if end and end > 0 and now > end:
        return False
    return True


def _period_is_today_or_unexpired(start: int | None, end: int | None, now: float) -> bool:
    if end and end > 0 and now > end:
        return False

    today = datetime.fromtimestamp(now, tz=NYC_TZ).date()
    today_start = datetime(today.year, today.month, today.day, tzinfo=NYC_TZ).timestamp()
    tomorrow_start = today_start + 24 * 60 * 60

    effective_start = start or today_start
    effective_end = end if end and end > 0 else tomorrow_start
    return effective_start < tomorrow_start and effective_end >= today_start


def _english_text(text_field) -> str:
    if not text_field or not text_field.translation:
        return ""

    fallback = ""
    for translation in text_field.translation:
        if not fallback:
            fallback = translation.text
        if translation.language == "en":
            return translation.text
    return fallback


def _parse_service_alerts(
    rawBytes: bytes,
    *,
    include_same_day: bool,
    now_timestamp: float | None = None,
) -> list:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(rawBytes)

    now = now_timestamp if now_timestamp is not None else datetime.now(tz=NYC_TZ).timestamp()
    alerts = []

    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue

        alert = entity.alert

        # Extract the first relevant active_period. The route-planning parser
        # keeps the old "currently active only" behavior, while the service
        # alerts board also includes same-day future notices until they expire.
        start = None
        end = None
        if alert.active_period:
            matching_period = None
            for period in alert.active_period:
                period_start, period_end = _period_bounds(period)
                matches = (
                    _period_is_today_or_unexpired(period_start, period_end, now)
                    if include_same_day
                    else _period_is_active(period_start, period_end, now)
                )
                if matches:
                    matching_period = period
                    break

            if matching_period is None:
                continue

            start, end = _period_bounds(matching_period)

        header = _english_text(alert.header_text)
        description = _english_text(alert.description_text)

        # Collect route_ids and stop_ids from informed_entity
        route_ids = set()
        stop_ids = set()
        for ie in alert.informed_entity:
            if ie.route_id:
                route_ids.add(ie.route_id)
            if ie.stop_id:
                stop_ids.add(ie.stop_id)

        alerts.append({
            "alert_id": entity.id,
            "header": header,
            "description": description,
            "route_ids": list(route_ids),
            "stop_ids": list(stop_ids),
            "start": start,
            "end": end,
        })

    return alerts


def parse_service_alerts(rawBytes: bytes) -> list:
    return _parse_service_alerts(rawBytes, include_same_day=False)


def parse_service_alerts_for_service_board(rawBytes: bytes) -> list:
    return _parse_service_alerts(rawBytes, include_same_day=True)


def filter_alerts_for_routes(alerts: list, route_ids: set) -> list:
    return [a for a in alerts if set(a["route_ids"]) & route_ids]


def parse_vehicle_positions(
    rawBytes: bytes,
    source: str = "unknown",
    diagnostics: list[dict] | None = None,
    include_stop_only: bool = False,
) -> list:
    locations = gtfs_realtime_pb2.FeedMessage()
    locations.ParseFromString(rawBytes)

    vehicle_positions = []
    stats = {
        "source": source,
        "bytes": len(rawBytes),
        "entities": len(locations.entity),
        "trip_updates": 0,
        "vehicle_entities": 0,
        "vehicles_with_position": 0,
        "vehicles_without_position": 0,
        "zero_coordinates": 0,
        "missing_route": 0,
        "valid_positions": 0,
        "routes": {},
        "sample_without_position": [],
    }

    for entity in locations.entity:
        if entity.HasField("trip_update"):
            stats["trip_updates"] += 1
        if entity.HasField("vehicle"):
            stats["vehicle_entities"] += 1
            vehicle = entity.vehicle
            route_id_for_stats = vehicle.trip.route_id or "?"
            stats["routes"][route_id_for_stats] = stats["routes"].get(route_id_for_stats, 0) + 1

            if not vehicle.HasField("position"):
                stats["vehicles_without_position"] += 1
                if len(stats["sample_without_position"]) < 3:
                    stats["sample_without_position"].append({
                        "entity_id": entity.id,
                        "trip_id": vehicle.trip.trip_id,
                        "route_id": route_id_for_stats,
                        "stop_id": vehicle.stop_id,
                        "current_stop_sequence": vehicle.current_stop_sequence or None,
                        "status": _vehicle_status_name(vehicle),
                    })
                if include_stop_only and route_id_for_stats != "?" and vehicle.stop_id:
                    vehicle_positions.append({
                        "id": entity.id or vehicle.trip.trip_id or f"{route_id_for_stats}-{vehicle.stop_id}",
                        "trip_id": vehicle.trip.trip_id or None,
                        "route_id": route_id_for_stats,
                        "lat": None,
                        "lng": None,
                        "stop_id": vehicle.stop_id,
                        "status": _vehicle_status_name(vehicle),
                        "current_stop_sequence": vehicle.current_stop_sequence or None,
                        "timestamp": vehicle.timestamp or None,
                        "color": get_route_color(route_id_for_stats),
                        "position_source": "stop_id_pending_coords",
                    })
                continue

            trip_id = vehicle.trip.trip_id
            route_id = vehicle.trip.route_id
            coordinates = (vehicle.position.latitude, vehicle.position.longitude)
            stop_id = vehicle.stop_id
            status = _vehicle_status_name(vehicle)
            timestamp = vehicle.timestamp

            stats["vehicles_with_position"] += 1
            if not route_id:
                stats["missing_route"] += 1
            if vehicle.position.latitude == 0 and vehicle.position.longitude == 0:
                stats["zero_coordinates"] += 1
            else:
                stats["valid_positions"] += 1

            vehicle_positions.append({
                "id": entity.id or trip_id or f"{route_id}-{stop_id}-{timestamp}",
                "trip_id": trip_id,
                "route_id": route_id,
                "coordinates": coordinates,
                "lat": vehicle.position.latitude,
                "lng": vehicle.position.longitude,
                "stop_id": stop_id,
                "status": status,
                "current_stop_sequence": vehicle.current_stop_sequence or None,
                "timestamp": timestamp,
                "color": get_route_color(route_id),
                "position_source": "vehicle_position",
            })
    
    if diagnostics is not None:
        diagnostics.append(stats)

    return vehicle_positions


def _log_vehicle_diagnostics(debug: dict):
    # Verbose per-fetch vehicle dump. Off unless BACKEND_VERBOSE_LOGS=1.
    if os.getenv("BACKEND_VERBOSE_LOGS", "0") != "1":
        return
    print(
        "[mta_feed][vehicles] "
        f"scope={debug['scope']} requested_routes={debug['requested_routes']} "
        f"feeds_ok={debug['feeds_ok']} feed_failures={debug['feed_failures']} "
        f"entities={debug['entities']} trip_updates={debug['trip_updates']} "
        f"vehicle_entities={debug['vehicle_entities']} "
        f"with_position={debug['vehicles_with_position']} "
        f"without_position={debug['vehicles_without_position']} "
        f"zero_coords={debug['zero_coordinates']} raw_positions={debug['raw_positions']} "
        f"stop_only_candidates={debug.get('stop_only_candidates', 0)} "
        f"final_markers={debug['final_markers']}"
    )
    for feed in debug["feeds"]:
        sample = feed.get("sample_without_position") or []
        print(
            "[mta_feed][vehicles][feed] "
            f"{feed['source']} bytes={feed['bytes']} entities={feed['entities']} "
            f"trip_updates={feed['trip_updates']} vehicle_entities={feed['vehicle_entities']} "
            f"with_position={feed['vehicles_with_position']} "
            f"without_position={feed['vehicles_without_position']} "
            f"valid_positions={feed['valid_positions']} routes={feed['routes']} "
            f"sample_without_position={sample}"
        )


async def get_all_subway_vehicle_positions(
    route_ids: set[str] | list[str] | None = None,
    debug: bool = False,
    include_stop_only: bool = False,
) -> list:
    requested_routes = list(route_ids) if route_ids else list(route_to_feed.keys())
    requested_set = {r for r in requested_routes if r in route_to_feed}
    raw_feeds = await fetch_feeds_with_metadata(
        requested_routes,
        "vehicles_all" if route_ids is None else "vehicles_scoped",
    )
    # Decoding protobuf for the whole subway feed (1000+ entities x 8 feeds) and
    # building the marker dicts is CPU-bound. Run it in a worker thread so it
    # never blocks the event loop -- otherwise concurrent live-feed websockets
    # starve in-flight trip requests (Google/Claude awaits) and they time out.
    return await asyncio.to_thread(
        _build_subway_vehicle_positions,
        raw_feeds, requested_set, route_ids, debug, include_stop_only,
    )


def _build_subway_vehicle_positions(raw_feeds, requested_set, route_ids, debug, include_stop_only):
    all_positions = []
    feed_diagnostics: list[dict] = []
    for feed in raw_feeds:
        all_positions.extend(
            parse_vehicle_positions(
                feed["content"],
                source=feed["suffix"],
                diagnostics=feed_diagnostics,
                include_stop_only=include_stop_only,
            )
        )

    now = datetime.now(tz=NYC_TZ).timestamp()
    vehicles = []
    seen_ids = set()
    for pos in all_positions:
        lat = pos.get("lat")
        lng = pos.get("lng")
        route_id = pos.get("route_id")
        if (lat is None or lng is None) and not include_stop_only:
            continue
        if lat is not None and lng is not None and lat == 0 and lng == 0:
            continue
        if not route_id:
            continue
        if requested_set and route_id not in requested_set:
            continue

        vehicle_id = pos.get("id") or f"{route_id}-{pos.get('trip_id', '')}-{pos.get('stop_id', '')}"
        if vehicle_id in seen_ids:
            continue
        seen_ids.add(vehicle_id)

        timestamp = pos.get("timestamp") or None
        age_seconds = round(now - timestamp) if timestamp else None
        vehicles.append({
            "id": vehicle_id,
            "trip_id": pos.get("trip_id") or None,
            "route_id": route_id,
            "lat": lat,
            "lng": lng,
            "stop_id": pos.get("stop_id") or None,
            "status": pos.get("status") or None,
            "current_stop_sequence": pos.get("current_stop_sequence") or None,
            "timestamp": timestamp,
            "age_seconds": age_seconds,
            "stale": bool(age_seconds is not None and age_seconds > 300),
            "color": get_route_color(route_id),
            "position_source": pos.get("position_source") or "vehicle_position",
        })

    if debug:
        debug_payload = {
            "scope": "all_subway" if route_ids is None else "nearest_routes",
            "requested_routes": sorted(requested_set),
            "feeds_ok": len(raw_feeds),
            "feed_failures": len({route_to_feed[r] for r in requested_set}) - len(raw_feeds),
            "entities": sum(item["entities"] for item in feed_diagnostics),
            "trip_updates": sum(item["trip_updates"] for item in feed_diagnostics),
            "vehicle_entities": sum(item["vehicle_entities"] for item in feed_diagnostics),
            "vehicles_with_position": sum(item["vehicles_with_position"] for item in feed_diagnostics),
            "vehicles_without_position": sum(item["vehicles_without_position"] for item in feed_diagnostics),
            "zero_coordinates": sum(item["zero_coordinates"] for item in feed_diagnostics),
            "raw_positions": len(all_positions),
            "final_markers": len(vehicles),
            "stop_only_candidates": sum(
                1 for pos in all_positions if pos.get("position_source") == "stop_id_pending_coords"
            ),
            "feeds": feed_diagnostics,
        }
        _log_vehicle_diagnostics(debug_payload)
        return vehicles, debug_payload

    return vehicles


async def get_stalled_trains(route_ids: set) -> list:
    """Fetch vehicle positions for the given route IDs and return any trains
    that haven't reported a position update in over 5 minutes."""
    if not route_ids:
        return []

    raw_feeds = await fetch_feeds(list(route_ids))
    all_positions = []
    for feed in raw_feeds:
        all_positions.extend(parse_vehicle_positions(feed))

    now = datetime.now(tz=NYC_TZ).timestamp()
    stalled = []
    for pos in all_positions:
        if pos["route_id"] in route_ids and pos["timestamp"] and (now - pos["timestamp"]) > 300:
            stalled.append({
                "route_id": pos["route_id"],
                "stop_id": pos["stop_id"],
                "status": pos["status"],
                "stalled_minutes": round((now - pos["timestamp"]) / 60),
            })
    return stalled

async def fetch_bus_positions(route_id) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            BUS_URL, params={
                "key": os.getenv("MTA_BUS_API_KEY"),
                "version": 2,
                "LineRef": route_id,
            }, timeout=10.0
        )
        return response.json()


def _bus_api_key() -> str | None:
    key = os.getenv("MTA_BUS_API_KEY")
    return key.strip() if key and key.strip() else None


def _strip_mta_bus_prefix(value: str | None) -> str:
    text = str(value or "").strip()
    for prefix in ("MTA NYCT_", "MTABC_", "MTA_"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _parse_siri_time(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=NYC_TZ)
        return int(parsed.timestamp())
    except ValueError:
        return None


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return None
    if isinstance(value, dict):
        for key in ("text", "value", "Name", "name"):
            text = _first_text(value.get(key))
            if text:
                return text
        return None
    text = str(value).strip()
    return text or None


def _route_ids_for_bus_stop(stop: dict) -> list[str]:
    raw_route_ids = stop.get("routeIds") or stop.get("routes") or []
    routes = []
    for route in _as_list(raw_route_ids):
        if isinstance(route, dict):
            candidate = route.get("shortName") or route.get("id")
        else:
            candidate = route
        route_id = _strip_mta_bus_prefix(_first_text(candidate))
        if route_id and route_id not in routes:
            routes.append(route_id)
    return routes


def _stops_for_location_list(payload: dict) -> list:
    """Extract the stop array from a stops-for-location response.

    MTA BusTime's OneBusAway flavor returns ``data.stops``; vanilla
    OneBusAway returns ``data.list``. Reading only ``data.list`` made every
    location report zero nearby bus stops, killing bus arrivals entirely.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    stops = data.get("stops")
    if isinstance(stops, list):
        return stops
    fallback = data.get("list")
    return fallback if isinstance(fallback, list) else []


def _bus_stop_record(stop: dict, distance_m: float) -> dict:
    """Normalize one OBA stops-for-location entry into our stop dict.

    ``direction`` is the compass heading of the stop (e.g. ``NE``, ``SW``);
    NYC bus stops are directional, so it is the travel direction of every
    bus serving the stop. Passed through raw as ``stop_compass`` --
    deliberately NOT named ``direction`` to avoid colliding with SIRI
    DirectionRef on arrivals or subway direction labels.

    Precondition: ``stop`` must have ``lat`` and ``lon`` (raises KeyError
    otherwise); the caller filters out coordinate-less stops first.
    """
    return {
        "stop_id": str(stop.get("id") or ""),
        "stop_name": str(stop.get("name") or "Bus stop"),
        "stop_lat": float(stop["lat"]),
        "stop_lon": float(stop["lon"]),
        "distance_m": distance_m,
        "route_ids": _route_ids_for_bus_stop(stop),
        "stop_compass": str(stop.get("direction") or ""),
    }


async def fetch_nearby_bus_stops(
    lat: float,
    lng: float,
    radius_m: float = 804.672,
    limit: int = 12,
) -> tuple[list[dict], dict]:
    key = _bus_api_key()
    if not key:
        return [], {"bus_arrivals_supported": False, "reason": "missing_mta_bus_api_key"}

    # OneBusAway stops-for-location uses a bounding box. Use a half-mile-ish
    # span, then enforce the exact radius locally.
    lat_span = max(0.002, radius_m / 111_320 * 2)
    lon_scale = max(0.2, abs(math.cos(math.radians(lat))))
    lon_span = max(0.002, radius_m / (111_320 * lon_scale) * 2)

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                BUS_STOPS_FOR_LOCATION_URL,
                params={
                    "key": key,
                    "lat": lat,
                    "lon": lng,
                    "latSpan": lat_span,
                    "lonSpan": lon_span,
                },
            )
        if response.status_code != 200:
            return [], {
                "bus_arrivals_supported": False,
                "reason": f"stops_for_location_http_{response.status_code}",
            }
        payload = response.json()
    except Exception as exc:
        return [], {"bus_arrivals_supported": False, "reason": type(exc).__name__}

    from app.utils.geo import distance_meters

    stops = []
    for stop in _stops_for_location_list(payload):
        stop_lat = stop.get("lat")
        stop_lon = stop.get("lon")
        if stop_lat is None or stop_lon is None:
            continue
        distance_m = distance_meters(lat, lng, float(stop_lat), float(stop_lon))
        if distance_m > radius_m:
            continue
        stops.append(_bus_stop_record(stop, distance_m))

    stops.sort(key=lambda stop: stop["distance_m"])
    return stops[:limit], {
        "bus_arrivals_supported": True,
        "nearby_bus_stop_count": len(stops),
    }


async def fetch_bus_stop_monitoring(stop_id: str, visits: int = 4) -> dict:
    key = _bus_api_key()
    if not key:
        return {}

    monitoring_ref = _strip_mta_bus_prefix(stop_id)
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(
            BUS_STOP_MONITORING_URL,
            params={
                "key": key,
                "version": 2,
                "MonitoringRef": monitoring_ref,
                "MaximumStopVisits": visits,
            },
        )
    if response.status_code != 200:
        return {}
    return response.json()


def parse_bus_stop_monitoring(payload: dict, stop: dict) -> list[dict]:
    service_delivery = payload.get("Siri", {}).get("ServiceDelivery", {})
    visits = []
    for delivery in _as_list(service_delivery.get("StopMonitoringDelivery")):
        if not isinstance(delivery, dict):
            continue
        visits.extend(_as_list(delivery.get("MonitoredStopVisit")))

    arrivals = []
    for visit in visits:
        if not isinstance(visit, dict):
            continue
        journey = visit.get("MonitoredVehicleJourney", {})
        call = journey.get("MonitoredCall", {})
        arrival_time = (
            _parse_siri_time(_first_text(call.get("ExpectedArrivalTime")))
            or _parse_siri_time(_first_text(call.get("ExpectedDepartureTime")))
            or _parse_siri_time(_first_text(call.get("AimedArrivalTime")))
            or _parse_siri_time(_first_text(call.get("AimedDepartureTime")))
        )
        if not arrival_time:
            continue
        route_id = _strip_mta_bus_prefix(
            _first_text(journey.get("PublishedLineName"))
            or _first_text(journey.get("LineRef"))
        )
        if not route_id:
            continue
        arrivals.append({
            "route_id": route_id,
            "trip_id": journey.get("FramedVehicleJourneyRef", {}).get("DatedVehicleJourneyRef"),
            "stop_id": _strip_mta_bus_prefix(
                _first_text(call.get("StopPointRef")) or stop.get("stop_id")
            ),
            "arrival_time": arrival_time,
            "delay": None,
            "direction": _first_text(journey.get("DirectionRef")),
            "terminal_stop_name": _first_text(journey.get("DestinationName")),
            "parent_stop_id": stop.get("stop_id"),
            "parent_stop_name": stop.get("stop_name"),
            "station_name": stop.get("stop_name"),
            "distance_m": stop.get("distance_m"),
            "stop_compass": str(stop.get("stop_compass") or ""),
            "stop_lat": stop.get("stop_lat"),
            "stop_lon": stop.get("stop_lon"),
            "mode": "bus",
        })
    return arrivals


async def fetch_nearby_bus_arrivals(
    lat: float,
    lng: float,
    radius_m: float = 804.672,
    stop_limit: int = 10,
    visits_per_stop: int = 4,
) -> tuple[list[dict], dict]:
    stops, debug = await fetch_nearby_bus_stops(lat, lng, radius_m, stop_limit)
    if not stops:
        return [], {**debug, "bus_arrival_count": 0}

    tasks = [fetch_bus_stop_monitoring(stop["stop_id"], visits_per_stop) for stop in stops]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    arrivals = []
    failures = 0
    for stop, result in zip(stops, results):
        if isinstance(result, Exception):
            failures += 1
            continue
        arrivals.extend(parse_bus_stop_monitoring(result, stop))

    now = datetime.now(tz=NYC_TZ).timestamp()
    arrivals = [
        arrival for arrival in arrivals
        if arrival.get("arrival_time") and arrival["arrival_time"] >= now - 60
    ]
    arrivals.sort(key=lambda arrival: arrival.get("arrival_time") or 0)
    return arrivals, {
        **debug,
        "nearby_bus_stop_count": len(stops),
        "bus_arrival_count": len(arrivals),
        "bus_stop_monitoring_failures": failures,
    }


async def get_stalled_buses(route_ids:set) -> list:
    if not route_ids:
        return []

    stalled_buses = []

    tasks = [fetch_bus_positions(line) for line in route_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            print(f"[mta_feed] bus feed fetch error: {res}")
            continue

        vehicles = (
            res.get("Siri", {})
            .get("ServiceDelivery", {})
            .get("VehicleMonitoringDelivery", [{}])[0]
            .get("VehicleActivity", [])
        )

        for vehicle in vehicles:
            vehicle_position = vehicle.get("MonitoredVehicleJourney", {})
            progress_rate = vehicle_position.get("ProgressRate")
            progress_status = vehicle_position.get("ProgressStatus", [])

            if progress_rate == "noProgress" and "layover" not in progress_status:
                line_ref = vehicle_position.get("LineRef", "")
                location = vehicle_position.get("VehicleLocation")
                # SIRI often puts RecordedAtTime at VehicleActivity level.
                recorded_at_time = vehicle.get("RecordedAtTime") or vehicle_position.get("RecordedAtTime")

                if not line_ref or location is None:
                    continue

                stalled_buses.append({
                    "route_id": line_ref.replace("MTA NYCT_", ""),
                    "location": location,
                    "time_recorded": recorded_at_time
                })
    
    return stalled_buses
