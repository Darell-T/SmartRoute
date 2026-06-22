from app.services.voice import generate_speech
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import asyncio
import base64
import random

thinking_phrases = [
    "Scanning MTA feeds across all five boroughs, sir.",
    "Pulling real-time data from every active train on the network.",
    "Interrogating the subway schedule. It's being uncooperative as usual.",
    "Cross-referencing service alerts with live vehicle positions.",
    "Calculating optimal path through the MTA's latest creative decisions.",
    "Monitoring live train positions and checking for anything suspicious.",
    "Consulting the MTA's real-time feeds, which is always an adventure.",
    "Mapping your route through what the MTA calls a transit system.",
    "Running diagnostics on every route alternative available to you.",
    "Evaluating transfer options and praying the connections hold.",
    "Scanning for delays, disruptions, and the usual MTA surprises.",
    "Processing real-time arrivals and checking which trains are actually moving.",
    "Checking for stalled trains. There's usually at least one, sir.",
    "Reviewing incident reports near your stations just to be thorough.",
    "Plotting the fastest route and a backup in case the MTA has other plans.",
    "Querying transit intelligence from every feed the MTA publishes.",
    "Synchronizing with live subway and bus feeds across the network.",
    "Running a full route analysis. The MTA makes this harder than it should be.",
    "Triangulating your best option from several mediocre ones.",
    "Auditing the subway system's current state of affairs, sir.",
    "Checking if the trains have decided to run on schedule today.",
    "Compiling your transit briefing from real-time and historical data.",
    "Negotiating with the MTA's data feeds on your behalf.",
    "Scanning bus and subway positions to find you the cleanest path.",
    "Analyzing stalled vehicles, service changes, and whatever else the MTA is up to.",
    "One moment sir, verifying that your route hasn't been cancelled in the last thirty seconds.",
    "Pulling live feeds and hoping the MTA's servers are feeling cooperative.",
    "Checking every alternative because the obvious route is rarely the best one.",
    "Cross-checking delays, incidents, and service alerts before I send you anywhere.",
    "Surveying the entire transit network so you don't have to, sir.",
]

router = APIRouter()

_audio_cache: dict[str, str] = {}
_phrase_queue: list[str] = []


def _next_phrase() -> str:
    """Draw the next phrase without repeats until all have been used."""
    if not _phrase_queue:
        _phrase_queue.extend(thinking_phrases)
        random.shuffle(_phrase_queue)
    return _phrase_queue.pop()


def _cached_phrase() -> str | None:
    """Return an already generated phrase so repeated route starts avoid TTS."""
    return next(iter(_audio_cache), None)


@router.post("/api/thinking")
async def thinking_audio():
    phrase = _cached_phrase() or _next_phrase()

    if phrase in _audio_cache:
        return JSONResponse(content={"text": phrase, "audio": _audio_cache[phrase]})

    try:
        audio = await asyncio.to_thread(generate_speech, phrase)
        audio_b64 = base64.b64encode(audio).decode("utf-8")
        _audio_cache[phrase] = audio_b64
    except Exception as exc:
        print(f"[thinking] TTS unavailable, returning text-only response: {exc}")
        audio_b64 = ""

    return JSONResponse(content={"text": phrase, "audio": audio_b64})
