from app.services.voice import generate_speech
from fastapi import APIRouter
from fastapi.responses import Response
import random

thinking_phrases = [
    "Consulting the MTA, as unreliable as that may be. Cross-referencing real-time feeds, nearby stations, and current delay reports. Stand by.",
    "One moment, sir. I'm pulling live train data, checking for incidents, and calculating your best options. The subway requires patience, even from me.",
    "Analyzing conditions across your route corridor. Scanning for delays, stalled trains, and anything the MTA hasn't bothered to announce yet.",
    "Processing. I'm checking real-time arrivals, walking distances, and active service alerts. I assure you this will be faster than waiting for the F train.",
    "Give me a moment, sir. I'm querying the MTA feeds, cross-checking incidents, and running the numbers. I have standards for accuracy, unlike the G train schedule.",
    "On it, sir. Pulling real-time data from multiple subway lines, checking for delays and disruptions. This will be worth the wait, unlike most MTA announcements.",
    "Accessing live MTA feeds now. Comparing your nearby stations, active trains, and any incidents in the area. Precision takes a moment, but never as long as a signal delay."
]

router = APIRouter()

@router.post("/api/thinking")
async def thinking_audio():
    audio = generate_speech(random.choice(thinking_phrases))

    return Response(content=audio, media_type="audio/mpeg")