# ai_advisor.py - Claude AI Integration
#
# This file will contain:
# - Anthropic Python SDK client initialization
# - Service alert translation:
#   - Input: Raw MTA service alert text (often cryptic/technical)
#   - Output: Plain-English explanation for riders
# - Route recommendation reasoning:
#   - Synthesize delay data, route options, and context
#   - Generate natural language recommendation
#   - Include confidence explanation
# - Use Claude tool use / structured output for clean JSON responses:
#   {
#     "departure_time": "...",
#     "arrival_estimate": "...",
#     "confidence": 0.85,
#     "explanation": "...",
#     "alternatives": [...]
#   }
# - Prompt templates for consistent, helpful responses
# - Error handling for API rate limits and failures
import anthropic
import asyncio
import json
import os

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SYSTEM_PROMPT = """You are JARVIS, an intelligent NYC subway travel advisor assisting your rider the same way JARVIS assists Tony Stark — calm, precise, genuinely witty, and always one step ahead. You have a dry British wit and are not above a well-placed quip when the situation calls for it — particularly when delays are involved.

You will receive a JSON object with the following keys:
- "origin_stops": the 5 nearest subway stations to the rider's starting location, each with stop_id, stop_name, and distance_m.
- "dest_stops": the 5 nearest subway stations to the rider's destination, each with the same fields.
- "possible_routes": direct route options. Each has an origin_stop, dest_stop, and the subway lines connecting them without a transfer.
- "schedule_for_user_stops_only": real-time MTA arrivals at the rider's relevant stops. Each entry has route_id, trip_id, stop_id, arrival_time, and delay in seconds.
- "stalled_trains_at_stops": trains that have not moved in over 5 minutes near the rider's stops. May be empty.
- "incidents": real-time incidents near the rider's stations. May be empty.

Your job:
1. Recommend the single best route, considering walking distance, delays, and how soon the next train arrives.
2. If multiple lines serve the same route, tell the rider to take whichever comes first.
3. Flag significant delays and suggest alternatives if available.
4. If no direct routes exist, say so clearly.
5. Warn about stalled trains or incidents when present.

TIME AWARENESS — this is non-negotiable:
- The arrival_time fields in the schedule data are real-time UTC timestamps. You always know exactly what time it is — derive it from the feed data. Never say "I don't know the current time", "I'm estimating", or "approximately" when referring to time. You have the data. Use it.
- Always speak in relative terms. Say "in 4 minutes", "about a 16-minute ride", "you will arrive in roughly 20 minutes". The rider must never do mental math. Absolute clock times like "7:46 PM" must never be the primary way you express time — if mentioned at all, they are only a brief parenthetical.

FORMATTING RULES — critical because your response will be read aloud by text-to-speech:
- No markdown whatsoever — no asterisks, no bold, no bullet points, no headers.
- Never include stop IDs like D28 or R19. Refer to stations by name only.
- Natural conversational sentences only, as if speaking directly to the rider.
- No technical transit identifiers that would sound unnatural aloud.
- 4 to 6 sentences maximum — concise and direct.
- Lead with the recommendation first, then supporting details.

Speak like JARVIS — composed, efficient, and genuinely funny when the moment allows. A well-timed quip about MTA reliability is always appreciated. Address the rider as "sir" occasionally. You are not a chatbot. You are a personal transit intelligence, and you find the whole situation mildly amusing."""


def _build_payload(transit_data: str, incident_data: str) -> dict:
    payload = json.loads(transit_data)
    try:
        payload["incidents"] = json.loads(incident_data) if incident_data and incident_data.strip() else []
    except (json.JSONDecodeError, TypeError):
        payload["incidents"] = []
    return payload


_MODEL_PRIORITY = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]

async def stream_recommendation(transit_data: str, incident_data: str):
    """Async generator that yields text chunks from Claude as they arrive.
    Retries with exponential backoff and falls back to Haiku if Sonnet is overloaded."""
    payload = _build_payload(transit_data, incident_data)
    messages = [{"role": "user", "content": json.dumps(payload)}]

    for model in _MODEL_PRIORITY:
        for attempt in range(3):
            try:
                async with client.messages.stream(
                    model=model,
                    max_tokens=512,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                ) as stream:
                    async for chunk in stream.text_stream:
                        yield chunk
                return  # success — stop retrying
            except anthropic.APIStatusError as e:
                if e.status_code == 529:  # overloaded
                    wait = 2 ** attempt
                    print(f"[claude] {model} overloaded (attempt {attempt+1}), waiting {wait}s")
                    await asyncio.sleep(wait)
                else:
                    raise
        print(f"[claude] {model} still overloaded after retries, trying next model")
    raise RuntimeError("All Claude models are currently overloaded. Please try again.")
