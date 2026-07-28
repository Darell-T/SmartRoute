from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.utils.geo import find_nearest_stops
from app.services import mta_feed
from app.services.live_feed import vehicle_enrichment
from app.services.live_feed.log import _vlog
from app.services import admission
import asyncio
import hashlib
import hmac
import json
import math
import os
import re
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


async def _verify_ws_ticket(ticket: str, path: str) -> tuple[str | None, bool]:
    """Validate a short-lived ticket minted by the Next /api/ws-ticket route.

    The ticket is ``exp.nonce.principal.signature``. Its nonce is atomically
    consumed before accept, preventing replays across backend instances.
    """
    app_key = os.getenv("APP_KEY", "")
    if not app_key or not ticket or not path:
        return None, False
    if len(ticket) > 512:
        return None, False
    parts = ticket.split(".")
    if len(parts) != 4:
        return None, False
    exp_str, nonce, principal_id, sig = parts
    principal = f"v1.{principal_id}"
    if (not exp_str or len(exp_str) > 12 or not exp_str.isdigit() or not nonce
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce)
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", principal_id)
            or not re.fullmatch(r"[0-9a-f]{64}", sig)):
        return None, False
    try:
        exp = int(exp_str)
    except ValueError:
        return None, False
    now = int(time.time())
    if exp < now or exp > now + 120:
        return None, False
    expected = hmac.new(
        app_key.encode(),
        f"{exp_str}.{path}.{nonce}.{principal}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None, False
    try:
        admission.principal_from_request(principal)
    except admission.AdmissionDenied:
        return None, False
    nonce_result = await admission.consume_nonce(nonce, exp - now)
    if nonce_result == "unavailable":
        return None, True
    if nonce_result != "consumed":
        return None, False
    return principal, False


_LAST_EMPTY_VEHICLE_LOG = 0.0
_WS_CONNECTION_COUNTER = 0
NEARBY_ARRIVAL_RADIUS_M = 804.672
NEARBY_ARRIVAL_STOP_LIMIT = 32
MAX_WS_MESSAGE_BYTES = 4 * 1024
MAX_SELECTED_ROUTE_IDS = 12
LEASE_GUARD_INTERVAL_S = max(1, admission.LEASE_TTL_S // 3)
_SIGNAL_DISRUPTION_KEYWORDS = (
    "SUSPEND",
    "NO ",
    "SKIP",
    "BYPASS",
    "REROUT",
    "SLOW SPEED",
    "MAJOR DELAY",
    "PART SUSPEND",
)
_SIGNAL_CAUTION_KEYWORDS = (
    "DELAY",
    "SERVICE CHANGE",
    "PLANNED WORK",
    "LOCAL TO EXPRESS",
    "EXPRESS TO LOCAL",
    "SHUTTLE",
)


async def _send_json_safe(websocket: WebSocket, payload: dict) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


async def _receive_bounded_ws_json(websocket: WebSocket) -> dict:
    """Read one complete text/bytes frame before parsing untrusted JSON."""
    frame = await websocket.receive()
    raw = frame.get("text") if isinstance(frame.get("text"), str) else frame.get("bytes")
    if not isinstance(raw, (str, bytes)):
        raise ValueError("missing websocket payload")
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_WS_MESSAGE_BYTES:
        raise ValueError("websocket payload too large")
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed websocket JSON") from exc
    if not isinstance(message, dict):
        raise ValueError("websocket message must be an object")
    message_type = message.get("type")
    if message_type == "location":
        if set(message) - {"type", "lat", "lng", "selected_route_ids"}:
            raise ValueError("unexpected location fields")
        if not all(isinstance(message.get(key), (int, float)) and not isinstance(message.get(key), bool) and math.isfinite(float(message[key])) for key in ("lat", "lng")):
            raise ValueError("invalid location")
        if not (40.2 <= float(message["lat"]) <= 41.2 and -74.6 <= float(message["lng"]) <= -73.2):
            raise ValueError("location outside service area")
    elif message_type == "vehicle_scope":
        if set(message) - {"type", "selected_route_ids"}:
            raise ValueError("unexpected scope fields")
    else:
        raise ValueError("unsupported websocket message")
    selected = message.get("selected_route_ids")
    if selected is not None and (not isinstance(selected, list) or len(selected) > MAX_SELECTED_ROUTE_IDS or any(not isinstance(route, str) or not route.strip() or len(route) > 12 for route in selected)):
        raise ValueError("invalid route selection")
    return message


async def _guard_socket_lease(websocket: WebSocket, lease: admission.AdmissionLease, stopped: asyncio.Event, owner: asyncio.Task) -> None:
    """Refresh independent of rider frames; never reads from the socket."""
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=LEASE_GUARD_INTERVAL_S)
            return
        except asyncio.TimeoutError:
            pass
        if not await admission.refresh(lease):
            try:
                await websocket.close(code=1013)
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                owner.cancel()
            return


class LiveFeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float
    lng: float



def _live_feed_coordinates_are_valid(payload: LiveFeedRequest) -> bool:
    return (
        isinstance(payload.lat, (int, float)) and not isinstance(payload.lat, bool)
        and isinstance(payload.lng, (int, float)) and not isinstance(payload.lng, bool)
        and math.isfinite(payload.lat) and math.isfinite(payload.lng)
        and 40.2 <= payload.lat <= 41.2 and -74.6 <= payload.lng <= -73.2
    )

@router.post("/api/live-feed")
async def live_feed(request: Request, payload: LiveFeedRequest):
    if not _live_feed_coordinates_are_valid(payload):
        return JSONResponse({"error": "Invalid live feed request."}, status_code=400)
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
            "updated_at": snapshot["updated_at"],
            "degraded": snapshot["degraded"],
            "debug": snapshot["debug"],
        }
    )


def _normalize_route_ids(route_ids):
    return {
        str(route_id).strip().upper()
        for route_id in (route_ids or [])
        if str(route_id).strip()
    }


def _location_verbose_log(connection_id: int, selected_route_ids: set[str]) -> str:
    """Return location telemetry without rider-provided coordinates."""

    return (
        f"[ws_live_feed:{connection_id}] location "
        f"selected_routes={sorted(selected_route_ids)}"
    )


def _socket_failure_log(channel: str, exc: Exception) -> str:
    """Preserve failure classification without serializing provider details."""

    return f"[{channel}] failed error_type={type(exc).__name__}"


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


def _signal_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _alert_signal_severity(alert: dict) -> str:
    alert_text = f"{_signal_text(alert.get('header'))} {_signal_text(alert.get('description'))}".upper()
    if any(keyword in alert_text for keyword in _SIGNAL_DISRUPTION_KEYWORDS):
        return "disrupted"
    if any(keyword in alert_text for keyword in _SIGNAL_CAUTION_KEYWORDS):
        return "caution"
    return "caution"


def _derive_live_network_status(
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


def _build_live_signals(
    parsed_alerts: list[dict],
    vehicles: list[dict],
    vehicle_debug: dict,
    updated_at: int,
) -> dict:
    affected_routes = {
        str(route_id).strip().upper()
        for alert in parsed_alerts
        for route_id in (alert.get("route_ids") or [])
        if str(route_id).strip()
    }
    major_alert_count = sum(
        1 for alert in parsed_alerts if _alert_signal_severity(alert) == "disrupted"
    )
    stale_vehicles = [vehicle for vehicle in vehicles if vehicle.get("stale")]
    vehicle_entities = int(vehicle_debug.get("vehicle_entities") or len(vehicles))
    vehicles_without_position = int(vehicle_debug.get("vehicles_without_position") or 0)
    feed_failures = int(vehicle_debug.get("feed_failures") or 0)

    return {
        "network_status": _derive_live_network_status(
            active_alert_count=len(parsed_alerts),
            major_alert_count=major_alert_count,
            stale_count=len(stale_vehicles),
            feed_failures=feed_failures,
            vehicle_entities=vehicle_entities,
            vehicles_without_position=vehicles_without_position,
        ),
        "active_alert_count": len(parsed_alerts),
        "major_alert_count": major_alert_count,
        "affected_route_count": len(affected_routes),
        "tracked_vehicle_count": len(vehicles),
        "stale_vehicle_count": len(stale_vehicles),
        "routes_reporting_count": len({
            str(vehicle.get("route_id") or "").upper()
            for vehicle in vehicles
            if vehicle.get("route_id")
        }),
        "feed_failures": feed_failures,
        "vehicles_with_position": int(vehicle_debug.get("vehicles_with_position") or 0),
        "vehicles_without_position": vehicles_without_position,
        "updated_at": updated_at,
    }


async def _build_live_snapshot(
    gtfs, lat: float, lng: float, selected_route_ids: set[str] | None = None,
    emit=None,
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
        for key in [vehicle_enrichment._arrival_lookup_key(arrival.get("trip_id"), arrival.get("stop_id"))]
        if key
    }

    # Time-to-first-arrivals: everything above (nearest stops + the nearby
    # subway feed, served from the warm cache) is all the rider waits on for the
    # first paint. Measured here so production telemetry can report it.
    arrivals_ms = round((time.monotonic() - _t0) * 1000)

    # Progressive first paint: the nearby subway arrivals are ready now (and
    # served from the warm feed cache), so push them immediately instead of
    # making the rider wait on the slower vehicles/alerts/bus fan-out below.
    # Only the FIRST snapshot for a location passes an
    # emit callback; periodic refreshes send the full snapshot in one message.
    if emit is not None:
        await emit({
            "nearest_stop": nearest_stop,
            "stops": enriched_stops,
            "arrivals": sorted(arrivals, key=lambda a: a.get("arrival_time") or 0)[:40],
            "alerts": [],
            "vehicles": [],
            "signals": None,
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

    stop_ids = [v.get("stop_id") for v in vehicles if v.get("stop_id")]
    stop_locations = gtfs.get_stop_locations(stop_ids)
    trip_stop_context = gtfs.get_trip_stop_context([
        *(arrival.get("trip_id") for arrival in arrivals if arrival.get("trip_id")),
        *(v.get("trip_id") for v in vehicles if v.get("trip_id")),
    ])
    for arrival in arrivals:
        vehicle_enrichment._attach_terminal_stop(arrival, trip_stop_context.get(arrival.get("trip_id") or ""))
    stop_fallback_count = 0
    segment_estimate_count = 0
    missing_stop_coord_count = 0
    for vehicle in vehicles:
        stop_id = vehicle.get("stop_id")
        trip_stops = trip_stop_context.get(vehicle.get("trip_id") or "")
        vehicle_enrichment._attach_terminal_stop(vehicle, trip_stops)
        if (
            vehicle.get("position_source") != "vehicle_position"
            and trip_stops
            and vehicle_enrichment._attach_trip_segment(vehicle, trip_stops, arrival_lookup, now)
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
    signals = _build_live_signals(parsed_alerts, vehicles, vehicle_debug, now)

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
        "signals": signals,
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
    principal, ticket_store_unavailable = await _verify_ws_ticket(ticket, websocket.url.path)
    if principal is None:
        await websocket.close(code=1013 if ticket_store_unavailable else 1008)
        return
    try:
        lease = await admission.acquire(principal, "ws")
    except admission.AdmissionDenied as exc:
        await websocket.close(code=1013 if exc.status_code == 503 else 1008)
        return

    await websocket.accept()
    guard_stopped = asyncio.Event()
    guard_task = asyncio.create_task(_guard_socket_lease(websocket, lease, guard_stopped, asyncio.current_task()))
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
                print(_socket_failure_log("ws_service_alerts", exc))
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
    except asyncio.CancelledError:
        return
    finally:
        guard_stopped.set()
        guard_task.cancel()
        try:
            await guard_task
        except asyncio.CancelledError:
            pass
        await admission.release(lease)


@ws_router.websocket("/ws/live-feed")
async def live_feed_socket(websocket: WebSocket):
    global _WS_CONNECTION_COUNTER
    _WS_CONNECTION_COUNTER += 1
    connection_id = _WS_CONNECTION_COUNTER

    ticket = websocket.query_params.get("ticket", "")
    # Fail closed: reject unless the ticket carries a valid, unexpired signature
    # minted by the Next /api/ws-ticket route (APP_KEY never reaches the client).
    principal, ticket_store_unavailable = await _verify_ws_ticket(ticket, websocket.url.path)
    if principal is None:
        await websocket.close(code=1013 if ticket_store_unavailable else 1008)
        return
    try:
        lease = await admission.acquire(principal, "ws")
    except admission.AdmissionDenied as exc:
        await websocket.close(code=1013 if exc.status_code == 503 else 1008)
        return

    await websocket.accept()
    _vlog(f"[ws_live_feed:{connection_id}] accepted")
    gtfs = getattr(websocket.app.state, "gtfs", None)
    if gtfs is None:
        await _send_json_safe(websocket, {"type": "error", "message": "GTFS not ready"})
        await websocket.close(code=1011)
        await admission.release(lease)
        return

    guard_stopped = asyncio.Event()
    guard_task = asyncio.create_task(_guard_socket_lease(websocket, lease, guard_stopped, asyncio.current_task()))

    location: tuple[float, float] | None = None
    selected_route_ids: set[str] = set()
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
                try:
                    msg = await _receive_bounded_ws_json(websocket)
                except ValueError:
                    await websocket.close(code=1008)
                    return
            else:
                # Push-driven: wake on whichever fires first -- a client message
                # (location/scope change), a realtime data refresh, or the
                # fallback timeout. The receive task is kept ALIVE across
                # iterations (never cancelled) so receive_json() is never
                # re-entered concurrently; only the cheap event wait is cancelled.
                if recv_task is None:
                    recv_task = asyncio.ensure_future(_receive_bounded_ws_json(websocket))
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
                    except ValueError:
                        await websocket.close(code=1008)
                        return
                    except Exception:
                        return

            if isinstance(msg, dict) and msg.get("type") == "location":
                lat = msg.get("lat")
                lng = msg.get("lng")
                if isinstance(msg.get("selected_route_ids"), list):
                    selected_route_ids = _normalize_route_ids(msg.get("selected_route_ids"))
                if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                    location = (float(lat), float(lng))
                    last_sent = 0
                    _vlog(_location_verbose_log(connection_id, selected_route_ids))
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
                    gtfs, location[0], location[1], selected_route_ids,
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
                print(_socket_failure_log("ws_live_feed", exc))
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
    except asyncio.CancelledError:
        return
    finally:
        # Never leak the in-flight receive task when the socket closes.
        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
        guard_stopped.set()
        guard_task.cancel()
        try:
            await guard_task
        except asyncio.CancelledError:
            pass
        await admission.release(lease)
