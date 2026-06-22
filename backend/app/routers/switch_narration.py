"""Short JARVIS line for switching to an alternative route.

The full trip narration is generated per request; alternative switches use
canned per-line phrases so the TTS audio can be cached and reused instead
of paying ElevenLabs latency/cost on every switch (same cache pattern as
the thinking endpoint). The phrase for a given line is deterministic so a
warm cache stays warm.

route_id is strictly validated -- this endpoint feeds client input into
TTS, so only plain MTA line tokens are accepted (never free text).
"""

import asyncio
import base64
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.voice import generate_speech

router = APIRouter()

_ROUTE_ID_PATTERN = re.compile(r"^[A-Z0-9+-]{1,10}$")

_TEMPLATES = [
    "Rerouting via the {line}, sir.",
    "Very well. Switching you to the {line}.",
    "As you wish -- the {line} it is, sir.",
    "Course corrected. Follow the {line} now.",
]

_audio_cache: dict[str, str] = {}


class SwitchNarrationRequest(BaseModel):
    route_id: str


def _phrase_for(line: str) -> str:
    # Deterministic template per line so repeat switches reuse the cache.
    # hash() is salted per process; a stable digest keeps it deterministic
    # across restarts too.
    index = sum(ord(ch) for ch in line) % len(_TEMPLATES)
    return _TEMPLATES[index].format(line=line)


@router.post("/api/switch-narration")
async def switch_narration(payload: SwitchNarrationRequest):
    line = str(payload.route_id or "").strip().upper()
    if not _ROUTE_ID_PATTERN.match(line):
        raise HTTPException(status_code=422, detail="Invalid route id")

    phrase = _phrase_for(line)
    if phrase in _audio_cache:
        return JSONResponse(content={"text": phrase, "audio": _audio_cache[phrase]})

    try:
        audio = await asyncio.to_thread(generate_speech, phrase)
        audio_b64 = base64.b64encode(audio).decode("utf-8")
        _audio_cache[phrase] = audio_b64
    except Exception as exc:
        print(f"[switch-narration] TTS unavailable, text-only response: {exc}")
        audio_b64 = ""

    return JSONResponse(content={"text": phrase, "audio": audio_b64})
