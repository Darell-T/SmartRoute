# Environment, telemetry, and configuration gates intentionally run before the
# rest of the application import graph is loaded.
# ruff: noqa: E402

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import redis
from dotenv import load_dotenv

# Load env before routers/services import (they read os.getenv at module load).
# Repo root .env, then optional backend/.env overrides. The root file is the
# local project configuration, so it must replace values inherited from a
# shell that may have been started before the file was corrected.
_backend_dir = Path(__file__).resolve().parent.parent
_repo_root = _backend_dir.parent
load_dotenv(_repo_root / ".env", override=True)
load_dotenv(_backend_dir / ".env", override=True)

from app import observability

observability.initialize()

from app import runtime
from app.services.agent.model import policy as agent_policy
from app.services.agent.tools.places import damn_lines

agent_policy.validate_agent_configuration()

if not os.getenv("APP_KEY"):
    raise RuntimeError("APP_KEY is not set; refusing to start.")

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader

from app.routers import agent_chat, incident_refresh, live_feed, subway, trips
from app.services.incidents.scout_provider import close_incident_scout_client
from app.services.live_feed.network_snapshot import network_snapshot_store
from app.services.mta.bus_runtime import close_bus_client, start_bus_client
from app.services.mta.static_gtfs.migration import migrate
from app.services.mta.static_gtfs.store import GTFSStaticData, close_pool, init_pool
from app.services.trips.crowds.search_provider import close_crowd_search_client

REALTIME_REFRESH_INTERVAL_S = 30
REALTIME_ACTIVE_WINDOW_S = 45
QUEUE_HISTORY_CHECK_INTERVAL_S = 60 * 60
_LOGGER = logging.getLogger(__name__)

# Render polls readiness frequently. PING is deliberately cheap but does not
# prove that quota-enforced Redis commands used by admission and sessions are
# available. Cache a functional EVAL probe so readiness detects that failure
# without turning health checks into a material source of Redis usage.
REDIS_FUNCTIONAL_PROBE_SUCCESS_TTL_S = 300
REDIS_FUNCTIONAL_PROBE_FAILURE_TTL_S = 15
_redis_functional_probe_client_id: int | None = None
_redis_functional_probe_checked_at = 0.0
_redis_functional_probe_result = False
_REDIS_FUNCTIONAL_PROBE = "return 1"


api_key_header = APIKeyHeader(name = "X-App-Key")

async def _verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != os.getenv("APP_KEY"):
        raise HTTPException(status_code = 403, detail = "Forbidden")


def _allowed_origins() -> list[str]:
    return [
        "https://smartroute.fyi",
    ]


def _allowed_origin_regex() -> str | None:
    raw = os.getenv("CORS_ORIGIN_REGEX", "").strip()
    return raw or None

async def _gtfs_refresh_loop():
    while True:
        await asyncio.sleep(86400)
        try:
            print("[gtfs] starting daily refresh...")
            await asyncio.to_thread(migrate)
            print("[gtfs] refresh complete")
        except Exception as e:  # noqa: BLE001 daily refresh must not kill the process
            print(f"[gtfs] refresh error: {e}")


async def _realtime_warm_loop():
    while True:
        if network_snapshot_store.has_recent_demand(REALTIME_ACTIVE_WINDOW_S):
            try:
                await network_snapshot_store.refresh()
            except Exception as exc:  # noqa: BLE001 parser faults must not kill the warm loop
                print(
                    "[live_feed] network snapshot refresh failed: "
                    f"{type(exc).__name__}"
                )
        await asyncio.sleep(REALTIME_REFRESH_INTERVAL_S)


async def _queue_history_refresh_loop() -> None:
    while True:
        try:
            await damn_lines.warm_history()
        except Exception:
            _LOGGER.exception("Damn Lines history refresh failed")
        await asyncio.sleep(QUEUE_HISTORY_CHECK_INTERVAL_S)


async def _init_pool_bg():
    # Creating minconn connections can block for connect_timeout x minconn when
    # Postgres is slow or unreachable. Trip enrichment uses the static index, so
    # the optional pool can initialize without delaying application startup.
    try:
        await asyncio.to_thread(init_pool)
        print("[startup] DB pool ready (optional; trip enrichment is static)")
    except Exception as exc:  # noqa: BLE001 optional pool must not delay startup
        print(f"[startup] DB pool init failed; continuing (enrichment is static): {exc!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    gtfs = GTFSStaticData()
    try:
        from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex
        gtfs.set_pattern_index(StopPatternIndex.load())
        print(
            f"[startup] stop-pattern index loaded: {len(gtfs._pattern_index.patterns)} "
            f"patterns, {len(gtfs._pattern_index.stops)} stops"
        )
    except Exception as exc:  # noqa: BLE001 missing index degrades enrichment
        print(f"[startup] stop-pattern index load FAILED (enrichment degraded): {exc!r}")
    try:
        schedule_loaded = gtfs.load_scheduled_arrivals()
        print(f"[startup] scheduled-arrival fallback loaded={int(schedule_loaded)}")
    except Exception as exc:  # noqa: BLE001 missing schedule degrades fallback
        print(
            "[startup] scheduled-arrival fallback unavailable "
            f"type={type(exc).__name__}"
        )
    app.state.gtfs = gtfs
    await start_bus_client()
    app.state.pool_task = asyncio.create_task(_init_pool_bg())
    refresh_task = asyncio.create_task(_gtfs_refresh_loop())
    warm_task = asyncio.create_task(_realtime_warm_loop())
    queue_history_task = asyncio.create_task(_queue_history_refresh_loop())
    app.state.startup_complete = True
    yield
    app.state.startup_complete = False
    refresh_task.cancel()
    warm_task.cancel()
    queue_history_task.cancel()
    for task in (refresh_task, warm_task, queue_history_task):
        with suppress(asyncio.CancelledError):
            await task
    await live_feed.close_background_bus_tasks()
    await network_snapshot_store.close()
    await close_bus_client()
    await close_incident_scout_client()
    await close_crowd_search_client()
    close_pool()
    await asyncio.to_thread(observability.shutdown)


app = FastAPI(lifespan=lifespan)

MAX_PUBLIC_BODY_BYTES = 32 * 1024


@app.middleware("http")
async def reject_oversize_public_json(request: Request, call_next):
    """Bound raw request bytes before FastAPI/Pydantic parses public JSON."""
    if request.method in {"POST", "PUT", "PATCH"} and request.url.path.startswith("/api/"):
        raw_length = request.headers.get("content-length")
        if raw_length and raw_length.isdigit() and int(raw_length) > MAX_PUBLIC_BODY_BYTES:
            return JSONResponse({"detail": "Request body is too large."}, status_code=413)
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_PUBLIC_BODY_BYTES:
                # Do not create an unbounded request-body cache. Draining is
                # unnecessary after a terminal 413 response on this request.
                return JSONResponse({"detail": "Request body is too large."}, status_code=413)
            chunks.append(chunk)
        # Starlette caches ``_body`` for downstream JSON/Pydantic consumers;
        # it is assigned only after the streaming cap has been satisfied.
        request._body = b"".join(chunks)
        if size > MAX_PUBLIC_BODY_BYTES:
            return JSONResponse({"detail": "Request body is too large."}, status_code=413)
    return await call_next(request)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_origin_regex=_allowed_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

protected_api = APIRouter(dependencies=[Depends(_verify_api_key)])
protected_api.include_router(trips.router)
protected_api.include_router(live_feed.router)
protected_api.include_router(subway.router)
protected_api.include_router(agent_chat.router)
app.include_router(protected_api)
app.include_router(live_feed.ws_router)
# Cron-secret auth only. Not behind X-App-Key.
app.include_router(incident_refresh.router)

@app.get("/health", dependencies=[])
@app.head("/health", dependencies=[])
async def health():
    """Liveness only: the process can answer an HTTP request."""
    return {"status": "ok"}


async def _session_store_ready() -> bool:
    from app.services import cache

    client = cache.redis_client
    if client is None:
        return False
    try:
        ping_ok = bool(
            await asyncio.wait_for(asyncio.to_thread(client.ping), timeout=0.25)
        )
    except (redis.exceptions.RedisError, TimeoutError, OSError, RuntimeError):
        return False
    if not ping_ok:
        return False

    global _redis_functional_probe_client_id
    global _redis_functional_probe_checked_at
    global _redis_functional_probe_result

    now = time.monotonic()
    client_id = id(client)
    if _redis_functional_probe_result:
        ttl_s = REDIS_FUNCTIONAL_PROBE_SUCCESS_TTL_S
    else:
        ttl_s = REDIS_FUNCTIONAL_PROBE_FAILURE_TTL_S
    if (
        _redis_functional_probe_client_id == client_id
        and now - _redis_functional_probe_checked_at < ttl_s
    ):
        return _redis_functional_probe_result

    try:
        functional = bool(
            await asyncio.wait_for(
                asyncio.to_thread(client.eval, _REDIS_FUNCTIONAL_PROBE, 0),
                timeout=0.25,
            )
        )
    except (redis.exceptions.RedisError, TimeoutError, OSError, RuntimeError):
        functional = False
    _redis_functional_probe_client_id = client_id
    _redis_functional_probe_checked_at = now
    _redis_functional_probe_result = functional
    return functional


def _readiness_failure(reason: str) -> JSONResponse:
    return JSONResponse(
        {
            "status": "not_ready",
            "reason": reason,
            "runtime_mode": runtime.runtime_mode_label(),
        },
        status_code=503,
    )


@app.get("/ready", dependencies=[])
@app.head("/ready", dependencies=[])
async def readiness():
    """Readiness for the durable state and static config required to plan trips."""
    if not getattr(app.state, "startup_complete", False):
        return _readiness_failure("startup")
    if not os.getenv("GOOGLE_ROUTES_API_KEY", "").strip():
        return _readiness_failure("routes_provider_config")
    redis_configured = bool(os.getenv("REDIS_URL", "").strip())
    memory_sessions_allowed = (
        agent_chat.AGENT_ALLOW_MEMORY_SESSIONS and runtime.allows_mock_modes()
    )
    if not redis_configured and not memory_sessions_allowed:
        return _readiness_failure("redis_session_store")
    if redis_configured and not await _session_store_ready():
        return _readiness_failure("redis_session_store_unreachable")
    return {
        "status": "ready",
        "chat_sessions": "durable" if redis_configured else "local",
        "runtime_mode": runtime.runtime_mode_label(),
    }
