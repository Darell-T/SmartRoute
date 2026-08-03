import os
import asyncio
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
from app.routers import trips, live_feed, subway, agent_chat
from app.services.live_feed.network_snapshot import network_snapshot_store
from app.services.incident_monitor import close_incident_client
from app.services.mta_feed import close_bus_client, start_bus_client
from app.services.trips.crowd_search_provider import close_crowd_search_client
from app.utils.gtfs_static import GTFSStaticData, close_pool, init_pool
from app.models.migrate_gtfs import migrate

# How often the background loop force-refreshes the realtime MTA caches. Shorter
# than the feed TTL (30s) so user-facing snapshots always hit a warm, fresh
# cache instead of paying upstream fetch latency. The instance only runs this
# while it is awake (Render spins down idle free instances on no inbound
# traffic), so it does not poll 24/7 when nobody is using the app.
REALTIME_WARM_INTERVAL_S = 15


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
    # One process owner fetches and parses network-wide realtime data. Sockets
    # only filter the latest completed generation for their rider location.
    while True:
        try:
            await network_snapshot_store.refresh()
        except Exception as exc:
            print(f"[live_feed] network snapshot refresh failed: {type(exc).__name__}")
        await asyncio.sleep(REALTIME_WARM_INTERVAL_S)


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
    await close_incident_client()
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

@app.get("/health", dependencies=[])
@app.head("/health", dependencies=[])
async def health():
    """Liveness only: the process can answer an HTTP request."""
    return {"status": "ok"}


async def _session_store_ready() -> bool:
    """Bound the Redis probe so readiness never occupies an event-loop worker."""
    from app.utils import cache

    if cache.redis_client is None:
        return False
    try:
        return bool(await asyncio.wait_for(asyncio.to_thread(cache.redis_client.ping), timeout=0.25))
    except Exception:
        return False


@app.get("/ready", dependencies=[])
@app.head("/ready", dependencies=[])
async def readiness():
    """Readiness for chat sessions; optional providers are not startup gates."""
    if not getattr(app.state, "startup_complete", False):
        return JSONResponse({"status": "not_ready", "reason": "startup", "runtime_mode": runtime.runtime_mode_label()}, status_code=503)
    if not os.getenv("REDIS_URL") and not agent_chat.AGENT_ALLOW_MEMORY_SESSIONS:
        return JSONResponse(
            {"status": "not_ready", "reason": "redis_session_store", "runtime_mode": runtime.runtime_mode_label()}, status_code=503
        )
    if os.getenv("REDIS_URL") and not await _session_store_ready():
        return JSONResponse(
            {"status": "not_ready", "reason": "redis_session_store_unreachable", "runtime_mode": runtime.runtime_mode_label()},
            status_code=503,
        )
    return {"status": "ready", "chat_sessions": "durable" if os.getenv("REDIS_URL") else "local", "runtime_mode": runtime.runtime_mode_label()}
