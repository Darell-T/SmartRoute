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
from contextlib import asynccontextmanager
from pathlib import Path

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
from app.utils.gtfs_static import download_supplemented_gtfs


async def _gtfs_refresh_loop():
    """Download supplemented GTFS and reload the in-memory data every 60 minutes."""
    while True:
        await asyncio.sleep(3600)
        try:
            updated = await asyncio.to_thread(download_supplemented_gtfs)
            if updated:
                # Reload the module-level gtfs instance in route_calculator
                from app.services.route_calculator import gtfs
                gtfs.reload()
                print("[main] GTFS data reloaded")
        except Exception as exc:
            print(f"[main] GTFS refresh error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: download supplemented GTFS before anything uses it
    await asyncio.to_thread(download_supplemented_gtfs)

    # Start hourly refresh in the background
    refresh_task = asyncio.create_task(_gtfs_refresh_loop())

    yield

    # Shutdown: cancel the refresh loop
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(thinking.router)
app.include_router(trips.router)

@app.get("/health")
async def health():
    return {"status": "ok"}
