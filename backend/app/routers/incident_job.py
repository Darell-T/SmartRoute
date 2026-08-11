"""Internal cron entrypoint for background incident intelligence."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException

from app.services import background_incident_job

router = APIRouter()


def _verify_cron_secret(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> None:
    expected = os.getenv("INCIDENT_JOB_CRON_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    if (x_cron_secret or "").strip() != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/api/internal/incident-refresh")
async def incident_refresh(_auth: None = Depends(_verify_cron_secret)):
    """Manual/internal trigger for the background incident intelligence refresh.

    Render cron drives ``backend/scripts/run_incident_refresh.py``; this
    endpoint exists for manual and internal invocation only.
    """

    result = await background_incident_job.run_background_incident_refresh()
    if result.get("status") == "failed":
        # 503 without metrics or error details signals failure to the caller.
        raise HTTPException(status_code=503, detail="Incident refresh failed")
    return result
