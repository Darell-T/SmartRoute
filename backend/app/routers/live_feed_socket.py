"""WebSocket message validation and streaming coordination for live feed."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from fastapi import WebSocket


SERVICE_ALERT_REFRESH_INTERVAL_S = 60


class LeaseRefresher(Protocol):
    async def refresh(self, lease: object) -> bool: ...


@dataclass(frozen=True)
class LiveFeedSocketDependencies:
    """Facade-owned bindings captured at socket invocation time."""

    disconnect_error: type[Exception]
    admission_denied: type[Exception]
    acquire: Callable[[str, str], Awaitable[object]]
    release: Callable[[object], Awaitable[None]]
    verify: Callable[[str, str], Awaitable[tuple[str | None, bool]]]
    guard: Callable[[WebSocket, object, asyncio.Event, asyncio.Task], Awaitable[None]]
    receive: Callable[[WebSocket], Awaitable[dict]]
    send: Callable[[WebSocket, dict], Awaitable[bool]]
    refresh_event: Callable[[], asyncio.Event]
    service_payload: Callable[[object], Awaitable[dict]]
    alert_signatures: Callable[[list[dict]], dict[str, str]]
    snapshot: Callable[..., Awaitable[dict]]
    normalize: Callable[[object], set[str]]
    location_log: Callable[[int, set[str]], str]
    failure_log: Callable[[str, Exception], str]
    vlog: Callable[[str], None]


async def send_json_safe(
    websocket: WebSocket,
    payload: dict,
    disconnect_error: type[Exception],
) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except (disconnect_error, RuntimeError):
        return False


async def receive_bounded_json(
    websocket: WebSocket,
    max_bytes: int,
    max_route_ids: int,
) -> dict:
    frame = await websocket.receive()
    raw = frame.get("text") if isinstance(frame.get("text"), str) else frame.get("bytes")
    if not isinstance(raw, (str, bytes)):
        raise ValueError("missing websocket payload")
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > max_bytes:
        raise ValueError("websocket payload too large")
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed websocket JSON") from exc
    if not isinstance(message, dict):
        raise ValueError("websocket message must be an object")
    if message.get("type") == "location":
        if set(message) - {"type", "lat", "lng", "selected_route_ids"}:
            raise ValueError("unexpected location fields")
        if not all(
            isinstance(message.get(key), (int, float))
            and not isinstance(message.get(key), bool)
            and math.isfinite(float(message[key]))
            for key in ("lat", "lng")
        ):
            raise ValueError("invalid location")
        if not (
            40.2 <= float(message["lat"]) <= 41.2
            and -74.6 <= float(message["lng"]) <= -73.2
        ):
            raise ValueError("location outside service area")
    elif message.get("type") == "vehicle_scope":
        if set(message) - {"type", "selected_route_ids"}:
            raise ValueError("unexpected scope fields")
    else:
        raise ValueError("unsupported websocket message")
    selected = message.get("selected_route_ids")
    if selected is not None and (
        not isinstance(selected, list)
        or len(selected) > max_route_ids
        or any(
            not isinstance(route, str) or not route.strip() or len(route) > 12
            for route in selected
        )
    ):
        raise ValueError("invalid route selection")
    return message


async def wait_for_client_disconnect(
    websocket: WebSocket,
    timeout_seconds: float,
) -> bool:
    """Notice closed alert sockets while waiting for the next refresh."""
    try:
        await asyncio.wait_for(websocket.receive(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False
    except RuntimeError:
        return True
    return True


async def guard_lease(
    websocket: WebSocket,
    lease: object,
    stopped: asyncio.Event,
    owner: asyncio.Task,
    *,
    admission: LeaseRefresher,
    interval_seconds: float,
    disconnect_error: type[Exception],
) -> None:
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval_seconds)
            return
        except asyncio.TimeoutError:
            pass
        if not await admission.refresh(lease):
            try:
                await websocket.close(code=1013)
            except (disconnect_error, RuntimeError):
                pass
            finally:
                owner.cancel()
            return


async def stream_service_alerts(
    websocket: WebSocket,
    connection_id: int,
    deps: LiveFeedSocketDependencies,
) -> None:
    lease = await _admit_socket(websocket, deps)
    if lease is None:
        return
    await websocket.accept()
    stopped = asyncio.Event()
    guard = asyncio.create_task(deps.guard(websocket, lease, stopped, asyncio.current_task()))
    previous: dict[str, str] = {}
    sent_snapshot = False
    deps.vlog(f"[ws_service_alerts:{connection_id}] accepted")
    try:
        while True:
            try:
                payload = await deps.service_payload(getattr(websocket.app.state, "gtfs", None))
                signatures = deps.alert_signatures(payload.get("alerts", []))
                if not sent_snapshot:
                    message = {"type": "SERVICE_SNAPSHOT", "data": payload, "changed_alert_ids": []}
                    sent_snapshot = True
                elif signatures != previous:
                    changed_alert_ids = [
                        key
                        for key, value in signatures.items()
                        if previous.get(key) != value
                    ]
                    message = {
                        "type": "SERVICE_UPDATE",
                        "data": payload,
                        "changed_alert_ids": changed_alert_ids,
                    }
                else:
                    message = {"type": "SERVICE_HEARTBEAT", "updated_at": payload.get("updated_at")}
                previous = signatures
                if not await deps.send(websocket, message):
                    print(f"[ws_service_alerts:{connection_id}] client closed before send")
                    return
                if await wait_for_client_disconnect(
                    websocket,
                    SERVICE_ALERT_REFRESH_INTERVAL_S,
                ):
                    return
            except Exception as exc:
                if isinstance(exc, deps.disconnect_error):
                    return
                print(deps.failure_log("ws_service_alerts", exc))
                if not await deps.send(
                    websocket,
                    {
                        "type": "error",
                        "message": "service alerts temporarily unavailable",
                    },
                ):
                    return
                await asyncio.sleep(5)
    except deps.disconnect_error:
        deps.vlog(f"[ws_service_alerts:{connection_id}] disconnected")
        return
    except asyncio.CancelledError:
        return
    finally:
        stopped.set()
        guard.cancel()
        try:
            await guard
        except asyncio.CancelledError:
            pass
        await deps.release(lease)


async def stream_live_feed(
    websocket: WebSocket,
    connection_id: int,
    deps: LiveFeedSocketDependencies,
) -> None:
    lease = await _admit_socket(websocket, deps)
    if lease is None:
        return
    await websocket.accept()
    deps.vlog(f"[ws_live_feed:{connection_id}] accepted")
    gtfs = getattr(websocket.app.state, "gtfs", None)
    if gtfs is None:
        await deps.send(websocket, {"type": "error", "message": "GTFS not ready"})
        await websocket.close(code=1011)
        await deps.release(lease)
        return
    stopped = asyncio.Event()
    guard = asyncio.create_task(deps.guard(websocket, lease, stopped, asyncio.current_task()))
    location = None
    selected_route_ids: set[str] = set()
    last_sent = 0.0
    recv_task = None

    async def emit_partial(partial: dict) -> None:
        await deps.send(websocket, {"type": "snapshot", "data": partial})

    try:
        while True:
            if location is None:
                try:
                    message = await deps.receive(websocket)
                except ValueError:
                    await websocket.close(code=1008)
                    return
            else:
                if recv_task is None:
                    recv_task = asyncio.ensure_future(deps.receive(websocket))
                refresh_task = asyncio.ensure_future(deps.refresh_event().wait())
                done, _ = await asyncio.wait(
                    {recv_task, refresh_task},
                    timeout=30,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not refresh_task.done():
                    refresh_task.cancel()
                message = None
                if recv_task in done:
                    finished, recv_task = recv_task, None
                    try:
                        message = finished.result()
                    except ValueError:
                        await websocket.close(code=1008)
                        return
                    except Exception:
                        return
            if isinstance(message, dict) and message.get("type") == "location":
                if isinstance(message.get("selected_route_ids"), list):
                    selected_route_ids = deps.normalize(message.get("selected_route_ids"))
                lat = message.get("lat")
                lng = message.get("lng")
                if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                    location = (float(lat), float(lng))
                    last_sent = 0
                    deps.vlog(deps.location_log(connection_id, selected_route_ids))
            elif isinstance(message, dict) and message.get("type") == "vehicle_scope":
                selected_route_ids = deps.normalize(message.get("selected_route_ids"))
                last_sent = 0
                deps.vlog(
                    f"[ws_live_feed:{connection_id}] vehicle scope "
                    f"selected_routes={sorted(selected_route_ids)}"
                )
            if location is None or time.monotonic() - last_sent < 1:
                continue
            try:
                snapshot = await deps.snapshot(
                    gtfs,
                    location[0],
                    location[1],
                    selected_route_ids,
                    emit=emit_partial if last_sent == 0 else None,
                )
                if not await deps.send(websocket, {"type": "snapshot", "data": snapshot}):
                    print(f"[ws_live_feed:{connection_id}] client closed before snapshot send")
                    return
                debug = snapshot.get("debug", {})
                vehicle_parse = debug.get("vehicle_parse", {})
                nearest_stop_name = (
                    snapshot.get("nearest_stop", {}).get("stop_name")
                    if snapshot.get("nearest_stop")
                    else None
                )
                deps.vlog(
                    f"[ws_live_feed:{connection_id}] sent snapshot "
                    f"nearest={nearest_stop_name} "
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
                if isinstance(exc, deps.disconnect_error):
                    return
                print(deps.failure_log("ws_live_feed", exc))
                if not await deps.send(
                    websocket,
                    {"type": "error", "message": "live feed temporarily unavailable"},
                ):
                    print(f"[ws_live_feed:{connection_id}] client closed before error send")
                    return
                await asyncio.sleep(5)
    except deps.disconnect_error:
        deps.vlog(f"[ws_live_feed:{connection_id}] disconnected")
        return
    except asyncio.CancelledError:
        return
    finally:
        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
        stopped.set()
        guard.cancel()
        try:
            await guard
        except asyncio.CancelledError:
            pass
        await deps.release(lease)


async def _admit_socket(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
) -> object | None:
    principal, unavailable = await deps.verify(
        websocket.query_params.get("ticket", ""),
        websocket.url.path,
    )
    if principal is None:
        await websocket.close(code=1013 if unavailable else 1008)
        return None
    try:
        return await deps.acquire(principal, "ws")
    except deps.admission_denied as exc:
        await websocket.close(code=1013 if exc.status_code == 503 else 1008)
        return None
