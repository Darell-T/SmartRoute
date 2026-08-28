"""WebSocket message validation and streaming coordination for live feed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

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
    except (disconnect_error, RuntimeError):
        return False
    else:
        return True


async def close_socket_safe(
    websocket: WebSocket,
    code: int,
    disconnect_error: type[Exception],
) -> None:
    with contextlib.suppress(disconnect_error, RuntimeError):
        await websocket.close(code=code)


async def cancel_and_await(*tasks: asyncio.Task | None) -> None:
    pending = [task for task in tasks if task is not None and not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


_WS_MESSAGE_FIELDS = {
    "location": frozenset({"type", "lat", "lng", "selected_route_ids"}),
    "vehicle_scope": frozenset({"type", "selected_route_ids"}),
}


def _require_in_service_location(message: dict) -> None:
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
        raise TypeError("missing websocket payload")
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > max_bytes:
        raise ValueError("websocket payload too large")
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed websocket JSON") from exc
    if not isinstance(message, dict):
        raise TypeError("websocket message must be an object")
    allowed = _WS_MESSAGE_FIELDS.get(message.get("type"))
    if allowed is None:
        raise ValueError("unsupported websocket message")
    if set(message) - allowed:
        kind = "location" if message.get("type") == "location" else "scope"
        raise ValueError(f"unexpected {kind} fields")
    if message.get("type") == "location":
        _require_in_service_location(message)
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
    except TimeoutError:
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
        except TimeoutError:
            pass
        else:
            return
        if not await admission.refresh(lease):
            lease_failed.set()
            owner.cancel()
            return


def _next_service_alert_message(
    payload: dict,
    signatures: dict[str, str],
    previous: dict[str, str],
    sent_snapshot: bool,
) -> dict:
    if not sent_snapshot:
        return {"type": "SERVICE_SNAPSHOT", "data": payload, "changed_alert_ids": []}
    if signatures != previous:
        changed_alert_ids = [
            key for key, value in signatures.items() if previous.get(key) != value
        ]
        return {
            "type": "SERVICE_UPDATE",
            "data": payload,
            "changed_alert_ids": changed_alert_ids,
        }
    return {"type": "SERVICE_HEARTBEAT", "updated_at": payload.get("updated_at")}


async def _service_alert_tick(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    connection_id: int,
    previous: dict[str, str],
    sent_snapshot: bool,
) -> dict[str, str] | None:
    payload = await deps.service_payload(getattr(websocket.app.state, "gtfs", None))
    signatures = deps.alert_signatures(payload.get("alerts", []))
    message = _next_service_alert_message(
        payload, signatures, previous, sent_snapshot
    )
    if not await deps.send(websocket, message):
        print(f"[ws_service_alerts:{connection_id}] client closed before send")
        return None
    if await wait_for_client_disconnect(websocket, SERVICE_ALERT_REFRESH_INTERVAL_S):
        return None
    return signatures


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
                signatures = await _service_alert_tick(
                    websocket, deps, connection_id, previous, sent_snapshot
                )
                if signatures is None:
                    return
                previous = signatures
                sent_snapshot = True
            except deps.disconnect_error:
                return
            except Exception as exc:  # noqa: BLE001 provider faults keep the socket open
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


@dataclass
class _LiveFeedConn:
    location: tuple[float, float] | None = None
    selected_route_ids: set[str] = field(default_factory=set)
    last_sent: float = 0.0
    recv_task: asyncio.Future | None = None
    bus_task: asyncio.Task[BusUpdate] | None = None
    bus_task_generation: int | None = None
    location_generation: int = 0
    refresh_signal: asyncio.Event | None = None
    close_code: int | None = None


@dataclass
class _LiveFeedTurn:
    message: dict | None = None
    network_refreshed: bool = False
    finished_bus_task: asyncio.Task[BusUpdate] | None = None
    finished_bus_generation: int | None = None
    disconnected: bool = False


async def _emit_bus_update(
    deps: LiveFeedSocketDependencies,
    websocket: WebSocket,
    task: asyncio.Task[BusUpdate],
    generation: int,
    current_generation: int,
) -> bool:
    try:
        update = task.result()
    except asyncio.CancelledError:
        return True
    except Exception as exc:  # noqa: BLE001 bus update faults degrade to unavailable
        update = {
            "arrivals": [],
            "fetched_at": int(time.time()),
            "status": "unavailable",
            "debug": {"reason": type(exc).__name__},
        }
    if generation != current_generation:
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


async def _await_first_location(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
) -> _LiveFeedTurn:
    try:
        message = await deps.receive(websocket)
    except (ValueError, TypeError):
        conn.close_code = 1008
        return _LiveFeedTurn(disconnected=True)
    return _LiveFeedTurn(message=message)


async def _await_located_inputs(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
) -> _LiveFeedTurn:
    if conn.recv_task is None:
        conn.recv_task = asyncio.ensure_future(deps.receive(websocket))
    if conn.refresh_signal is None:
        conn.refresh_signal = deps.refresh_event()
    refresh_task = asyncio.ensure_future(conn.refresh_signal.wait())
    waiting = {conn.recv_task, refresh_task}
    if conn.bus_task is not None:
        waiting.add(conn.bus_task)
    done, _ = await asyncio.wait(
        waiting,
        timeout=30,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if not refresh_task.done():
        await cancel_and_await(refresh_task)
    turn = _LiveFeedTurn()
    if conn.bus_task in done:
        turn.finished_bus_task = conn.bus_task
        turn.finished_bus_generation = conn.bus_task_generation
        conn.bus_task = None
        conn.bus_task_generation = None
    if refresh_task in done:
        turn.network_refreshed = True
        conn.refresh_signal = None
    if conn.recv_task in done:
        _recv_located_message(conn, turn, deps.disconnect_error)
    return turn


async def _apply_live_feed_frame(
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
    connection_id: int,
    message: dict | None,
) -> None:
    if not isinstance(message, dict):
        return
    if message.get("type") == "location":
        if isinstance(message.get("selected_route_ids"), list):
            conn.selected_route_ids = deps.normalize(message.get("selected_route_ids"))
        lat = message.get("lat")
        lng = message.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            await cancel_and_await(conn.bus_task)
            conn.bus_task = None
            conn.bus_task_generation = None
            conn.location = (float(lat), float(lng))
            conn.location_generation += 1
            conn.last_sent = 0
            deps.vlog(deps.location_log(connection_id, conn.selected_route_ids))
        return
    if message.get("type") == "vehicle_scope":
        conn.selected_route_ids = deps.normalize(message.get("selected_route_ids"))
        conn.last_sent = 0
        deps.vlog(
            f"[ws_live_feed:{connection_id}] vehicle scope "
            f"selected_routes={sorted(conn.selected_route_ids)}"
        )


def _log_live_snapshot(
    deps: LiveFeedSocketDependencies, connection_id: int, snapshot: dict
) -> None:
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


async def _publish_live_snapshot(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
    connection_id: int,
    gtfs,
    network_refreshed: bool,
) -> bool:
    since_last_snapshot = time.monotonic() - conn.last_sent
    if since_last_snapshot < 1:
        if not network_refreshed:
            return True
        await asyncio.sleep(1 - since_last_snapshot)
    location = conn.location
    if location is None:
        return True
    try:
        return await _send_located_snapshot(
            websocket, deps, conn, connection_id, gtfs, location
        )
    except deps.disconnect_error:
        return False
    except Exception as exc:  # noqa: BLE001 parser faults must not drop the socket
        print(deps.failure_log("ws_live_feed", exc))
        if not await deps.send(
            websocket,
            {"type": "error", "message": "live feed temporarily unavailable"},
        ):
            print(f"[ws_live_feed:{connection_id}] client closed before error send")
            return False
        await asyncio.sleep(5)
    return True


async def _send_located_snapshot(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
    connection_id: int,
    gtfs,
    location: tuple[float, float],
) -> bool:
    # Capture the generation signal before reading the snapshot.
    # If publication races with projection, the old signal is set
    # and the socket immediately follows with the newer generation.
    if conn.refresh_signal is None:
        conn.refresh_signal = deps.refresh_event()
    snapshot = await deps.snapshot(
        gtfs,
        location[0],
        location[1],
        conn.selected_route_ids,
    )
    snapshot["bus_generation"] = conn.location_generation
    if not await deps.send(websocket, {"type": "snapshot", "data": snapshot}):
        print(f"[ws_live_feed:{connection_id}] client closed before snapshot send")
        return False
    _log_live_snapshot(deps, connection_id, snapshot)
    conn.last_sent = time.monotonic()
    if snapshot.get("bus_status") != "cached" and conn.bus_task is None:
        conn.bus_task = asyncio.create_task(
            deps.bus_update(location[0], location[1]),
            name="ws-live-feed-bus-update",
        )
        conn.bus_task_generation = conn.location_generation
    return True


def _recv_located_message(
    conn: _LiveFeedConn,
    turn: _LiveFeedTurn,
    disconnect_error: type[Exception],
) -> None:
    finished, conn.recv_task = conn.recv_task, None
    try:
        turn.message = finished.result()
    except (ValueError, TypeError):
        conn.close_code = 1008
        turn.disconnected = True
    except (disconnect_error, RuntimeError):
        turn.disconnected = True


async def _emit_same_tick_bus(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
    turn: _LiveFeedTurn,
) -> Literal["disconnect", "continue", "snapshot"]:
    # Process a same-tick location frame first. Otherwise a bus result
    # completed for the old location could be emitted immediately before
    # we advance the rider generation.
    if turn.finished_bus_task is None:
        return "snapshot"
    generation = (
        turn.finished_bus_generation
        if turn.finished_bus_generation is not None
        else conn.location_generation
    )
    if not await _emit_bus_update(
        deps,
        websocket,
        turn.finished_bus_task,
        generation,
        conn.location_generation,
    ):
        return "disconnect"
    if turn.message is None:
        return "continue"
    return "snapshot"


async def _handle_live_feed_turn(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
    connection_id: int,
    gtfs,
    turn: _LiveFeedTurn,
) -> bool:
    if turn.disconnected or conn.close_code is not None:
        return False
    await _apply_live_feed_frame(deps, conn, connection_id, turn.message)
    bus_action = await _emit_same_tick_bus(websocket, deps, conn, turn)
    if bus_action == "disconnect":
        return False
    if bus_action == "continue":
        return True
    if conn.location is None:
        return True
    return await _publish_live_snapshot(
        websocket, deps, conn, connection_id, gtfs, turn.network_refreshed
    )


async def _drive_live_feed(
    websocket: WebSocket,
    deps: LiveFeedSocketDependencies,
    conn: _LiveFeedConn,
    connection_id: int,
) -> None:
    gtfs = getattr(websocket.app.state, "gtfs", None)
    if gtfs is None:
        await deps.send(websocket, {"type": "error", "message": "GTFS not ready"})
        conn.close_code = 1011
        return
    while True:
        if conn.location is None:
            turn = await _await_first_location(websocket, deps, conn)
        else:
            turn = await _await_located_inputs(websocket, deps, conn)
        if not await _handle_live_feed_turn(
            websocket, deps, conn, connection_id, gtfs, turn
        ):
            return


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
    conn = _LiveFeedConn()
    try:
        await _drive_live_feed(websocket, deps, conn, connection_id)
    except deps.disconnect_error:
        deps.vlog(f"[ws_live_feed:{connection_id}] disconnected")
        return
    except asyncio.CancelledError:
        return
    finally:
        stopped.set()
        await cancel_and_await(conn.recv_task, conn.bus_task, guard)
        if lease_failed.is_set():
            conn.close_code = 1013
        if conn.close_code is not None:
            await close_socket_safe(websocket, conn.close_code, deps.disconnect_error)
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
