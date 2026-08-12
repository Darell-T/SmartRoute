import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load env before routers/services import (they read os.getenv at module load).
# Repo root .env, then optional backend/.env overrides. The root file is the
# local project configuration, so it must replace values inherited from a
# shell that may have been started before the file was corrected.
_backend_dir = Path(__file__).resolve().parent.parent
_repo_root = _backend_dir.parent
load_dotenv(_repo_root / ".env", override=True)
load_dotenv(_backend_dir / ".env", override=True)

from app.services.agent import policy as agent_policy
from app import runtime

agent_policy.validate_agent_configuration()

# Fail fast on missing required auth config instead of discovering it per request.
# An unset APP_KEY would let the API key check and WebSocket auth fall through.
if not os.getenv("APP_KEY"):
    raise RuntimeError("APP_KEY is not set; refusing to start.")

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from app.routers import trips, live_feed, subway, agent_chat, incident_job
from app.services.live_feed.network_snapshot import network_snapshot_store
from app.services.incident_scout_transport import close_incident_scout_client
from app.services.mta_feed import close_bus_client, start_bus_client
from app.services.trips.crowd_search_provider import close_crowd_search_client
from app.utils.gtfs_static import GTFSStaticData, close_pool, init_pool
from app.models.migrate_gtfs import migrate

# Active riders share one process-owned realtime snapshot. Poll no faster than
# the upstream feed cadence, and stop polling shortly after the last REST or
# WebSocket consumer leaves so an always-on Render instance does not download
# and parse the full network indefinitely while idle.
REALTIME_REFRESH_INTERVAL_S = 30
REALTIME_ACTIVE_WINDOW_S = 45

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
    # Supports Vercel preview URLs like:
    # https://transitagent-<hash>-<scope>.vercel.app
    raw = os.getenv("CORS_ORIGIN_REGEX", "").strip()
    return raw or None

async def _gtfs_refresh_loop():
    while True:
        await asyncio.sleep(86400)
        try:
            print("[gtfs] starting daily refresh...")
            await asyncio.to_thread(migrate)
            print("[gtfs] refresh complete")
        except Exception as e:
            print(f"[gtfs] refresh error: {e}")


async def _realtime_warm_loop():
    # The first rider request refreshes stale state itself. While riders remain
    # active, this owner publishes later generations for all sockets to share.
    while True:
        if network_snapshot_store.has_recent_demand(REALTIME_ACTIVE_WINDOW_S):
            try:
                await network_snapshot_store.refresh()
            except Exception as exc:
                print(
                    "[live_feed] network snapshot refresh failed: "
                    f"{type(exc).__name__}"
                )
        await asyncio.sleep(REALTIME_REFRESH_INTERVAL_S)


async def _init_pool_bg():
    # Bring up the (optional) DB pool WITHOUT blocking startup: a slow/unreachable
    # Postgres takes connect_timeout x minconn seconds, which used to wedge the
    # worker. Trip enrichment no longer needs it (Fix B), so do it in background.
    try:
        await asyncio.to_thread(init_pool)
        print("[startup] DB pool ready (optional; trip enrichment is static)")
    except Exception as exc:
        print(f"[startup] DB pool init failed; continuing (enrichment is static): {exc!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fix B: trip enrichment resolves stop sequences from the in-memory static
    # pattern index, NOT the remote Postgres. Startup must not depend on the DB.
    gtfs = GTFSStaticData()
    # Load the precomputed stop-pattern artifact once. On failure, enrichment
    # degrades to empty stop lists rather than touching the remote DB.
    try:
        from app.utils.stop_patterns import StopPatternIndex
        gtfs.set_pattern_index(StopPatternIndex.load())
        print(
            f"[startup] stop-pattern index loaded: {len(gtfs._pattern_index.patterns)} "
            f"patterns, {len(gtfs._pattern_index.stops)} stops"
        )
    except Exception as exc:
        print(f"[startup] stop-pattern index load FAILED (enrichment degraded): {exc!r}")
    try:
        schedule_loaded = gtfs.load_scheduled_arrivals()
        print(f"[startup] scheduled-arrival fallback loaded={int(schedule_loaded)}")
    except Exception as exc:
        print(
            "[startup] scheduled-arrival fallback unavailable "
            f"type={type(exc).__name__}"
        )
    app.state.gtfs = gtfs
    await start_bus_client()
    # Optional DB pool + daily GTFS refresh, both off the startup critical path.
    app.state.pool_task = asyncio.create_task(_init_pool_bg())
    refresh_task = asyncio.create_task(_gtfs_refresh_loop())
    warm_task = asyncio.create_task(_realtime_warm_loop())
    app.state.startup_complete = True
    yield
    app.state.startup_complete = False
    refresh_task.cancel()
    warm_task.cancel()
    for task in (refresh_task, warm_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await network_snapshot_store.close()
    await close_bus_client()
    await close_incident_scout_client()
    await close_crowd_search_client()
    close_pool()


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
# Cron secret auth only; not behind X-App-Key so Render cron can call it.
app.include_router(incident_job.router)

@app.get("/health", dependencies=[])
@app.head("/health", dependencies=[])
async def health():
    """Liveness only: the process can answer an HTTP request."""
    return {"status": "ok"}


async def _session_store_ready() -> bool:
    """Verify Redis connectivity and periodically exercise a real command."""
    from app.utils import cache

    client = cache.redis_client
    if client is None:
        return False
    try:
        ping_ok = bool(
            await asyncio.wait_for(asyncio.to_thread(client.ping), timeout=0.25)
        )
    except Exception:
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
    except Exception:
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
