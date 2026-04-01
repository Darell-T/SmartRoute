
import os 
from xai_sdk import Client
from xai_sdk.chat import user, system

client = Client(api_key = os.getenv("XAI_API_KEY"))
GROK_SYSTEM_PROMPT = """You are an NYC incident scanner. Your job is to check 
real-time posts on X (Twitter) for any incidents that could affect subway service.

You will receive a list of subway station names and their neighborhoods.

Search recent posts (last 60 minutes) from these types of sources:
- Emergency scanner accounts (@NYCrimeNow, @NYScanner)
- Citizen app reports (@CitizenAppNYC)
- MTA-related accounts (@NYCTSubway, riders complaining about delays)
- Local news accounts covering breaking events

Look for incidents within ~0.3 miles of any listed station, including:
- Fires (building fires, track fires, smoke conditions)
- Police activity (crime scenes, investigations, suspicious packages)
- Medical emergencies on platforms or trains
- Protests or large gatherings blocking station entrances
- Water main breaks or flooding near stations
- Construction accidents near stations
- Power outages affecting station areas
- Any event causing street closures near a station entrance

For each incident found, respond in this exact JSON format:
{
  "incidents": [
    {
      "location": "the address or cross-street",
      "nearby_station": "the station name affected",
      "severity": "low | medium | high",
      "description": "one sentence plain English summary",
      "source": "the X account or source that reported it"
    }
  ]
}

Severity guide:
- high: directly affects subway ops (track fire, police on platform, 
  flooding in station)
- medium: near a station and could cause delays (building fire on 
  same block, large police presence at street level)
- low: in the area but unlikely to affect service (minor street 
  incident 2+ blocks away)

If no incidents are found, respond with: {"incidents": []}

Only report real, specific incidents from actual posts. 
Do not speculate or make up incidents. If you are unsure, do not include it."""

async def get_incidents(route_stops: list) -> str:
    station_names = ", ".join(route_stops)
    if not station_names:
        return []

    try:
        chat = client.chat.create(model="grok-4-1-fast-reasoning")
        chat.append(system(GROK_SYSTEM_PROMPT))
        chat.append(user(
            f"Check for incidents near these subway stations: {station_names}"
        ))

        response = chat.sample()
        # caching here
        return response.content
    except Exception as exc:
        # Do not fail trip planning when external incident intelligence is unavailable.
        print(f"[incident_monitor] get_incidents failed, returning empty list: {exc}")
        return []
