# main.py - FastAPI Application Entry Point
#
# This file will contain:
# - FastAPI app instance creation
# - CORS middleware configuration (allow frontend origin)
# - Lifespan events for startup/shutdown:
#   - Initialize database connection pool
#   - Connect to Redis
#   - Load static GTFS data into memory
#   - Start background task for periodic MTA feed polling
# - Include routers from routers/ directory
# - Health check endpoint
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load env before routers/services import (they read os.getenv at module load).
# Repo root .env, then optional backend/.env overrides.
_backend_dir = Path(__file__).resolve().parent.parent
_repo_root = _backend_dir.parent
load_dotenv(_repo_root / ".env")
load_dotenv(_backend_dir / ".env")

from fastapi import FastAPI
from app.routers import thinking, trips
from fastapi.middleware.cors import CORSMiddleware
from app.utils.gtfs_static import GTFSStaticData, download_supplemented_gtfs
from app.utils.gtfs_static import GTFSStaticData
from fastapi import Depends, Security, HTTPException
from fastapi.security.api_key import APIKeyHeader


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


async def _load_gtfs_into_state(app: FastAPI) -> None:
    import time as _time
    t0 = _time.monotonic()
    print("[main] === GTFS init start ===")
    try:
        print("[main] step 1: download_supplemented_gtfs()")
        downloaded = await asyncio.to_thread(download_supplemented_gtfs)
        print(f"[main] step 1 done in {_time.monotonic()-t0:.1f}s — downloaded={downloaded}")

        print("[main] step 2: GTFSStaticData() (SQLite load)")
        t1 = _time.monotonic()
        app.state.gtfs = await asyncio.to_thread(GTFSStaticData)
        print(f"[main] step 2 done in {_time.monotonic()-t1:.1f}s")

        app.state.gtfs_error = None
        print(f"[main] === GTFS ready — total {_time.monotonic()-t0:.1f}s ===")
    except Exception as exc:
        app.state.gtfs = None
        app.state.gtfs_error = f"GTFS initialization failed: {exc}"
        print(f"[main] GTFS initialization error after {_time.monotonic()-t0:.1f}s: {exc}")


async def _gtfs_refresh_loop(app: FastAPI):
    """Keep GTFS data fresh without blocking app startup."""
    while True:
        try:
            if app.state.gtfs is None:
                await _load_gtfs_into_state(app)
                if app.state.gtfs is None:
                    await asyncio.sleep(30)
                    continue

            await asyncio.sleep(3600)
            updated = await asyncio.to_thread(download_supplemented_gtfs)
            gtfs: Optional[GTFSStaticData] = getattr(app.state, "gtfs", None)
            if updated and gtfs is not None:
                await asyncio.to_thread(gtfs.reload)
                print("[main] GTFS data reloaded")
        except Exception as exc:
            print(f"[main] GTFS refresh error: {exc}")
            await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.gtfs = None
    app.state.gtfs_error = None

    # Start GTFS initialization/refresh in the background so the web port can open immediately.
    refresh_task = asyncio.create_task(_gtfs_refresh_loop(app))

    yield

    # Shutdown: cancel the refresh loop
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan, dependencies= [Depends(_verify_api_key)])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_origin_regex=_allowed_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(thinking.router)
app.include_router(trips.router)

@app.get("/health", dependencies=[])
@app.head("/health", dependencies=[])
async def health():
    return {
        "status": "ok",
        "gtfs_ready": app.state.gtfs is not None,
        "gtfs_error": app.state.gtfs_error,
    }