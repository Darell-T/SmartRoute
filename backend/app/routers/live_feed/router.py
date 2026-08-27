import asyncio
import hashlib
import json
import math
import os
import time
from contextlib import suppress

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.routers.live_feed import socket as _live_feed_socket
from app.routers.live_feed import ticket as _live_feed_ticket
from app.services import admission
from app.services.live_feed import snapshot as _live_feed_snapshot
from app.services.live_feed.network_snapshot import network_snapshot_store
from app.services.mta import realtime as mta_realtime

router = APIRouter()
ws_router = APIRouter()
_background_bus_tasks: set[asyncio.Task] = set()


def _remember_background_task(task: asyncio.Task) -> None:
    _background_bus_tasks.add(task)
    task.add_done_callback(_background_bus_tasks.discard)


async def close_background_bus_tasks() -> None:
    tasks = list(_background_bus_tasks)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task


async def _verify_ws_ticket(ticket: str, path: str) -> tuple[str | None, bool]:
    """Validate a short-lived ticket minted by the Next /api/ws-ticket route.

    The ticket is ``exp.nonce.principal.signature``. Its nonce is atomically
    consumed before accept, preventing replays across backend instances.
    """
    return await _live_feed_ticket.verify_ticket(
        ticket,
        path,
        app_key=os.getenv("APP_KEY", ""),
        now=time.time,
        admission=admission,
    )


_WS_CONNECTION_COUNTER = 0
MAX_WS_MESSAGE_BYTES = 4 * 1024
MAX_SELECTED_ROUTE_IDS = 12
LEASE_GUARD_INTERVAL_S = max(1, admission.WEBSOCKET_LEASE_TTL_S // 3)


def _vlog(message: str) -> None:
    if os.getenv("BACKEND_VERBOSE_LOGS", "0") == "1":
        print(message)


async def _send_json_safe(websocket: WebSocket, payload: dict) -> bool:
    return await _live_feed_socket.send_json_safe(
        websocket,
        payload,
        WebSocketDisconnect,
    )


async def _receive_bounded_ws_json(websocket: WebSocket) -> dict:
    """Read one complete text/bytes frame before parsing untrusted JSON."""
    return await _live_feed_socket.receive_bounded_json(
        websocket,
        MAX_WS_MESSAGE_BYTES,
        MAX_SELECTED_ROUTE_IDS,
    )


async def _guard_socket_lease(
    lease: admission.AdmissionLease,
    stopped: asyncio.Event,
    lease_failed: asyncio.Event,
    owner: asyncio.Task,
) -> None:
    """Refresh independent of rider frames; never reads from the socket."""
    await _live_feed_socket.guard_lease(
        lease,
        stopped,
        lease_failed,
        owner,
        admission=admission,
        interval_seconds=LEASE_GUARD_INTERVAL_S,
    )


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
    except Exception:
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
    try:
        return JSONResponse(await _service_alerts_payload(gtfs))
    except Exception as exc:
        print(f"[service_alerts] snapshot unavailable: {type(exc).__name__}")
        return JSONResponse(
            {
                "alerts": [],
                "updated_at": int(time.time()),
                "active_count": 0,
                "affected_route_count": 0,
                "source": "mta",
                "error": "service alerts temporarily unavailable",
            },
            status_code=503,
        )


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
    network = await network_snapshot_store.get_or_refresh()
    parsed_alerts = [dict(alert) for alert in network.service_alerts]
    _attach_alert_stop_names(parsed_alerts, gtfs)
    affected_routes = {
        str(route_id).strip().upper()
        for alert in parsed_alerts
        for route_id in alert.get("route_ids", [])
        if str(route_id).strip()
    }

    return {
        "alerts": parsed_alerts,
        "updated_at": network.updated_at,
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
    snapshot = await _live_feed_snapshot.build_live_snapshot(
        gtfs, payload.lat, payload.lng
    )
    # The REST contract is primary subway data only. Warm an optional BusTime
    # result for the next request without adding its latency to this response.
    bus_refresh = asyncio.create_task(
        mta_realtime.fetch_nearby_bus_update(
            payload.lat,
            payload.lng,
            radius_m=_live_feed_snapshot.NEARBY_ARRIVAL_RADIUS_M,
        ),
        name="live-feed-rest-bus-refresh",
    )
    _remember_background_task(bus_refresh)
    return JSONResponse(
        {
            "nearest_stop": snapshot["nearest_stop"],
            "stops": snapshot["stops"],
            "arrivals": snapshot["arrivals"],
            "alerts": snapshot["alerts"],
            "vehicles": snapshot["vehicles"],
            "signals": snapshot["signals"],
            "bus_status": snapshot["bus_status"],
            "updated_at": snapshot["updated_at"],
            "degraded": snapshot["degraded"],
            "debug": snapshot["debug"],
        }
    )

@router.get("/api/vehicles")
async def vehicles(route_ids: str | None = None):
    try:
        route_filter = {r.strip().upper() for r in route_ids.split(",")} if route_ids else None
        network = await network_snapshot_store.get_or_refresh()
        positions = [
            dict(vehicle)
            for vehicle in network.vehicles
            if route_filter is None
            or str(vehicle.get("route_id") or "").upper() in route_filter
        ]
    except Exception:
        import traceback
        print(f"[vehicles] UNHANDLED ERROR:\n{traceback.format_exc()}")
        return JSONResponse(
            {
                "vehicles": [],
                "updated_at": int(time.time()),
                "error": "vehicles temporarily unavailable",
            },
            status_code=503,
        )
    return JSONResponse(
        {"vehicles": positions, "updated_at": int(time.time())}
    )


@ws_router.websocket("/ws/service-alerts")
async def service_alerts_socket(websocket: WebSocket):
    return await _live_feed_socket.stream_service_alerts(
        websocket,
        _next_connection_id(),
        _socket_dependencies(),
    )


@ws_router.websocket("/ws/live-feed")
async def live_feed_socket(websocket: WebSocket):
    return await _live_feed_socket.stream_live_feed(
        websocket,
        _next_connection_id(),
        _socket_dependencies(),
    )


def _next_connection_id() -> int:
    global _WS_CONNECTION_COUNTER
    _WS_CONNECTION_COUNTER += 1
    return _WS_CONNECTION_COUNTER


def _socket_dependencies() -> _live_feed_socket.LiveFeedSocketDependencies:
    return _live_feed_socket.LiveFeedSocketDependencies(
        disconnect_error=WebSocketDisconnect,
        admission_denied=admission.AdmissionDenied,
        acquire=admission.acquire,
        release=admission.release,
        verify=_verify_ws_ticket,
        guard=_guard_socket_lease,
        receive=_receive_bounded_ws_json,
        send=_send_json_safe,
        refresh_event=network_snapshot_store.refresh_event,
        service_payload=_service_alerts_payload,
        alert_signatures=_service_alert_signatures,
        snapshot=_live_feed_snapshot.build_live_snapshot,
        bus_update=mta_realtime.fetch_nearby_bus_update,
        normalize=_live_feed_snapshot._normalize_route_ids,
        location_log=_live_feed_snapshot._location_verbose_log,
        failure_log=_live_feed_snapshot._socket_failure_log,
        vlog=_vlog,
    )
