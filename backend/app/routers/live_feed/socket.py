"""WebSocket message validation and streaming coordination for live feed."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from app.services.mta.bus_updates import BusUpdate, BusUpdateData, BusUpdateEvent


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
    guard: Callable[
        [object, asyncio.Event, asyncio.Event, asyncio.Task],
        Awaitable[None],
    ]
    receive: Callable[[WebSocket], Awaitable[dict]]
    send: Callable[[WebSocket, dict], Awaitable[bool]]
    refresh_event: Callable[[], asyncio.Event]
    service_payload: Callable[[object], Awaitable[dict]]
    alert_signatures: Callable[[list[dict]], dict[str, str]]
    snapshot: Callable[..., Awaitable[dict]]
    bus_update: Callable[[float, float], Awaitable[BusUpdate]]
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


async def close_socket_safe(
    websocket: WebSocket,
    code: int,
    disconnect_error: type[Exception],
) -> None:
    try:
        await websocket.close(code=code)
    except (disconnect_error, RuntimeError):
        pass


async def cancel_and_await(*tasks: asyncio.Task | None) -> None:
    pending = [task for task in tasks if task is not None and not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def receive_bounded_json(
    websocket: WebSocket,
    max_bytes: int,
    max_route_ids: int,
) -> dict:
    frame = await websocket.receive()
    if frame.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(code=int(frame.get("code") or 1000))
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
    lease: object,
    stopped: asyncio.Event,
    lease_failed: asyncio.Event,
    owner: asyncio.Task,
    *,
    admission: LeaseRefresher,
    interval_seconds: float,
) -> None:
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval_seconds)
            return
        except asyncio.TimeoutError:
            pass
        if not await admission.refresh(lease):
            lease_failed.set()
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
    lease_failed = asyncio.Event()
    guard = asyncio.create_task(
        deps.guard(lease, stopped, lease_failed, asyncio.current_task())
    )
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
        await cancel_and_await(guard)
        if lease_failed.is_set():
            await close_socket_safe(websocket, 1013, deps.disconnect_error)
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
    stopped = asyncio.Event()
    lease_failed = asyncio.Event()
    guard = asyncio.create_task(
        deps.guard(lease, stopped, lease_failed, asyncio.current_task())
    )
    close_code: int | None = None
    location = None
    selected_route_ids: set[str] = set()
    last_sent = 0.0
    recv_task = None
    bus_task: asyncio.Task[BusUpdate] | None = None
    bus_task_generation: int | None = None
    location_generation = 0
    refresh_signal: asyncio.Event | None = None

    async def send_bus_update(task: asyncio.Task[BusUpdate], generation: int) -> bool:
        try:
            update = task.result()
        except asyncio.CancelledError:
            return True
        except Exception as exc:
            update: BusUpdate = {
                "arrivals": [],
                "fetched_at": int(time.time()),
                "status": "unavailable",
                "debug": {"reason": type(exc).__name__},
            }
        if generation != location_generation:
            return True
        status = update.get("status") if isinstance(update, dict) else "unavailable"
        status = status if status in {"ready", "cached", "unavailable"} else "unavailable"
        arrivals = update.get("arrivals") if isinstance(update, dict) else []
        fetched_at = update.get("fetched_at") if isinstance(update, dict) else None
        payload: BusUpdateData = {
            "generation": generation,
            "arrivals": arrivals if isinstance(arrivals, list) else [],
            "fetched_at": fetched_at if isinstance(fetched_at, int) else int(time.time()),
            "status": status,
        }
        event: BusUpdateEvent = {"type": "bus_update", "data": payload}
        return await deps.send(websocket, event)

    try:
        gtfs = getattr(websocket.app.state, "gtfs", None)
        if gtfs is None:
            await deps.send(websocket, {"type": "error", "message": "GTFS not ready"})
            close_code = 1011
            return
        while True:
            finished_bus_task: asyncio.Task[BusUpdate] | None = None
            finished_bus_generation: int | None = None
            network_refreshed = False
            if location is None:
                try:
                    message = await deps.receive(websocket)
                except ValueError:
                    close_code = 1008
                    return
            else:
                if recv_task is None:
                    recv_task = asyncio.ensure_future(deps.receive(websocket))
                if refresh_signal is None:
                    refresh_signal = deps.refresh_event()
                refresh_task = asyncio.ensure_future(refresh_signal.wait())
                waiting = {recv_task, refresh_task}
                if bus_task is not None:
                    waiting.add(bus_task)
                done, _ = await asyncio.wait(
                    waiting,
                    timeout=30,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not refresh_task.done():
                    await cancel_and_await(refresh_task)
                message = None
                if bus_task is not None and bus_task in done:
                    finished_bus_task, bus_task = bus_task, None
                    finished_bus_generation, bus_task_generation = bus_task_generation, None
                if refresh_task in done:
                    network_refreshed = True
                    refresh_signal = None
                if recv_task in done:
                    finished, recv_task = recv_task, None
                    try:
                        message = finished.result()
                    except ValueError:
                        close_code = 1008
                        return
                    except Exception:
                        return
            if isinstance(message, dict) and message.get("type") == "location":
                if isinstance(message.get("selected_route_ids"), list):
                    selected_route_ids = deps.normalize(message.get("selected_route_ids"))
                lat = message.get("lat")
                lng = message.get("lng")
                if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                    await cancel_and_await(bus_task)
                    bus_task = None
                    bus_task_generation = None
                    location = (float(lat), float(lng))
                    location_generation += 1
                    last_sent = 0
                    deps.vlog(deps.location_log(connection_id, selected_route_ids))
            elif isinstance(message, dict) and message.get("type") == "vehicle_scope":
                selected_route_ids = deps.normalize(message.get("selected_route_ids"))
                last_sent = 0
                deps.vlog(
                    f"[ws_live_feed:{connection_id}] vehicle scope "
                    f"selected_routes={sorted(selected_route_ids)}"
                )
            if finished_bus_task is not None:
                # Process a same-tick location frame first. Otherwise a bus
                # result completed for the old location could be emitted
                # immediately before we advance the rider generation.
                generation = (
                    finished_bus_generation
                    if finished_bus_generation is not None
                    else location_generation
                )
                if not await send_bus_update(finished_bus_task, generation):
                    return
                if message is None:
                    continue
            if location is None:
                continue
            since_last_snapshot = time.monotonic() - last_sent
            if since_last_snapshot < 1:
                if not network_refreshed:
                    continue
                await asyncio.sleep(1 - since_last_snapshot)
            try:
                # Capture the generation signal before reading the snapshot.
                # If publication races with projection, the old signal is set
                # and the socket immediately follows with the newer generation.
                if refresh_signal is None:
                    refresh_signal = deps.refresh_event()
                snapshot = await deps.snapshot(
                    gtfs,
                    location[0],
                    location[1],
                    selected_route_ids,
                )
                snapshot["bus_generation"] = location_generation
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
                if snapshot.get("bus_status") != "cached" and bus_task is None:
                    bus_task = asyncio.create_task(
                        deps.bus_update(location[0], location[1]),
                        name="ws-live-feed-bus-update",
                    )
                    bus_task_generation = location_generation
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
        stopped.set()
        await cancel_and_await(recv_task, bus_task, guard)
        if lease_failed.is_set():
            close_code = 1013
        if close_code is not None:
            await close_socket_safe(websocket, close_code, deps.disconnect_error)
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
        await close_socket_safe(
            websocket,
            1013 if unavailable else 1008,
            deps.disconnect_error,
        )
        return None
    try:
        return await deps.acquire(principal, "ws")
    except deps.admission_denied as exc:
        await close_socket_safe(
            websocket,
            1013 if exc.status_code == 503 else 1008,
            deps.disconnect_error,
        )
        return None
