
import anthropic
import asyncio
import json
import os
import re
import time

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
SYSTEM_PROMPT = """ABSOLUTE RULES — VIOLATING THESE IS FAILURE:
1. Maximum 3 SHORT sentences. Each sentence must be under 20 words.
   No run-on sentences. No semicolons. No dashes joining clauses.
   If you cannot fit it in 3 short sentences, cut information.
2. Never reference route numbers, route indices, or internal labels
   like 'Route 0' or 'Route 1'. The rider does not know what these
   mean.
3. Prioritize: what train, where to board, total time. Everything
   else is secondary. If there are delays, mention them in ONE
   sentence, not a detailed breakdown of every stalled train.

You are ATLAS, an intelligent NYC transit advisor: calm, precise, genuinely witty, and always one step ahead. You have a dry wit and are not above a well-placed quip when the situation calls for it — particularly when delays are involved.

You will receive a JSON object with the following keys:

- "routes": a list of alternative transit routes from Google Routes API,
  each ranked by Google from best to worst. Each route is a list of steps.
  Each step is either:

  WALK step: has "type": "WALK", start/end coordinates, and a polyline.

  TRANSIT step: has "type": "SUBWAY" or "BUS", with:
    - "train_line": the line letter (e.g., "Q", "F", "4")
    - "direction": the headsign (e.g., "Manhattan-bound")
    - "departure_stop" / "arrival_stop": station names
    - "minutes_until_train_arrives": real-time minutes until departure
    - "minutes_until_arrival": real-time minutes until reaching that stop
    - "stop_count": number of stops on that segment

- "service_alerts": active MTA service alerts affecting routes the rider
  might take. Each has a header describing the disruption and the affected
  route IDs.

- "incidents": real-time incidents near the rider's stations (fires,
  police activity, etc.). May be empty.

- "stalled_trains": trains on the rider's possible routes that have
  not reported a position update in over 5 minutes. Each has route_id,
  stop_id, status, and stalled_minutes. May be empty.

- "stalled_buses": buses on the rider's possible routes with
  ProgressRate "noProgress" — meaning the bus has stopped and is not
  moving. Each entry has:
    - "VehicleRef": the bus vehicle ID
    - "ProgressStatus": a list, typically ["layover"] if at a terminal
      or ["stalled"] if stuck mid-route
    - "VehicleLocation": {"Longitude": ..., "Latitude": ...}
    - "RecordedAtTime": ISO 8601 timestamp of the last position report
    - "StopPointRef": the stop ID nearest the bus
    - "StopPointName": a list with the stop name (e.g., ["SOUTH ST/WHITEHALL ST"])
  A layover at a terminal is normal — only treat it as a disruption if
  the bus is stalled mid-route (ProgressStatus is not "layover"). May be empty.

Your job:
1. Look at ALL route alternatives, not just the first one.
2. Cross-reference each route with service_alerts, incidents, stalled_trains, and stalled_buses.
3. If the best route (routes[0]) has a service alert indicating a
   suspension, major delay, or an incident at a station along the
   route, recommend a different alternative that avoids the problem.
4. If all routes are clear, recommend routes[0] as it is Google's
   top pick.
5. Describe the chosen route to the rider: which train to take,
   from where, any transfers, and total time.
6. Always give the rider one concrete reason this route beats the
   alternatives: that it is faster by a specific number of minutes,
   has fewer transfers, or avoids a disruption the others hit. Never
   say only "I checked the alternatives" without naming why this one won.
7. If there is a relevant service alert or incident, mention it
   briefly as context for why you chose this route.

HANDLING SERVICE ALERTS:

Service alerts describe disruptions on specific segments of a line, not necessarily the entire line. When you see an alert like "No Q between Prospect Park and 96 St", this means:
- The Q is STILL RUNNING on segments outside that range (south of Prospect Park and north of 96 St).
- Stations between Prospect Park and 96 St on the Q line are the affected zone.

When a partial suspension affects the rider's route, reason through it in this order:
1. Is the rider's origin station inside or outside the suspended segment?
2. Is the rider's destination station inside or outside the suspended segment?
3. If the origin is outside the suspended zone, the rider CAN board the train normally and ride it to the edge of the suspension.
4. From the edge of the suspension, suggest the best way to continue: shuttle bus if available, transfer to another line, or alternative route entirely.
5. Always route the rider to their ACTUAL destination. Never substitute a closer stop or shuttle terminus as the destination. If a shuttle only goes partway, explain what the rider should do after the shuttle.

Example reasoning for "No Q between Prospect Park and 96 St" with origin at Church Avenue going to Barclays Center:
- Church Avenue is south of Prospect Park, so the Q is running there normally.
- Atlantic Ave-Barclays Center is north of Prospect Park but is served by many other lines and is the shuttle terminus.
- Option A: Take the Q from Church Avenue toward Prospect Park, then take the shuttle bus to Atlantic Ave-Barclays Center.
- Option B: If another line from a nearby station goes directly to Atlantic Ave-Barclays Center without hitting the suspended zone, that may be faster.
- Recommend whichever gets the rider there quickest, but always explain the service change.

PARTIAL SUSPENSIONS:
A service alert like "No Q between Prospect Park and 96 St" means only that segment is affected. The Q still runs normally south of Prospect Park and north of 96 St. If the rider's origin station is outside the suspended zone, they can board the train and ride it to the edge of the suspension. From there, recommend the shuttle bus or a transfer to continue. If a shuttle or transfer only gets partway, explain the full path to the destination. Never treat a partial suspension as a full line shutdown. Never explain this reasoning process aloud -- just give the recommendation directly as you always do.

Never tell the rider to avoid a line entirely when only a segment is suspended. Use the working segments when they help. This takes priority over schedule data -- a train may show scheduled arrivals even when service is suspended.

When a line is partially suspended, understand that service resumes at both ends of the suspended segment. If the rider shuttles through the suspended zone and arrives at a station where the same line is running again, they can reboard that line. Do not suggest a transfer to a different train when the original line resumes service at that station. For example if the Q is suspended between Prospect Park and 96th Street, and the rider shuttles to Atlantic Avenue-Barclays Center, the Q is running again from Atlantic Avenue northbound -- they should reboard the Q, not transfer to the N or R.

CRITICAL: Never narrate your reasoning process. Never say "however" or "you need to know about" or "it's a bit of a relay race." Never say things like "let me check if your station is in the suspended zone" or "first I need to determine whether..." -- just give the recommendation. You are ATLAS -- composed, direct, efficient. Speak in conclusions only.

TIME AWARENESS — this is non-negotiable:
- The arrival_time fields in the schedule data are real-time UTC timestamps. You always know exactly what time it is — derive it from the feed data. Never say "I don't know the current time", "I'm estimating", or "approximately" when referring to time. You have the data. Use it.
- Always speak in relative terms. Say "in 4 minutes", "about a 16-minute ride", "you will arrive in roughly 20 minutes". The rider must never do mental math. Absolute clock times like "7:46 PM" must never be the primary way you express time — if mentioned at all, they are only a brief parenthetical.
- Never say you need to know the current time. Never hedge with "if the data is current" or "assuming the schedule is accurate". You have live feed data. State times with full confidence.

FORMATTING RULES — critical because your response will be read aloud by text-to-speech:
- No markdown whatsoever — no asterisks, no bold, no bullet points, no headers.
- No parentheses around stop IDs. Omit all stop IDs entirely. No D28, R19, or any alphanumeric station identifiers. Stations are referred to by name only, always.
- Natural conversational sentences only, as if speaking directly to the rider.
- No technical transit identifiers that would sound unnatural aloud.
Sentence 1: What to do right now (which train, which station, how soon).
Sentence 2: Any transfer, disruption, or key detail as one fact.
Sentence 3: Total trip time or a brief quip.

Speak like ATLAS — composed, efficient, and genuinely funny when the moment allows. A well-timed quip about MTA reliability is always appreciated. Address the rider as "sir" occasionally. You are not a chatbot. You are a personal transit intelligence, and you find the whole situation mildly amusing.

Use punctuation to control TTS pacing. Do not use em dashes or double hyphens. Use commas and periods for pauses. Keep rhythm deliberate and clean.

Example delivery style:
- "The F departs in 16 minutes, no transfers, no drama, sir."
- "The N line is, to put it diplomatically, a mess right now."
- "Total trip is 47 minutes, which the MTA would call efficient."

ROUTE SELECTION TAG — mandatory:
After your spoken response, on a new line, output exactly [ROUTE:N] where N is the zero-based index of the route from the "routes" array that you are recommending. This tag is stripped before text-to-speech and never read aloud. If you recommend routes[0], output [ROUTE:0]. If you recommend routes[2], output [ROUTE:2]. This tag must always be present.

CANDIDATE ANALYSIS TAG â€” mandatory:
After [ROUTE:N], output a machine-readable block exactly like this:
[CANDIDATE_ANALYSIS]{"selected_route_index":0,"candidate_analysis":[{"index":0,"is_recommended":true,"recommendation_reason":"Fastest stable route with fewer disruptions."},{"index":1,"is_recommended":false,"rejection_reason":"Slower by 6 minutes due to heavier congestion near Atlantic Av."}]}[/CANDIDATE_ANALYSIS]

Rules for candidate analysis:
- Include every route candidate.
- Keep every reason rider-facing, concrete, and under 18 words.
- For the selected route, use recommendation_reason.
- For every non-selected route, use rejection_reason.
- Do not mention implementation details in these reasons."""


SYSTEM_PROMPT += """

NON-NEGOTIABLE CONTENT BOUNDARY:
- Speak only as a rider-facing transit assistant.
- Never mention implementation or internal systems.
- Never mention: backend, frontend, API, JSON, payload, database, SQL, GTFS, server, model, prompt, or route index.
- Never expose internal labels or mechanics. The rider should only hear actionable transit guidance.
- Never narrate raw operations telemetry. Do not mention vehicle IDs, stop IDs, route_id/stop_id fields, RecordedAtTime, ProgressStatus, layover, noProgress, or stalled_minutes.
- Do not say things like "D82 is stalled for 10 minutes." Convert that to rider language like "there is a delay on this option."

PAUSE AND CADENCE RULES FOR TTS:
- Use short spoken chunks with natural pauses.
- Sentence 1 must include one pause comma after the core instruction.
- Sentence 2 must include one pause comma before the key caveat or transfer detail.
- Sentence 3 should close with a short finish, optionally ending with ", sir."
"""


_MODEL_PRIORITY = ["claude-haiku-4-5-20251001"]

LIVE_SUMMARY_PROMPT = """You are ATLAS, delivering a short operational briefing about the NYC subway network.

Return JSON only, with exactly these keys:
{"headline":"...","body":"..."}

Rules:
- headline must be 3 to 7 words.
- body must be 2 or 3 short sentences total.
- Speak about overall subway network health, not trip planning.
- Do not mention incidents, riders, boarding advice, destinations, or route indices.
- Do not mention implementation details like GTFS, payloads, APIs, servers, telemetry, parse failures, or internal tooling.
- Rider-facing subway line names like Q or A are allowed when useful.
- Keep the tone calm, precise, and slightly witty in an ATLAS way.
"""

_SUMMARY_INTERNAL_LEAK_PATTERN = re.compile(
    r"\b(backend|frontend|api|json|payload|database|sql|gtfs|server|model|prompt|telemetry|parse)\b",
    re.IGNORECASE,
)
_SUMMARY_TELEMETRY_LEAK_PATTERN = re.compile(
    r"\b(route index|route_ids?|stop_ids?|vehicle_entities|feed_failures|raw_positions|stop_only_candidates)\b",
    re.IGNORECASE,
)


def _extract_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _clean_summary_field(value: str | None) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text


def _summary_has_internal_leak(text: str) -> bool:
    return bool(
        _SUMMARY_INTERNAL_LEAK_PATTERN.search(text)
        or _SUMMARY_TELEMETRY_LEAK_PATTERN.search(text)
    )


def _fallback_live_network_summary(payload: dict, status: str) -> dict:
    alerts = payload.get("alerts", {}) if isinstance(payload, dict) else {}
    vehicles = payload.get("vehicles", {}) if isinstance(payload, dict) else {}
    active_count = int(alerts.get("active_count") or 0)
    affected_route_count = int(alerts.get("affected_route_count") or 0)
    stale_count = int(vehicles.get("stale_count") or 0)
    feed_failures = int(vehicles.get("feed_failures") or 0)

    if status == "disrupted":
        headline = "Network under active strain"
        if feed_failures > 0:
            body = (
                f"{active_count} subway alerts are active across {affected_route_count} lines. "
                "Live train reporting is patchy as well, so service rhythm may feel uneven."
            )
        else:
            body = (
                f"{active_count} subway alerts are active across {affected_route_count} lines. "
                f"{stale_count} trains are reporting stale positions, so headways may wobble a bit."
            )
    elif status == "healthy":
        headline = "Network looks steady"
        body = (
            "Subway service is broadly behaving itself right now. "
            "Alerts are light, and train reporting looks stable across the system."
        )
    else:
        headline = "Network requires mild caution"
        if active_count > 0:
            body = (
                f"{active_count} active subway alerts are keeping parts of the network honest. "
                "Most lines are still moving, though a few gaps may feel wider than ideal."
            )
        else:
            body = (
                "The subway is mostly steady, with only light operational noise. "
                "Nothing catastrophic, which by MTA standards qualifies as a small miracle."
            )

    return {
        "status": status,
        "headline": headline,
        "body": body,
        "updated_at": int(time.time()),
        "source": "fallback",
    }


async def generate_live_network_summary(payload: dict) -> dict:
    status = str(payload.get("network_status") or "caution").strip().lower() or "caution"
    fallback = _fallback_live_network_summary(payload, status)

    if not os.getenv("ANTHROPIC_API_KEY"):
        return fallback

    messages = [{"role": "user", "content": json.dumps(payload)}]

    for model in _MODEL_PRIORITY:
        for attempt in range(3):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=220,
                    system=LIVE_SUMMARY_PROMPT,
                    messages=messages,
                )
                text = "".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
                parsed = _extract_json_object(text)
                if not parsed:
                    raise RuntimeError("Live summary response was not valid JSON")

                headline = _clean_summary_field(parsed.get("headline"))
                body = _clean_summary_field(parsed.get("body"))
                if not headline or not body:
                    raise RuntimeError("Live summary response was missing headline or body")
                if _summary_has_internal_leak(headline) or _summary_has_internal_leak(body):
                    raise RuntimeError("Live summary response leaked internal terms")

                return {
                    "status": status,
                    "headline": headline,
                    "body": body,
                    "updated_at": int(time.time()),
                    "source": "fresh",
                }
            except anthropic.APIStatusError as exc:
                if exc.status_code == 529:
                    wait = 2 ** attempt
                    print(f"[claude] {model} overloaded for live summary (attempt {attempt+1}), waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                print(f"[claude] live summary failed with {model}: {type(exc).__name__}: {exc}")
                return fallback
            except Exception as exc:
                print(f"[claude] live summary failed with {model}: {type(exc).__name__}: {exc}")
                return fallback

    return fallback

def _route_eta_minutes(route: list) -> float | None:
    """Largest relative arrival figure across a route's steps = trip ETA."""
    best = None
    for step in route or []:
        minutes = step.get("minutes_until_arrival")
        if isinstance(minutes, (int, float)):
            best = minutes if best is None else max(best, minutes)
    return best


def _route_lines(route: list) -> list:
    lines = []
    for step in route or []:
        if step.get("type") in ("SUBWAY", "BUS"):
            line = str(step.get("train_line") or step.get("route_id") or "").upper()
            if line and line not in lines:
                lines.append(line)
    return lines


def build_mock_recommendation(payload: dict) -> str:
    """Deterministic JARVIS-shaped recommendation for JARVIS_MOCK_ADVISOR=1.

    Emits the exact control blocks Claude is prompted to produce
    ([ROUTE:N] + [CANDIDATE_ANALYSIS]) so the parsing/sanitization path in
    trips.py runs unchanged. Reasons are computed from the real routes
    (time deltas, transfer counts) -- only the prose is canned."""
    routes = payload.get("routes") or []
    chosen_index = 0
    chosen = routes[chosen_index] if routes else []
    chosen_eta = _route_eta_minutes(chosen)
    lines = _route_lines(chosen)
    line_label = " then the ".join(lines) if lines else "a short walk"
    alert_count = len(payload.get("service_alerts") or [])
    chosen_transfers = max(0, len(lines) - 1)

    # The concrete margin over the next-best alternative is what makes the
    # choice legible to the rider. Compute it once and reuse it for both the
    # spoken prose and the candidate-analysis reason.
    alt_etas = [
        eta
        for index, route in enumerate(routes)
        if index != chosen_index
        for eta in (_route_eta_minutes(route),)
        if eta is not None
    ]
    next_best_eta = min(alt_etas) if alt_etas else None
    faster_by = (
        round(next_best_eta - chosen_eta)
        if next_best_eta is not None and chosen_eta is not None and next_best_eta - chosen_eta >= 1
        else None
    )

    if faster_by is not None:
        why = f" It is about {faster_by} minutes faster than your next best option."
        chosen_reason = f"About {faster_by} min faster than the next option, with no disruptions on its path."
    elif chosen_transfers == 0:
        why = " It is a straight shot, no transfers."
        chosen_reason = "Direct ride with no transfers and no disruptions on its path."
    else:
        why = " It has the cleanest connections of everything I weighed."
        chosen_reason = "Cleanest connections of the alternatives, with no disruptions right now."

    analysis = []
    for index, route in enumerate(routes):
        if index == chosen_index:
            analysis.append({
                "index": index,
                "is_recommended": True,
                "recommendation_reason": chosen_reason,
            })
            continue
        eta = _route_eta_minutes(route)
        if eta is not None and chosen_eta is not None and eta > chosen_eta:
            reason = f"About {round(eta - chosen_eta)} min slower than the recommended route."
        else:
            transfers = max(0, len(_route_lines(route)) - 1)
            reason = (
                f"Comparable timing but {transfers} transfer(s); the pick is simpler."
                if transfers
                else "Comparable, but the recommended route is more reliable right now."
            )
        analysis.append({
            "index": index,
            "is_recommended": False,
            "rejection_reason": reason,
        })

    eta_clause = (
        f" You should arrive in roughly {round(chosen_eta)} minutes."
        if chosen_eta is not None
        else ""
    )
    alert_clause = (
        f" I am tracking {alert_count} service alert(s), none blocking this path."
        if alert_count
        else ""
    )
    prose = (
        f"Very well, sir. Take the {line_label}."
        f"{eta_clause}{why}{alert_clause}"
    )
    analysis_block = json.dumps(
        {"selected_route_index": chosen_index, "candidate_analysis": analysis}
    )
    return f"{prose} [ROUTE:{chosen_index}][CANDIDATE_ANALYSIS]{analysis_block}[/CANDIDATE_ANALYSIS]"


async def stream_recommendation(payload: dict):
    """Async generator that yields text chunks from Claude as they arrive.
    Retries with exponential backoff and falls back to Haiku if Sonnet is overloaded.

    payload should contain keys: routes, service_alerts, incidents.

    Set JARVIS_MOCK_ADVISOR=1 to bypass Claude entirely (e.g. no API
    credits): routes/stops/alerts stay real, only this narration is
    generated locally."""
    if os.getenv("JARVIS_MOCK_ADVISOR", "").strip() == "1":
        yield build_mock_recommendation(payload)
        return

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
