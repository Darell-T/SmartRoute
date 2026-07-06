
import anthropic
import asyncio
import json
import os
import re
import time
from pathlib import Path

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
# The SmartRoute system prompt lives in a sibling text file so it can be
# edited and reviewed without touching code. SMARTROUTE_SYSTEM_PROMPT overrides
# it (legacy ATLAS_SYSTEM_PROMPT still honored for existing deploys); the file
# is the default so local dev works with no env configuration.
SYSTEM_PROMPT = (
    os.getenv("SMARTROUTE_SYSTEM_PROMPT")
    or os.getenv("ATLAS_SYSTEM_PROMPT")
    or Path(__file__).with_name("ai_advisor_system_prompt.txt").read_text(encoding="utf-8")
)


_MODEL_PRIORITY = ["claude-haiku-4-5-20251001"]

LIVE_SUMMARY_PROMPT = """Produce a short operational briefing about the NYC subway network.

Return JSON only, with exactly these keys:
{"headline":"...","body":"..."}

Rules:
- headline must be 3 to 7 words.
- body must be 2 or 3 short sentences total.
- Speak about overall subway network health, not trip planning.
- Do not mention incidents, riders, boarding advice, destinations, or route indices.
- Do not mention implementation details like GTFS, payloads, APIs, servers, telemetry, parse failures, or internal tooling.
- Rider-facing subway line names like Q or A are allowed when useful.
- Keep the tone calm, precise, and factual. No persona, no jokes.
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
