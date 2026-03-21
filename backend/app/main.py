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

app = FastAPI()
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