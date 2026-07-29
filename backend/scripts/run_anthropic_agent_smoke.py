"""Opt-in, sanitized Claude model and minimal-message certification."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from app.services.agent import model_request, policy


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run bounded Anthropic model-list and minimal-message checks."
    )
    parser.add_argument("--live", action="store_true", help="Allow live Anthropic requests.")
    return parser.parse_args()


async def certify() -> dict[str, Any]:
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return {"status": "blocked", "reason": "ANTHROPIC_API_KEY is not configured"}

    auto = policy.policy_for_mode("auto")
    quick = policy.policy_for_mode("quick")
    client = anthropic.AsyncAnthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=0,
        timeout=15.0,
    )
    model_access = {"auto_model_accessible": False, "quick_model_accessible": False}
    try:
        page = await asyncio.wait_for(client.models.list(limit=100), timeout=15.0)
        model_ids = {str(model.id) for model in page.data}
        model_access = {
            "auto_model_accessible": auto.model in model_ids,
            "quick_model_accessible": quick.model in model_ids,
        }
    except Exception as exc:
        details = model_request.provider_error_details(exc)
        return {
            "status": "failed",
            "phase": "models",
            "status_code": details.status_code,
            "error_type": details.error_type,
            "message": details.message,
            "request_id": details.request_id,
            **model_access,
        }

    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=auto.model,
                max_tokens=32,
                messages=[{"role": "user", "content": "Reply with OK."}],
            ),
            timeout=15.0,
        )
    except Exception as exc:
        details = model_request.provider_error_details(exc)
        return {
            "status": "failed",
            "phase": "message",
            "status_code": details.status_code,
            "error_type": details.error_type,
            "message": details.message,
            "request_id": details.request_id,
            "latency_ms": round((time.monotonic() - started) * 1000),
            **model_access,
        }

    request_id = policy.safe_model_label(str(getattr(response, "_request_id", "none")))
    return {
        "status": "passed",
        "phase": "message",
        "status_code": 200,
        "error_type": "none",
        "message": "none",
        "request_id": request_id,
        "latency_ms": round((time.monotonic() - started) * 1000),
        **model_access,
    }


def main() -> int:
    args = _arguments()
    if not args.live:
        print("SKIPPED: pass --live to allow bounded Anthropic requests")
        return 0
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    result = asyncio.run(certify())
    fields = (
        "status",
        "phase",
        "status_code",
        "error_type",
        "message",
        "request_id",
        "latency_ms",
        "auto_model_accessible",
        "quick_model_accessible",
        "reason",
    )
    print(
        "Anthropic agent smoke: "
        + " ".join(f"{field}={result[field]}" for field in fields if field in result)
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
