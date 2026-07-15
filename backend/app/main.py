import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load env before routers/services import (they read os.getenv at module load).
# Repo root .env, then optional backend/.env overrides.
_backend_dir = Path(__file__).resolve().parent.parent
_repo_root = _backend_dir.parent
load_dotenv(_repo_root / ".env")
load_dotenv(_backend_dir / ".env", override=True)

# Fail fast on missing required auth config instead of discovering it per request.
# An unset APP_KEY would let the API key check and WebSocket auth fall through.
if not os.getenv("APP_KEY"):
    raise RuntimeError("APP_KEY is not set; refusing to start.")

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from app.routers import trips, live_feed, subway, agent_chat
from app.services.mta.warm import warm_realtime_caches
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
    # Keep the MTA realtime caches warm so the live feed, hub, and alerts are
    # served from cache (Transit-app style) instead of each fresh connection
    # paying the full upstream fetch fan-out. First pass runs immediately.
    while True:
        try:
            await warm_realtime_caches()
        except Exception as exc:
            print(f"[warm] realtime cache warm failed: {exc!r}")
        # Wake every connected live-feed socket so it pushes the freshly warmed
        # data immediately -- event-driven realtime push, not a per-client timer.
        live_feed.signal_realtime_refresh()
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
        gtfs._pattern_index = StopPatternIndex.load()
        print(
            f"[startup] stop-pattern index loaded: {len(gtfs._pattern_index.patterns)} "
            f"patterns, {len(gtfs._pattern_index.stops)} stops"
        )
    except Exception as exc:
        print(f"[startup] stop-pattern index load FAILED (enrichment degraded): {exc!r}")
    app.state.gtfs = gtfs
    # Optional DB pool + daily GTFS refresh, both off the startup critical path.
    app.state.pool_task = asyncio.create_task(_init_pool_bg())
    refresh_task = asyncio.create_task(_gtfs_refresh_loop())
    warm_task = asyncio.create_task(_realtime_warm_loop())
    yield
    refresh_task.cancel()
    warm_task.cancel()
    for task in (refresh_task, warm_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    close_pool()


app = FastAPI(lifespan=lifespan)
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
    return {"status": "ok"}
