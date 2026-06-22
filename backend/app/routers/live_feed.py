from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.utils.geo import find_nearest_stops
from app.services import mta_feed
from app.services.ai_advisor import generate_live_network_summary
from app.services.incident_monitor import get_incidents
from app.utils.cache import cache_get, cache_set
import asyncio
import hashlib
import hmac
import json
import os
import time


router = APIRouter()
ws_router = APIRouter()


# Realtime push fan-out. The background warm loop (main.py) calls
# signal_realtime_refresh() right after it refreshes the MTA caches; every
# connected live-feed socket is awaiting the current event, so it wakes and
# pushes a fresh snapshot the instant new upstream data lands -- event-driven
# push, not a per-client timer. Swap-and-set so each waiter wakes exactly once
# and any new waiter gets a fresh event (no clear/set race).
_realtime_refresh_event: asyncio.Event = asyncio.Event()


def get_realtime_refresh_event() -> asyncio.Event:
    return _realtime_refresh_event


def signal_realtime_refresh() -> None:
    global _realtime_refresh_event
    previous = _realtime_refresh_event
    _realtime_refresh_event = asyncio.Event()
    previous.set()


def _verify_ws_ticket(ticket: str, path: str) -> bool:
    """Validate a short-lived ticket minted by the Next /api/ws-ticket route.

    The ticket is "<exp>.<hmac_sha256(APP_KEY, exp.path)>". Binding the
    signature to the websocket path prevents a ticket minted for one endpoint
    from being replayed on another. Fails closed when APP_KEY is unset, the path
    is missing, or the ticket is malformed.
    """
    app_key = os.getenv("APP_KEY", "")
    if not app_key or not ticket or not path:
        return False
    exp_str, _, sig = ticket.partition(".")
    if not exp_str or not sig:
        return False
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(
        app_key.encode(),
        f"{exp_str}.{path}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def _vlog(message: str):
    """Routine websocket telemetry (connection lifecycle, per-snapshot dumps).
    Off by default so the console stays readable; set BACKEND_VERBOSE_LOGS=1 to
    re-enable. Genuine errors are logged unconditionally with print()."""
    if os.getenv("BACKEND_VERBOSE_LOGS", "0") == "1":
        print(message)


_LAST_EMPTY_VEHICLE_LOG = 0.0
_WS_CONNECTION_COUNTER = 0
_LIVE_SUMMARY_CACHE_TTL = 3600
_SUMMARY_DISRUPTION_KEYWORDS = (
    "SUSPEND",
    "NO ",
    "SKIP",
    "BYPASS",
    "REROUT",
    "SLOW SPEED",
    "MAJOR DELAY",
    "PART SUSPEND",
)
_SUMMARY_CAUTION_KEYWORDS = (
    "DELAY",
    "SERVICE CHANGE",
    "PLANNED WORK",
    "LOCAL TO EXPRESS",
    "EXPRESS TO LOCAL",
    "SHUTTLE",
)
NEARBY_ARRIVAL_RADIUS_M = 804.672
NEARBY_ARRIVAL_STOP_LIMIT = 32


async def _send_json_safe(websocket: WebSocket, payload: dict) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


class LiveFeedRequest(BaseModel):
    lat: float
    lng: float

@router.post("/api/live-feed")
async def live_feed(request: Request, payload: LiveFeedRequest):
    gtfs = getattr(request.app.state, "gtfs", None)
    if gtfs is None:
        return JSONResponse({"error": "GTFS not ready"}, status_code=503)

    try:
        return await _live_feed_impl(gtfs, payload)
    except Exception as exc:
        import traceback
        print(f"[live_feed] UNHANDLED ERROR:\n{traceback.format_exc()}")
        # Always return JSON so the Next.js proxy doesn't choke on FastAPI's
        # default plain-text "Internal Server Error" body.
        return JSONResponse(
            {
                "nearest_stop": None,
                "stops": [],
                "arrivals": [],
                "alerts": [],
                "signals": None,
                "incidents": [],
                "updated_at": int(time.time()),
                "error": "live feed temporarily unavailable",
            },
            status_code=503,
        )


@router.get("/api/service-alerts")
async def service_alerts(request: Request):
    gtfs = getattr(request.app.state, "gtfs", None)
    return JSONResponse(await _service_alerts_payload(gtfs))


def _attach_alert_stop_names(alerts: list[dict], gtfs) -> None:
    stop_ids = {
        str(stop_id).strip()
        for alert in alerts
        for stop_id in alert.get("stop_ids", [])
        if str(stop_id).strip()
    }
    if not stop_ids or gtfs is None:
        return

    try:
        stop_locations = gtfs.get_stop_locations(list(stop_ids))
    except Exception as exc:
        print(f"[service_alerts] stop-name enrichment failed: {type(exc).__name__}: {exc!r}")
        return

    for alert in alerts:
        names = []
        seen = set()
        for stop_id in alert.get("stop_ids", []):
            stop_key = str(stop_id or "").strip()
            if not stop_key:
                continue
            location = stop_locations.get(stop_key) or stop_locations.get(stop_key.rstrip("NS"))
            name = location.get("stop_name") if isinstance(location, dict) else None
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        if names:
            alert["stop_names"] = names


async def _service_alerts_payload(gtfs=None):
    now = int(time.time())
    raw_alerts = await mta_feed.fetch_service_alerts()
    parsed_alerts = (
        mta_feed.parse_service_alerts_for_service_board(raw_alerts)
        if raw_alerts
        else []
    )
    _attach_alert_stop_names(parsed_alerts, gtfs)
    affected_routes = {
        str(route_id).strip().upper()
        for alert in parsed_alerts
        for route_id in alert.get("route_ids", [])
        if str(route_id).strip()
    }

    return {
        "alerts": parsed_alerts,
        "updated_at": now,
        "active_count": len(parsed_alerts),
        "affected_route_count": len(affected_routes),
        "source": "mta",
    }


def _service_alert_id(alert: dict, index: int) -> str:
    alert_id = alert.get("alert_id")
    if alert_id:
        return str(alert_id)
    route_ids = alert.get("route_ids") or alert.get("routeIds") or []
    start = alert.get("start") or index
    return f"{'-'.join(str(route_id) for route_id in route_ids) or 'system'}-{start}"


def _service_alert_signatures(alerts: list[dict]) -> dict[str, str]:
    signatures: dict[str, str] = {}
    for index, alert in enumerate(alerts):
        alert_id = _service_alert_id(alert, index)
        signatures[alert_id] = hashlib.sha256(
            json.dumps(alert, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    return signatures


async def _live_feed_impl(gtfs, payload: LiveFeedRequest):
    snapshot = await _build_live_snapshot(gtfs, payload.lat, payload.lng)
    return JSONResponse(
        {
            "nearest_stop": snapshot["nearest_stop"],
            "stops": snapshot["stops"],
            "arrivals": snapshot["arrivals"],
            "alerts": snapshot["alerts"],
            "vehicles": snapshot["vehicles"],
            "signals": snapshot["signals"],
            "incidents": snapshot["incidents"],
            "updated_at": snapshot["updated_at"],
            "degraded": snapshot["degraded"],
            "debug": snapshot["debug"],
            "summary": snapshot["summary"],
        }
    )


def _base_stop_id(stop_id: str | None) -> str | None:
    if not stop_id:
        return None
    return stop_id[:-1] if stop_id[-1:] in {"N", "S"} else stop_id


def _find_trip_stop_index(stops: list[dict], stop_id: str | None, sequence: int | None):
    if sequence is not None:
        for idx, stop in enumerate(stops):
            if stop.get("stop_sequence") == sequence:
                return idx

    base = _base_stop_id(stop_id)
    for idx, stop in enumerate(stops):
        sid = stop.get("stop_id")
        if sid == stop_id or _base_stop_id(sid) == base:
            return idx
    return None


def _arrival_lookup_key(trip_id: str | None, stop_id: str | None):
    if not trip_id or not stop_id:
        return None
    return (trip_id, stop_id)


def _attach_terminal_stop(record: dict, trip_stops: list[dict] | None):
    if not trip_stops:
        return
    terminal = trip_stops[-1]
    if not terminal:
        return
    record["terminal_stop_id"] = terminal.get("stop_id")
    record["terminal_stop_name"] = terminal.get("stop_name")


def _estimate_segment_progress(vehicle: dict, arrival_lookup: dict[tuple[str, str], int], now: int) -> float:
    status = str(vehicle.get("status") or "").upper()
    if "STOPPED_AT" in status:
        return 1.0
    if "INCOMING_AT" in status:
        return 0.88

    key = _arrival_lookup_key(vehicle.get("trip_id"), vehicle.get("stop_id"))
    arrival_time = arrival_lookup.get(key) if key else None
    if arrival_time:
        eta_seconds = arrival_time - now
        if eta_seconds <= 0:
            return 0.94
        if eta_seconds >= 180:
            return 0.22
        return max(0.22, min(0.94, 1 - (eta_seconds / 180) * 0.72))

    if "IN_TRANSIT_TO" in status:
        return 0.55
    return 0.72


def _interpolate_stop_segment(start: dict, end: dict, progress: float):
    progress = max(0, min(1, progress))
    return {
        "lat": start["lat"] + (end["lat"] - start["lat"]) * progress,
        "lng": start["lng"] + (end["lng"] - start["lng"]) * progress,
    }


def _attach_trip_segment(vehicle: dict, trip_stops: list[dict], arrival_lookup: dict[tuple[str, str], int], now: int):
    idx = _find_trip_stop_index(
        trip_stops,
        vehicle.get("stop_id"),
        vehicle.get("current_stop_sequence"),
    )
    if idx is None:
        return False

    target = trip_stops[idx]
    previous = trip_stops[idx - 1] if idx > 0 else None
    nxt = trip_stops[idx + 1] if idx + 1 < len(trip_stops) else None
    status = str(vehicle.get("status") or "").upper()

    if previous and "STOPPED_AT" not in status:
        start = previous
        end = target
        progress = _estimate_segment_progress(vehicle, arrival_lookup, now)
    elif previous:
        start = previous
        end = target
        progress = 1.0
    elif nxt:
        start = target
        end = nxt
        progress = 0.0
    else:
        return False

    estimate = _interpolate_stop_segment(start, end, progress)
    vehicle["lat"] = estimate["lat"]
    vehicle["lng"] = estimate["lng"]
    vehicle["stop_name"] = target["stop_name"]
    vehicle["position_source"] = "stop_id" if progress >= 0.99 else "polyline_estimate"
    vehicle["segment"] = {
        "from_stop_id": start["stop_id"],
        "from_stop_name": start["stop_name"],
        "from_lat": start["lat"],
        "from_lng": start["lng"],
        "to_stop_id": end["stop_id"],
        "to_stop_name": end["stop_name"],
        "to_lat": end["lat"],
        "to_lng": end["lng"],
        "progress": progress,
    }
    return True


def _normalize_route_ids(route_ids):
    return {
        str(route_id).strip().upper()
        for route_id in (route_ids or [])
        if str(route_id).strip()
    }


def _expand_vehicle_route_scope(route_ids):
    expanded = set(route_ids or set())
    if "S" in expanded:
        expanded.update({"FS", "GS", "H"})
    return expanded


def _build_nearby_stop_context(gtfs, stops: list[dict]) -> tuple[set[str], dict[str, dict]]:
    child_stop_ids: set[str] = set()
    stop_lookup: dict[str, dict] = {}

    for stop in stops:
        parent_id = str(stop.get("stop_id") or "").strip()
        if not parent_id:
            continue
        child_ids = set(gtfs.get_child_stop_ids(parent_id))
        child_ids.add(parent_id)
        for child_id in child_ids:
            child_key = str(child_id or "").strip()
            if not child_key:
                continue
            child_stop_ids.add(child_key)
            stop_lookup[child_key] = stop
            stop_lookup[child_key.rstrip("NS")] = stop

    return child_stop_ids, stop_lookup


def _arrival_stop_context(arrival: dict, stop_lookup: dict[str, dict]) -> dict | None:
    stop_id = str(arrival.get("stop_id") or "").strip()
    if not stop_id:
        return None
    return stop_lookup.get(stop_id) or stop_lookup.get(stop_id.rstrip("NS"))


async def _safe_nearby_bus_arrivals(lat: float, lng: float) -> tuple[list[dict], dict]:
    try:
        return await mta_feed.fetch_nearby_bus_arrivals(
            lat,
            lng,
            radius_m=NEARBY_ARRIVAL_RADIUS_M,
        )
    except Exception as exc:
        print(f"[live_feed] BusTime nearby arrivals failed: {type(exc).__name__}: {exc!r}")
        return [], {
            "bus_arrivals_supported": False,
            "reason": type(exc).__name__,
            "bus_arrival_count": 0,
        }


def _summary_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def _alert_summary_severity(alert: dict) -> str:
    text = f"{_summary_text(alert.get('header'))} {_summary_text(alert.get('description'))}".upper()
    if any(keyword in text for keyword in _SUMMARY_DISRUPTION_KEYWORDS):
        return "disrupted"
    if any(keyword in text for keyword in _SUMMARY_CAUTION_KEYWORDS):
        return "caution"
    return "caution"


def _derive_live_summary_status(
    active_alert_count: int,
    major_alert_count: int,
    stale_count: int,
    feed_failures: int,
    vehicle_entities: int,
    vehicles_without_position: int,
) -> str:
    no_position_ratio = (
        vehicles_without_position / vehicle_entities if vehicle_entities > 0 else 0
    )
    if (
        major_alert_count >= 2
        or active_alert_count >= 8
        or stale_count >= 20
        or (feed_failures > 0 and no_position_ratio >= 0.5)
        or no_position_ratio >= 0.85
    ):
        return "disrupted"
    if (
        active_alert_count > 0
        or stale_count > 0
        or feed_failures > 0
        or no_position_ratio >= 0.35
    ):
        return "caution"
    return "healthy"


def _build_live_summary_package(parsed_alerts: list[dict], network_vehicles: list[dict], vehicle_debug: dict) -> dict:
    affected_routes: set[str] = set()
    top_alerts = []
    major_alert_count = 0
    caution_alert_count = 0

    for alert in parsed_alerts:
        routes = sorted(
            {
                str(route_id).strip().upper()
                for route_id in (alert.get("route_ids") or [])
                if str(route_id).strip()
            }
        )
        affected_routes.update(routes)
        severity = _alert_summary_severity(alert)
        if severity == "disrupted":
            major_alert_count += 1
        else:
            caution_alert_count += 1
        header = _summary_text(alert.get("header"))
        if header:
            top_alerts.append(
                {
                    "severity": severity,
                    "header": header[:220],
                    "routes": routes[:8],
                }
            )

    severity_rank = {"disrupted": 0, "caution": 1}
    top_alerts.sort(
        key=lambda item: (
            severity_rank.get(item["severity"], 9),
            -len(item["routes"]),
            item["header"],
        )
    )

    stale_vehicles = [vehicle for vehicle in network_vehicles if vehicle.get("stale")]
    vehicle_entities = int(vehicle_debug.get("vehicle_entities") or 0)
    vehicles_without_position = int(vehicle_debug.get("vehicles_without_position") or 0)
    status = _derive_live_summary_status(
        active_alert_count=len(parsed_alerts),
        major_alert_count=major_alert_count,
        stale_count=len(stale_vehicles),
        feed_failures=int(vehicle_debug.get("feed_failures") or 0),
        vehicle_entities=vehicle_entities,
        vehicles_without_position=vehicles_without_position,
    )

    return {
        "network_status": status,
        "alerts": {
            "active_count": len(parsed_alerts),
            "major_count": major_alert_count,
            "caution_count": caution_alert_count,
            "affected_route_count": len(affected_routes),
            "affected_routes": sorted(affected_routes),
            "top_alerts": top_alerts[:3],
        },
        "vehicles": {
            "tracked_count": len(network_vehicles),
            "stale_count": len(stale_vehicles),
            "stale_routes": sorted({str(vehicle.get("route_id") or "").upper() for vehicle in stale_vehicles if vehicle.get("route_id")}),
            "routes_reporting": sorted({str(vehicle.get("route_id") or "").upper() for vehicle in network_vehicles if vehicle.get("route_id")}),
            "feeds_ok": int(vehicle_debug.get("feeds_ok") or 0),
            "feed_failures": int(vehicle_debug.get("feed_failures") or 0),
            "vehicle_entities": vehicle_entities,
            "vehicles_with_position": int(vehicle_debug.get("vehicles_with_position") or 0),
            "vehicles_without_position": vehicles_without_position,
            "stop_only_candidates": int(vehicle_debug.get("stop_only_candidates") or 0),
            "final_markers": int(vehicle_debug.get("final_markers") or 0),
        },
    }


def _load_cached_live_summary(cache_key: str) -> dict | None:
    cached = cache_get(cache_key)
    if not cached:
        return None
    if isinstance(cached, bytes):
        cached = cached.decode("utf-8")
    try:
        payload = json.loads(cached)
        if not isinstance(payload, dict):
            return None
        if payload.get("source") != "fallback":
            payload["source"] = "cached"
        return payload
    except json.JSONDecodeError:
        return None


def _build_live_signals(package: dict, updated_at: int) -> dict:
    alerts = package.get("alerts", {}) if isinstance(package, dict) else {}
    vehicles = package.get("vehicles", {}) if isinstance(package, dict) else {}
    return {
        "network_status": package.get("network_status") or "caution",
        "active_alert_count": int(alerts.get("active_count") or 0),
        "major_alert_count": int(alerts.get("major_count") or 0),
        "affected_route_count": int(alerts.get("affected_route_count") or 0),
        "tracked_vehicle_count": int(vehicles.get("tracked_count") or 0),
        "stale_vehicle_count": int(vehicles.get("stale_count") or 0),
        "routes_reporting_count": len(vehicles.get("routes_reporting") or []),
        "feed_failures": int(vehicles.get("feed_failures") or 0),
        "vehicles_with_position": int(vehicles.get("vehicles_with_position") or 0),
        "vehicles_without_position": int(vehicles.get("vehicles_without_position") or 0),
        "updated_at": updated_at,
    }


async def _build_live_network_summary_bundle(parsed_alerts: list[dict], updated_at: int) -> tuple[dict, dict]:
    network_vehicles, network_debug = await mta_feed.get_all_subway_vehicle_positions(
        None,
        debug=True,
        include_stop_only=True,
    )
    package = _build_live_summary_package(parsed_alerts, network_vehicles, network_debug)
    serialized = json.dumps(package, sort_keys=True, separators=(",", ":"))
    summary_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    cache_key = f"live_summary:{summary_hash}"

    cached = _load_cached_live_summary(cache_key)
    if cached:
        return cached, _build_live_signals(package, updated_at)

    summary = await generate_live_network_summary(package)
    cache_set(cache_key, json.dumps(summary), _LIVE_SUMMARY_CACHE_TTL)
    return summary, _build_live_signals(package, updated_at)


# --- ATLAS nearby incident scan (toggle-gated) -----------------------------
# When the rider turns ATLAS scan on, we look for incidents within the same
# half-mile radius as nearby transit. Grok + X-search is slow (5-30s), so the
# scan runs as a single-flight BACKGROUND task and each snapshot serves the most
# recent cached result. Keyed to a coarse location bucket so moving to a new
# area forces a fresh scan instead of showing the old neighborhood's incidents.
_NEARBY_INCIDENTS: list[dict] = []
_NEARBY_SCAN_BUCKET: tuple | None = None
_NEARBY_SCAN_TS = 0.0
_NEARBY_SCAN_INFLIGHT = False
_NEARBY_SCAN_TASKS: set = set()
_NEARBY_SCAN_TTL_S = 120.0

_INCIDENT_TYPE_KEYWORDS = [
    ("fire", "fire"), ("smoke", "fire"),
    ("stab", "stabbing"), ("shot", "weapon"), ("shoot", "weapon"),
    ("gun", "weapon"), ("weapon", "weapon"),
    ("assault", "assault"), ("fight", "assault"), ("robb", "police"),
    ("police", "police"), ("nypd", "police"), ("arrest", "police"),
    ("medical", "medical"), ("injur", "medical"), ("ems", "medical"), ("sick", "medical"),
    ("flood", "hazard"), ("hazard", "hazard"), ("track fire", "hazard"), ("debris", "hazard"),
]


def _incident_type(text: str) -> str:
    low = (text or "").lower()
    for needle, kind in _INCIDENT_TYPE_KEYWORDS:
        if needle in low:
            return kind
    return "incident"


def _loc_bucket(lat: float, lng: float) -> tuple:
    # ~0.7 mi cells -- coarse enough that small GPS jitter reuses the cache.
    return (round(lat, 2), round(lng, 2))


def _nearby_incident_markers(incidents: list, meta_by_name: dict) -> list[dict]:
    """Convert raw Grok incidents to the frontend LiveFeedIncident shape, placing
    each at its station's coordinates. Incidents whose station is not among the
    scanned nearby stops are dropped (no coordinate to anchor the marker)."""
    markers = []
    now = int(time.time())
    for index, inc in enumerate(incidents or []):
        if not isinstance(inc, dict):
            continue
        meta = meta_by_name.get(inc.get("nearby_station"))
        if not meta or meta["lat"] is None or meta["lng"] is None:
            continue
        description = inc.get("description") or "Incident reported nearby."
        detail = " - ".join(p for p in (inc.get("location"), inc.get("source")) if p)
        markers.append({
            "id": f"nearby-incident-{index}",
            "type": _incident_type(f"{description} {inc.get('location', '')}"),
            "lat": meta["lat"],
            "lng": meta["lng"],
            "title": description,
            "detail": detail,
            "severity": inc.get("severity") or "medium",
            "source": inc.get("source"),
            "station": inc.get("nearby_station"),
            "routeIds": meta.get("routes", []),
            "updated_at": now,
        })
    return markers


async def _refresh_nearby_incidents_bg(station_names: list[str], meta_by_name: dict, bucket: tuple):
    global _NEARBY_SCAN_INFLIGHT, _NEARBY_INCIDENTS, _NEARBY_SCAN_TS, _NEARBY_SCAN_BUCKET
    try:
        result = await get_incidents(station_names)
        incidents = result.get("incidents", []) if isinstance(result, dict) else []
        _NEARBY_INCIDENTS = _nearby_incident_markers(incidents, meta_by_name)
        _NEARBY_SCAN_TS = time.monotonic()
        _NEARBY_SCAN_BUCKET = bucket
        _vlog(f"[atlas_scan] nearby incidents: {len(_NEARBY_INCIDENTS)} near {len(station_names)} stops")
    except Exception as exc:
        print(f"[atlas_scan] nearby incident scan failed: {exc!r}")
    finally:
        _NEARBY_SCAN_INFLIGHT = False


def _serve_nearby_incidents(enriched_stops: list, lat: float, lng: float) -> list[dict]:
    """Cached, single-flight half-mile incident scan around the rider. Serves the
    last result immediately and refreshes in the background when stale or when
    the rider has moved to a new area -- never blocks the snapshot on Grok."""
    global _NEARBY_SCAN_INFLIGHT
    meta_by_name: dict[str, dict] = {}
    for stop in enriched_stops or []:
        name = stop.get("stop_name")
        if name and name not in meta_by_name:
            meta_by_name[name] = {
                "lat": stop.get("stop_lat"),
                "lng": stop.get("stop_lon"),
                "routes": list(stop.get("route_ids", [])),
            }
    if not meta_by_name:
        return list(_NEARBY_INCIDENTS)

    bucket = _loc_bucket(lat, lng)
    moved = bucket != _NEARBY_SCAN_BUCKET
    stale = (time.monotonic() - _NEARBY_SCAN_TS) > _NEARBY_SCAN_TTL_S
    if not _NEARBY_SCAN_INFLIGHT and (moved or stale):
        _NEARBY_SCAN_INFLIGHT = True
        try:
            task = asyncio.create_task(
                _refresh_nearby_incidents_bg(list(meta_by_name.keys()), meta_by_name, bucket)
            )
            _NEARBY_SCAN_TASKS.add(task)
            task.add_done_callback(_NEARBY_SCAN_TASKS.discard)
        except Exception:
            _NEARBY_SCAN_INFLIGHT = False
    # While the rider has just moved to a new bucket, the cached incidents belong
    # to the old area -- withhold them until the refresh for this bucket lands.
    return [] if moved else list(_NEARBY_INCIDENTS)


async def _build_live_snapshot(
    gtfs, lat: float, lng: float, selected_route_ids: set[str] | None = None,
    atlas_scan: bool = False, emit=None,
):
    global _LAST_EMPTY_VEHICLE_LOG
    _t0 = time.monotonic()

    nearest_stops = find_nearest_stops(
        lat,
        lng,
        gtfs,
        NEARBY_ARRIVAL_STOP_LIMIT,
        radius_m=NEARBY_ARRIVAL_RADIUS_M,
    )
    if not nearest_stops:
        nearest_stops = find_nearest_stops(lat, lng, gtfs, 5)
    enriched_stops = []
    selected_route_ids = _normalize_route_ids(selected_route_ids)

    for stop in nearest_stops:
        routes = gtfs.get_route_ids_for_parent_stop(stop["stop_id"])
        enriched_stops.append({**stop, "route_ids": routes})

    nearest_stop = enriched_stops[0] if enriched_stops else None
    route_ids = {
        route_id
        for stop in enriched_stops
        for route_id in stop.get("route_ids", [])
    }
    nearby_child_stop_ids, nearby_stop_lookup = _build_nearby_stop_context(
        gtfs,
        enriched_stops,
    )

    feeds = await mta_feed.fetch_feeds(route_ids, "arrivals_nearest")

    parse_tasks = [asyncio.to_thread(mta_feed.parse_bytes, feed) for feed in feeds]
    parsed_lists = await asyncio.gather(*parse_tasks, return_exceptions = True)

    trip_updates = []
    for parsed in parsed_lists:
        if isinstance(parsed, Exception):
            print(f"[live_feed] parse_bytes failed: {parsed}")
            continue
        trip_updates.extend(parsed)

    now = int(time.time())
    arrivals = []
    for arrival in trip_updates:
        if not arrival.get("arrival_time") or arrival.get("arrival_time") < now - 60:
            continue
        stop_context = _arrival_stop_context(arrival, nearby_stop_lookup)
        if nearby_child_stop_ids and stop_context is None:
            continue
        record = dict(arrival)
        if stop_context:
            record["parent_stop_id"] = stop_context.get("stop_id")
            record["parent_stop_name"] = stop_context.get("stop_name")
            record["station_name"] = stop_context.get("stop_name")
            record["distance_m"] = stop_context.get("distance_m")
            record["stop_lat"] = stop_context.get("stop_lat")
            record["stop_lon"] = stop_context.get("stop_lon")
        arrivals.append(record)
    arrival_lookup = {
        key: arrival["arrival_time"]
        for arrival in trip_updates
        if arrival.get("arrival_time")
        for key in [_arrival_lookup_key(arrival.get("trip_id"), arrival.get("stop_id"))]
        if key
    }

    # Time-to-first-arrivals: everything above (nearest stops + the nearby
    # subway feed, served from the warm cache) is all the rider waits on for the
    # first paint. Measured here so production telemetry can report it.
    arrivals_ms = round((time.monotonic() - _t0) * 1000)

    # Progressive first paint: the nearby subway arrivals are ready now (and
    # served from the warm feed cache), so push them immediately instead of
    # making the rider wait on the slower network-wide vehicles/alerts/summary
    # and bus fan-out below. Only the FIRST snapshot for a location passes an
    # emit callback; periodic refreshes send the full snapshot in one message.
    if emit is not None:
        await emit({
            "nearest_stop": nearest_stop,
            "stops": enriched_stops,
            "arrivals": sorted(arrivals, key=lambda a: a.get("arrival_time") or 0)[:40],
            "alerts": [],
            "vehicles": [],
            "summary": None,
            "signals": None,
            "incidents": [],
            "updated_at": now,
            "degraded": False,
            "debug": {"partial": True, "arrivals_ms": arrivals_ms},
            "partial": True,
        })

    vehicle_route_ids = _expand_vehicle_route_scope(set(route_ids) | selected_route_ids)

    raw_alerts, vehicle_result, bus_result = await asyncio.gather(
        mta_feed.fetch_service_alerts(),
        mta_feed.get_all_subway_vehicle_positions(vehicle_route_ids, debug=True, include_stop_only=True),
        _safe_nearby_bus_arrivals(lat, lng),
    )
    bus_arrivals, bus_debug = bus_result
    arrivals.extend(bus_arrivals)
    arrivals.sort(key=lambda a: a.get("arrival_time") or 0)
    vehicles, vehicle_debug = vehicle_result
    parsed_alerts = mta_feed.parse_service_alerts(raw_alerts) if raw_alerts else []
    filtered_alerts = mta_feed.filter_alerts_for_routes(parsed_alerts, set(route_ids))
    summary, signals = await _build_live_network_summary_bundle(parsed_alerts, now)

    stop_ids = [v.get("stop_id") for v in vehicles if v.get("stop_id")]
    stop_locations = gtfs.get_stop_locations(stop_ids)
    trip_stop_context = gtfs.get_trip_stop_context([
        *(arrival.get("trip_id") for arrival in arrivals if arrival.get("trip_id")),
        *(v.get("trip_id") for v in vehicles if v.get("trip_id")),
    ])
    for arrival in arrivals:
        _attach_terminal_stop(arrival, trip_stop_context.get(arrival.get("trip_id") or ""))
    stop_fallback_count = 0
    segment_estimate_count = 0
    missing_stop_coord_count = 0
    for vehicle in vehicles:
        stop_id = vehicle.get("stop_id")
        trip_stops = trip_stop_context.get(vehicle.get("trip_id") or "")
        _attach_terminal_stop(vehicle, trip_stops)
        if (
            vehicle.get("position_source") != "vehicle_position"
            and trip_stops
            and _attach_trip_segment(vehicle, trip_stops, arrival_lookup, now)
        ):
            if vehicle.get("position_source") == "polyline_estimate":
                segment_estimate_count += 1
            else:
                stop_fallback_count += 1
        elif stop_id:
            stop_location = stop_locations.get(stop_id) or stop_locations.get(stop_id.rstrip("NS"))
            if stop_location:
                vehicle["stop_name"] = stop_location["stop_name"]
                if vehicle.get("lat") is None or vehicle.get("lng") is None:
                    vehicle["lat"] = stop_location["lat"]
                    vehicle["lng"] = stop_location["lng"]
                    vehicle["position_source"] = "stop_id"
                    stop_fallback_count += 1
            else:
                missing_stop_coord_count += 1
        vehicle["route_name"] = f"{vehicle.get('route_id', '')} train"

    vehicles = [
        vehicle
        for vehicle in vehicles
        if vehicle.get("lat") is not None
        and vehicle.get("lng") is not None
        and str(vehicle.get("route_id") or "").upper() in vehicle_route_ids
    ]
    vehicle_debug["stop_coordinate_fallbacks"] = stop_fallback_count
    vehicle_debug["segment_estimates"] = segment_estimate_count
    vehicle_debug["missing_stop_coordinates"] = missing_stop_coord_count
    vehicle_debug["final_markers_after_stop_fallback"] = len(vehicles)

    if feeds and not vehicles:
        log_now = time.monotonic()
        if log_now - _LAST_EMPTY_VEHICLE_LOG > 60:
            print(
                "[live_feed] MTA snapshot had no usable vehicle or stop coordinates "
                f"for scoped vehicle feeds. nearest_routes={sorted(route_ids)} "
                f"selected_routes={sorted(selected_route_ids)} "
                f"stop_fallbacks={stop_fallback_count} missing_stop_coords={missing_stop_coord_count}"
            )
            _LAST_EMPTY_VEHICLE_LOG = log_now

    return {
        "nearest_stop": nearest_stop,
        "stops": enriched_stops,
        "arrivals": arrivals[:40],
        "alerts": filtered_alerts,
        "vehicles": vehicles,
        "summary": summary,
        "signals": signals,
        # ATLAS scans the same half-mile radius as nearby transit, but only when
        # the rider has the scan toggled on (it is a slow, paid Grok call).
        "incidents": _serve_nearby_incidents(enriched_stops, lat, lng) if atlas_scan else [],
        "updated_at": now,
        "degraded": len(feeds) == 0 and len(route_ids) > 0,
        "debug": {
            "route_ids": sorted(route_ids),
            "nearest_route_ids": sorted(nearest_stop.get("route_ids", []) if nearest_stop else []),
            "nearby_route_ids": sorted(route_ids),
            "selected_route_ids": sorted(selected_route_ids),
            "vehicle_route_ids": sorted(vehicle_route_ids),
            "arrival_radius_m": NEARBY_ARRIVAL_RADIUS_M,
            "nearby_stop_count": len(enriched_stops),
            "nearby_child_stop_count": len(nearby_child_stop_ids),
            "bus_arrivals_supported": bool(bus_debug.get("bus_arrivals_supported")),
            "nearby_bus_stop_count": int(bus_debug.get("nearby_bus_stop_count") or 0),
            "bus_arrival_count": int(bus_debug.get("bus_arrival_count") or 0),
            "bus_stop_monitoring_failures": int(bus_debug.get("bus_stop_monitoring_failures") or 0),
            "bus_arrivals_reason": bus_debug.get("reason"),
            "feed_count": len(feeds),
            "vehicle_count": len(vehicles),
            "vehicle_scope": "nearest_plus_selected",
            "vehicle_parse": vehicle_debug,
            "arrivals_ms": arrivals_ms,
            "build_ms": round((time.monotonic() - _t0) * 1000),
        },
    }


@router.get("/api/vehicles")
async def vehicles(route_ids: str | None = None):
    try:
        route_filter = {r.strip().upper() for r in route_ids.split(",")} if route_ids else None
        positions = await mta_feed.get_all_subway_vehicle_positions(route_filter)
    except Exception as exc:
        import traceback
        print(f"[vehicles] UNHANDLED ERROR:\n{traceback.format_exc()}")
        return JSONResponse(
            {"vehicles": [], "updated_at": int(time.time()), "error": "vehicles temporarily unavailable"},
            status_code=503,
        )
    return JSONResponse(
        {"vehicles": positions, "updated_at": int(time.time())}
    )


@ws_router.websocket("/ws/service-alerts")
async def service_alerts_socket(websocket: WebSocket):
    global _WS_CONNECTION_COUNTER
    _WS_CONNECTION_COUNTER += 1
    connection_id = _WS_CONNECTION_COUNTER

    ticket = websocket.query_params.get("ticket", "")
    # Fail closed: reject unless the ticket carries a valid, unexpired signature
    # minted by the Next /api/ws-ticket route (APP_KEY never reaches the client).
    if not _verify_ws_ticket(ticket, websocket.url.path):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    _vlog(f"[ws_service_alerts:{connection_id}] accepted")
    previous_signatures: dict[str, str] = {}
    sent_snapshot = False
    stream_interval = 60

    try:
        while True:
            try:
                payload = await _service_alerts_payload(
                    getattr(websocket.app.state, "gtfs", None),
                )
                current_signatures = _service_alert_signatures(payload.get("alerts", []))
                if not sent_snapshot:
                    message = {
                        "type": "SERVICE_SNAPSHOT",
                        "data": payload,
                        "changed_alert_ids": [],
                    }
                    sent_snapshot = True
                elif current_signatures != previous_signatures:
                    changed_alert_ids = [
                        alert_id
                        for alert_id, signature in current_signatures.items()
                        if previous_signatures.get(alert_id) != signature
                    ]
                    message = {
                        "type": "SERVICE_UPDATE",
                        "data": payload,
                        "changed_alert_ids": changed_alert_ids,
                    }
                else:
                    message = {
                        "type": "SERVICE_HEARTBEAT",
                        "updated_at": payload.get("updated_at"),
                    }

                previous_signatures = current_signatures
                sent = await _send_json_safe(websocket, message)
                if not sent:
                    print(f"[ws_service_alerts:{connection_id}] client closed before send")
                    return
                await asyncio.sleep(stream_interval)
            except Exception as exc:
                if isinstance(exc, WebSocketDisconnect):
                    return
                print(f"[ws_service_alerts] stream failed: {type(exc).__name__}: {exc!r}")
                # Generic public message; the real exception is logged above only.
                sent = await _send_json_safe(
                    websocket,
                    {"type": "error", "message": "service alerts temporarily unavailable"},
                )
                if not sent:
                    return
                await asyncio.sleep(5)
    except WebSocketDisconnect:
        _vlog(f"[ws_service_alerts:{connection_id}] disconnected")
        return


@ws_router.websocket("/ws/live-feed")
async def live_feed_socket(websocket: WebSocket):
    global _WS_CONNECTION_COUNTER
    _WS_CONNECTION_COUNTER += 1
    connection_id = _WS_CONNECTION_COUNTER

    ticket = websocket.query_params.get("ticket", "")
    # Fail closed: reject unless the ticket carries a valid, unexpired signature
    # minted by the Next /api/ws-ticket route (APP_KEY never reaches the client).
    if not _verify_ws_ticket(ticket, websocket.url.path):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    _vlog(f"[ws_live_feed:{connection_id}] accepted")
    gtfs = getattr(websocket.app.state, "gtfs", None)
    if gtfs is None:
        await _send_json_safe(websocket, {"type": "error", "message": "GTFS not ready"})
        await websocket.close(code=1011)
        return

    location: tuple[float, float] | None = None
    selected_route_ids: set[str] = set()
    atlas_scan = False
    last_sent = 0.0
    # Fallback cadence only: normal pushes are driven by signal_realtime_refresh()
    # the moment fresh MTA data lands. This timeout is just the safety net if that
    # signal ever stalls.
    stream_interval = 30
    recv_task: "asyncio.Task | None" = None

    async def _emit_partial(partial: dict):
        await _send_json_safe(websocket, {"type": "snapshot", "data": partial})

    try:
        while True:
            if location is None:
                msg = await websocket.receive_json()
            else:
                # Push-driven: wake on whichever fires first -- a client message
                # (location/scope change), a realtime data refresh, or the
                # fallback timeout. The receive task is kept ALIVE across
                # iterations (never cancelled) so receive_json() is never
                # re-entered concurrently; only the cheap event wait is cancelled.
                if recv_task is None:
                    recv_task = asyncio.ensure_future(websocket.receive_json())
                refresh_task = asyncio.ensure_future(get_realtime_refresh_event().wait())
                done, _pending = await asyncio.wait(
                    {recv_task, refresh_task},
                    timeout=stream_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not refresh_task.done():
                    refresh_task.cancel()
                msg = None
                if recv_task in done:
                    finished, recv_task = recv_task, None
                    try:
                        msg = finished.result()
                    except Exception:
                        return

            if isinstance(msg, dict) and msg.get("type") == "location":
                lat = msg.get("lat")
                lng = msg.get("lng")
                if isinstance(msg.get("selected_route_ids"), list):
                    selected_route_ids = _normalize_route_ids(msg.get("selected_route_ids"))
                if "atlas_scan" in msg:
                    atlas_scan = bool(msg.get("atlas_scan"))
                    last_sent = 0
                if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                    location = (float(lat), float(lng))
                    last_sent = 0
                    _vlog(
                        f"[ws_live_feed:{connection_id}] location "
                        f"lat={location[0]:.5f} lng={location[1]:.5f} "
                        f"selected_routes={sorted(selected_route_ids)}"
                    )
            elif isinstance(msg, dict) and msg.get("type") == "vehicle_scope":
                selected_route_ids = _normalize_route_ids(msg.get("selected_route_ids"))
                last_sent = 0
                _vlog(
                    f"[ws_live_feed:{connection_id}] vehicle scope "
                    f"selected_routes={sorted(selected_route_ids)}"
                )

            if location is None:
                continue

            now = time.monotonic()
            if now - last_sent < 1:
                continue

            try:
                # First snapshot for a location streams arrivals first (a
                # partial), then the full snapshot; periodic refreshes send the
                # full snapshot only, so vehicles/alerts never flicker empty.
                snapshot = await _build_live_snapshot(
                    gtfs, location[0], location[1], selected_route_ids, atlas_scan,
                    emit=_emit_partial if last_sent == 0 else None,
                )
                sent = await _send_json_safe(websocket, {"type": "snapshot", "data": snapshot})
                if not sent:
                    print(f"[ws_live_feed:{connection_id}] client closed before snapshot send")
                    return
                debug = snapshot.get("debug", {})
                vehicle_parse = debug.get("vehicle_parse", {})
                _vlog(
                    f"[ws_live_feed:{connection_id}] sent snapshot "
                    f"nearest={snapshot.get('nearest_stop', {}).get('stop_name') if snapshot.get('nearest_stop') else None} "
                    f"arrivals={len(snapshot.get('arrivals', []))} "
                    f"bus_supported={debug.get('bus_arrivals_supported')} "
                    f"bus_stops={debug.get('nearby_bus_stop_count')} "
                    f"bus_arrivals={debug.get('bus_arrival_count')} "
                    f"bus_reason={debug.get('bus_arrivals_reason')} "
                    f"vehicles={len(snapshot.get('vehicles', []))} "
                    f"vehicle_entities={vehicle_parse.get('vehicle_entities')} "
                    f"with_position={vehicle_parse.get('vehicles_with_position')} "
                    f"without_position={vehicle_parse.get('vehicles_without_position')} "
                    f"segment_estimates={vehicle_parse.get('segment_estimates')} "
                    f"stop_fallbacks={vehicle_parse.get('stop_coordinate_fallbacks')} "
                    f"vehicle_routes={debug.get('vehicle_route_ids')} "
                    f"degraded={snapshot.get('degraded')}"
                )
                last_sent = time.monotonic()
            except Exception as exc:
                if isinstance(exc, WebSocketDisconnect):
                    return
                print(f"[ws_live_feed] snapshot failed: {type(exc).__name__}: {exc!r}")
                # Generic public message; the real exception is logged above only.
                sent = await _send_json_safe(
                    websocket,
                    {"type": "error", "message": "live feed temporarily unavailable"},
                )
                if not sent:
                    print(f"[ws_live_feed:{connection_id}] client closed before error send")
                    return
                await asyncio.sleep(5)
    except WebSocketDisconnect:
        _vlog(f"[ws_live_feed:{connection_id}] disconnected")
        return
    finally:
        # Never leak the in-flight receive task when the socket closes.
        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
