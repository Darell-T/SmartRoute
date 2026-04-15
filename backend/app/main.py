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
load_dotenv(_backend_dir / ".env")

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from app.routers import thinking, trips
from app.utils.gtfs_static import GTFSStaticData
from app.models.migrate_gtfs import migrate


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
            print("[gtfs] starting hourly refresh...")
            await asyncio.to_thread(migrate)
            print("[gtfs] refresh complete")
        except Exception as e:
            print(f"[gtfs] refresh error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.gtfs = GTFSStaticData()
    refresh_task = asyncio.create_task(_gtfs_refresh_loop())
    yield
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass


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
protected_api.include_router(thinking.router)
protected_api.include_router(trips.router)
app.include_router(protected_api)

@app.get("/health", dependencies=[])
@app.head("/health", dependencies=[])
async def health():
    return {"status": "ok"}
