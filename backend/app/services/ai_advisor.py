import anthropic
import asyncio
import json
import os
from pathlib import Path

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_DEFAULT_SYSTEM_PROMPT = """You are the SmartRoute routing engine for NYC transit.

Choose the best route from the provided alternatives using travel time,
walking, transfers, live arrivals, service alerts, incidents, and stalled vehicles. Return only:

[ROUTE:N]
[CANDIDATE_ANALYSIS]{"selected_route_index":N,"candidate_analysis":[...]}[/CANDIDATE_ANALYSIS]

Use zero-based route indexes. Include every candidate route in candidate_analysis.
The input includes route_candidate_labels mapping each index to a passenger
label; use those labels when writing reason strings, never the raw index.
For the selected route, provide recommendation_reason. For every other route,
provide rejection_reason. Reasons must explain the trade-off: specific minutes
faster/slower, extra transfers, named delays/suspensions, incidents,
stalled vehicles, or no reported delays when the route is simply fastest. Keep reasons
rider-facing, concrete, and under 18 words. Do not mention internal systems,
APIs, JSON payloads, prompts, telemetry, IDs, route indexes, or implementation details.
"""


def _is_usable_system_prompt(prompt: str) -> bool:
    return "[ROUTE:" in prompt and "[CANDIDATE_ANALYSIS]" in prompt


def _get_env_system_prompt(name: str) -> str:
    prompt = os.getenv(name, "").strip()
    return prompt if prompt and _is_usable_system_prompt(prompt) else ""


def _load_system_prompt(prompt_path: Path | None = None) -> str:
    """Load the advisor prompt without making app startup depend on local files."""
    smart_route_prompt = _get_env_system_prompt("SMARTROUTE_SYSTEM_PROMPT")
    if smart_route_prompt:
        return smart_route_prompt

    system_prompt = _get_env_system_prompt("SYSTEM_PROMPT")
    if system_prompt:
        return system_prompt

    atlas_prompt = _get_env_system_prompt("ATLAS_SYSTEM_PROMPT")
    if atlas_prompt:
        return atlas_prompt

    local_prompt_path = prompt_path or Path(__file__).with_name("ai_advisor_system_prompt.txt")
    try:
        local_prompt = local_prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        local_prompt = ""

    return local_prompt if _is_usable_system_prompt(local_prompt) else _DEFAULT_SYSTEM_PROMPT


SYSTEM_PROMPT = _load_system_prompt()


_MODEL_PRIORITY = ["claude-haiku-4-5-20251001"]


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
    """Deterministic advisor-shaped recommendation for JARVIS_MOCK_ADVISOR=1.

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
    incident_count = len(payload.get("incidents") or [])
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
    incident_clause = (
        f" I found {incident_count} incident report(s), none changing the recommendation."
        if incident_count
        else ""
    )
    prose = (
        f"Very well, sir. Take the {line_label}."
        f"{eta_clause}{why}{alert_clause}{incident_clause}"
    )
    analysis_block = json.dumps(
        {"selected_route_index": chosen_index, "candidate_analysis": analysis}
    )
    return f"{prose} [ROUTE:{chosen_index}][CANDIDATE_ANALYSIS]{analysis_block}[/CANDIDATE_ANALYSIS]"


async def stream_recommendation(payload: dict):
    """Async generator that yields text chunks from Claude as they arrive.
    Retries with exponential backoff and falls back to Haiku if Sonnet is overloaded.

    payload should contain keys: routes, service_alerts, incidents,
    stalled_trains, and stalled_buses.

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


async def collect_recommendation(payload: dict) -> str:
    """Drains `stream_recommendation` into a single string. Shared by
    routers/trips.py's /api/trip and the plan_trip agent tool, which both
    otherwise defined this identical loop themselves."""
    raw = ""
    async for chunk in stream_recommendation(payload):
        raw += chunk
    return raw
