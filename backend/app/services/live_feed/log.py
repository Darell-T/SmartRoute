"""Shared verbose telemetry logger for the live-feed router + its helpers."""

import os


def _vlog(message: str):
    """Routine websocket telemetry (connection lifecycle, per-snapshot dumps).
    Off by default so the console stays readable; set BACKEND_VERBOSE_LOGS=1 to
    re-enable. Genuine errors are logged unconditionally with print()."""
    if os.getenv("BACKEND_VERBOSE_LOGS", "0") == "1":
        print(message)
