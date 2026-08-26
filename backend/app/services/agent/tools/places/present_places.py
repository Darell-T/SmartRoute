"""Present a validated shortlist from a server-owned discovery set."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.agent import discovery_store
from app.services.agent import events as agent_events
from app.services.agent.passenger_output import (
    MAX_PRESENTATION_FRAMING_CHARS,
    MAX_RESEARCH_PRESENTATION_FRAMING_CHARS,
    framed_events,
    validated_framing,
)
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.places import damn_lines
from app.services.agent.turn.contract import GoalKind, GoalState


_PLACE_GOAL_KINDS = frozenset({GoalKind.PLACE_RECOMMENDATION, GoalKind.DESTINATION_SELECTION})
REASON_CODES = ("top_pick", "highest_rating", "most_reviewed", "budget_friendly", "open_now", "preference_match")
OBJECTIVE_REASONS = frozenset({"top_pick", "highest_rating", "most_reviewed", "budget_friendly", "open_now"})
_NYC = ZoneInfo("America/New_York")
PRESENT_PLACES_SCHEMA = {
    "name": "present_places",
    "description": "Present verified recommendations or one previously shown place's details.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "discovery_set_id": {
                "type": "string",
            },
            "selections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "place_id": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "enum": list(REASON_CODES),
                            "description": "Use top_pick first; highest_rating and other objective reasons need stored facts; otherwise use preference_match.",
                        },
                    },
                    "required": ["place_id", "reason"],
                    "additionalProperties": False,
                },
                "description": "Ordered unique opaque place ids with reason codes.",
            },
            "research_used": {
                "type": "boolean",
            },
            "presentation_mode": {
                "type": "string",
                "enum": ["recommendations", "details"],
                "description": "Use details for one previously shown place; otherwise recommendations.",
            },
            "goal_key": {
                "type": "string",
            },
            "lead_in": {
                "type": "string",
                "description": "Concise framing; research details must be current and grounded.",
            },
            "follow_up": {
                "type": "string",
            },
        },
        "required": ["discovery_set_id", "selections", "research_used", "presentation_mode", "goal_key", "lead_in", "follow_up"],
        "additionalProperties": False,
    },
}


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    owned = _owned_discovery(tool_input, ctx)
    if isinstance(owned, ToolResult):
        return owned
    selected = _selected_places(tool_input, owned, ctx)
    if isinstance(selected, ToolResult):
        return selected
    return await _emit_place_presentation(selected, ctx)


def _place_goal_key(tool_input: dict, ctx: ToolContext) -> str | None:
    evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None
    raw = tool_input.get("goal_key")
    if not isinstance(raw, str) or not raw.strip():
        return None
    goal = contract.get_goal(raw.strip())
    if goal is None or goal.kind not in _PLACE_GOAL_KINDS:
        return None
    return raw.strip()


def _owned_discovery(tool_input: dict, ctx: ToolContext) -> dict[str, Any] | ToolResult:
    presentation_mode = str(
        tool_input.get("presentation_mode") or "recommendations"
    ).strip().casefold()
    if presentation_mode not in {"recommendations", "details"}:
        return ToolResult(
            ok=False, error="presentation_mode must be recommendations or details", internal_diagnostic=True
        )
    framing_limit = MAX_PRESENTATION_FRAMING_CHARS
    if tool_input.get("research_used") is True:
        framing_limit = MAX_RESEARCH_PRESENTATION_FRAMING_CHARS
    lead_in, follow_up, framing_error = validated_framing(
        tool_input, lead_in_max_chars=framing_limit
    )
    if framing_error:
        return ToolResult(ok=False, error=framing_error, internal_diagnostic=True)
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    if not session_id:
        return ToolResult(ok=False, error="session is required for place presentation")
    set_id = str(tool_input.get("discovery_set_id") or "").strip()
    record = discovery_store.load_discovery_set(set_id, session_id=session_id)
    if record is None:
        return ToolResult(
            ok=False,
            error="discovery set is unknown, expired, or not owned by this session",
            internal_diagnostic=True,
        )
    evidence = getattr(ctx, "turn_evidence", None)
    if presentation_mode == "details":
        can_claim_research = bool(
            evidence is not None
            and callable(getattr(evidence, "can_claim_research_used", None))
            and evidence.can_claim_research_used()
        )
        if tool_input.get("research_used") is not True or not can_claim_research:
            return ToolResult(
                ok=False,
                error=(
                    "details requires successful current-turn research; "
                    "verify or research the place before presenting details"
                ),
                internal_diagnostic=True,
            )
    goal_key = _place_goal_key(tool_input, ctx)
    if presentation_mode == "details" and can_claim_research:
        rebound = _rebind_researched_details(
            tool_input,
            evidence=evidence,
            goal_key=goal_key,
            set_id=set_id,
            session_id=session_id,
            record=record,
        )
        if isinstance(rebound, ToolResult):
            return rebound
        set_id, record = rebound
    bound = _bind_or_reject_discovery(evidence, goal_key, set_id, ctx)
    if isinstance(bound, ToolResult):
        return bound
    return {
        "lead_in": lead_in,
        "follow_up": follow_up,
        "session_id": session_id,
        "set_id": set_id,
        "record": record,
        "evidence": evidence,
        "goal_key": goal_key,
        "presentation_mode": presentation_mode,
        "presentation_mode_explicit": "presentation_mode" in tool_input,
        "already_presented": ctx.telemetry.get("place_presentation_emitted") is True,
    }


def _rebind_researched_details(
    tool_input: dict,
    *,
    evidence: Any,
    goal_key: str | None,
    set_id: str,
    session_id: str,
    record: dict[str, Any],
) -> tuple[str, dict[str, Any]] | ToolResult:
    if evidence is None or goal_key is None:
        return set_id, record
    authoritative_id = str(evidence.handle_for(goal_key) or "").strip()
    if not authoritative_id or authoritative_id == set_id:
        return set_id, record
    raw_selections = tool_input.get("selections")
    if not isinstance(raw_selections, list) or len(raw_selections) != 1:
        return ToolResult(
            ok=False,
            error="researched details require exactly one verified place selection",
            internal_diagnostic=True,
        )
    raw_selection = raw_selections[0]
    place_id = (
        str(raw_selection.get("place_id") or "").strip()
        if isinstance(raw_selection, dict)
        else ""
    )
    if not discovery_store.is_opaque_place_id(place_id):
        return ToolResult(
            ok=False,
            error="researched details require one opaque verified place id",
            internal_diagnostic=True,
        )
    authoritative_record = discovery_store.load_discovery_set(
        authoritative_id, session_id=session_id
    )
    if authoritative_record is None:
        return ToolResult(
            ok=False,
            error="authoritative verified discovery set is unknown, expired, or not owned by this session",
            internal_diagnostic=True,
        )
    authoritative_ids = {
        str(place.get("place_id") or "")
        for place in authoritative_record.get("places") or []
        if isinstance(place, dict) and place.get("place_id")
    }
    if place_id not in authoritative_ids:
        return ToolResult(
            ok=False,
            error="place id is not in the authoritative verified discovery set",
            internal_diagnostic=True,
        )
    return authoritative_id, authoritative_record


def _bind_or_reject_discovery(
    evidence: Any, goal_key: str | None, set_id: str, ctx: ToolContext
) -> ToolResult | None:
    if evidence is None or goal_key is None:
        return None
    if evidence.handle_for(goal_key) == set_id:
        return None
    trip_state = (
        trip_state_module.get_trip_state(ctx.session)
        if isinstance(ctx.session, dict)
        else {}
    )
    active_set_id = str(trip_state.get("active_discovery_set_id") or "").strip()
    contract = evidence.turn_contract
    dependencies_ready = bool(
        contract is not None and contract.dependencies_ready(goal_key, evidence)
    )
    if (
        evidence.state_for(goal_key) == GoalState.PENDING
        and active_set_id == set_id
        and dependencies_ready
    ):
        evidence.record_goal_handle(goal_key, set_id)
        evidence.record_goal(goal_key, GoalState.EVIDENCE_READY, attempted=True)
        return None
    return ToolResult(
        ok=False,
        error="discovery set does not belong to this place goal",
        internal_diagnostic=True,
    )


def _destination_selection_replay_allowed(
    owned: dict[str, Any], selections: list[dict[str, Any]], ctx: ToolContext
) -> bool:
    """Allow one intentional, typed destination selection of a shown place."""

    if len(selections) != 1:
        return False
    place_id = str(selections[0].get("place_id") or "").strip()
    if not discovery_store.is_opaque_place_id(place_id):
        return False
    evidence = owned.get("evidence")
    goal_key = owned.get("goal_key")
    contract = getattr(evidence, "turn_contract", None)
    goal = contract.get_goal(goal_key) if contract is not None and goal_key else None
    if goal is None or goal.kind != GoalKind.DESTINATION_SELECTION:
        return False
    set_id = str(owned.get("set_id") or "").strip()
    if not set_id or evidence.handle_for(goal_key) != set_id:
        return False
    state = ctx.session.get("trip_state") if isinstance(ctx.session, dict) else None
    return (
        isinstance(state, dict)
        and str(state.get("active_discovery_set_id") or "").strip() == set_id
    )


def _selected_places(
    tool_input: dict, owned: dict[str, Any], ctx: ToolContext
) -> dict[str, Any] | ToolResult:
    places = [
        place
        for place in (owned["record"].get("places") or [])
        if isinstance(place, dict)
    ]
    by_id = {
        str(place.get("place_id") or ""): place
        for place in places
        if place.get("place_id")
    }
    selections, error = _validated_selections(tool_input.get("selections"), by_id)
    if error:
        return ToolResult(ok=False, error=error, internal_diagnostic=True)
    evidence = owned["evidence"]
    research_used = tool_input.get("research_used") is True
    if evidence is not None and evidence.web_research_required and not research_used:
        return ToolResult(
            ok=False, error="verified place details require current-turn research", internal_diagnostic=True
        )
    if research_used and (evidence is None or not evidence.can_claim_research_used()):
        return ToolResult(ok=False, error="research_used requires current-turn research", internal_diagnostic=True)
    presented_ids = {
        str(entry.get("canonical_identity") or "")
        for entry in discovery_store.presented_entity_registry(ctx.session)
        if entry.get("canonical_identity")
    }
    repeated = [
        place
        for place in selections
        if discovery_store._identity_key(place) in presented_ids
    ]
    mode = owned["presentation_mode"]
    if (
        mode == "recommendations"
        and not owned["presentation_mode_explicit"]
        and research_used
        and repeated
    ):
        mode = owned["presentation_mode"] = "details"
    if mode == "details":
        if len(selections) != 1 or not repeated:
            return ToolResult(ok=False, error="details requires exactly one shown place", internal_diagnostic=True)
        if not owned["lead_in"]:
            return ToolResult(
                ok=False, error="details requires concise grounded framing", internal_diagnostic=True
            )
    elif repeated and not owned["already_presented"]:
        if not _destination_selection_replay_allowed(owned, selections, ctx):
            return ToolResult(
                ok=False,
                error="recommendations cannot repeat a shown place",
                internal_diagnostic=True,
            )
    if research_used:
        if not owned["lead_in"]:
            return ToolResult(
                ok=False,
                error="research_used requires a concise current detail in lead_in",
                internal_diagnostic=True,
            )
    owned["selections"] = _normalize_reasons(selections, places)
    owned["research_used"] = research_used
    return owned


async def _emit_place_presentation(
    owned: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    selections = owned["selections"]
    set_id = owned["set_id"]
    presented = [
        {"place_id": place["place_id"], "reason": place["reason"]}
        for place in selections
    ]
    if owned["already_presented"] and not owned["research_used"]:
        return ToolResult(
            ok=True,
            data={
                "discovery_set_id": set_id,
                "presented": presented,
                "already_presented": True,
                "passenger_text": "",
                "lead_in": "",
                "follow_up": "",
            },
            summary="Place options already shown",
        )
    limit = 5 if str(getattr(ctx, "agent_mode", "") or "auto") != "quick" else 3
    selections = selections[:limit]
    details_only = owned["presentation_mode"] == "details"
    queue_text, queue_sources = await _queue_presentation(
        selections,
        record=owned["record"],
        ctx=ctx,
    )
    if isinstance(ctx.session, dict):
        selections = discovery_store.record_presented_places(
            ctx.session,
            session_id=owned["session_id"],
            discovery_set_id=set_id,
            places=selections,
        )
        presented = [
            {"place_id": place["place_id"], "reason": place["reason"]}
            for place in selections
        ]
    lead_in = owned["lead_in"]
    text = lead_in if details_only else render_place_list(
        selections,
        source_label="" if lead_in else None,
        coverage_note=_coverage_note(owned["record"]),
    )
    if isinstance(ctx.session, dict) and len(selections) == 1:
        trip_state_module.bind_selected_place(ctx.session, str(selections[0]["place_id"]))
    evidence = owned["evidence"]
    goal_key = owned["goal_key"]
    if evidence is not None and goal_key is not None:
        record_goal = getattr(evidence, "record_goal", None)
        if callable(record_goal):
            record_goal(goal_key, GoalState.SATISFIED, attempted=True, presented=True)
    # Presenters cannot invent a continuation. The schema field remains stable,
    # but only server-owned continuation state may offer more work.
    follow_up = ""
    ctx.telemetry["place_presentation_emitted"] = True
    canonical_events: list[agent_events.AgentEvent] = []
    if not details_only:
        canonical_events.append(agent_events.TokenEvent(text=text))
    if queue_text:
        canonical_events.append(agent_events.TokenEvent(text=f"\n\n{queue_text}"))
    if queue_sources:
        canonical_events.append(
            agent_events.SourcesEvent(
                turn_id=ctx.turn_id,
                sources=tuple(
                    {"title": source.title, "url": source.url}
                    for source in queue_sources
                ),
            )
        )
    return ToolResult(
        ok=True,
        data={
            "discovery_set_id": set_id,
            "presented": presented,
            "passenger_text": text,
            "lead_in": lead_in,
            "follow_up": follow_up,
        },
        summary="Place options ready",
        events=framed_events(
            canonical_events,
            lead_in,
            follow_up,
        ),
    )


async def _queue_presentation(
    selections: list[dict[str, Any]],
    *,
    record: dict[str, Any],
    ctx: ToolContext,
) -> tuple[str, tuple[damn_lines.QueueSource, ...]]:
    queue_context = discovery_store.sanitized_queue_context(
        record.get("queue_context")
    )
    mode = str((queue_context or {}).get("mode") or "ignore")
    if mode == "ignore":
        return "", ()

    when = _presentation_time(ctx.now_et)
    damn_lines.schedule_history_warmup(now=when)
    supported_ids = [
        place_id
        for place in selections
        if (place_id := str(place.get("provider_place_id") or "").strip())
        and damn_lines.get_supported_venue(place_id) is not None
    ]
    observations: dict[str, damn_lines.QueueObservation] = {}
    if supported_ids and mode != "historical":
        try:
            current = await damn_lines.get_current_observations(
                supported_ids, now=when
            )
            observations = current.observations
        except Exception:
            observations = {}

    notes: list[str] = []
    sourced_ids: list[str] = []
    for place in selections:
        name = str(place.get("name") or "this place").strip()
        place_id = str(place.get("provider_place_id") or "").strip()
        supported = damn_lines.get_supported_venue(place_id) is not None
        if not supported:
            if mode in {"decision", "historical"}:
                notes.append(f"There is no queue coverage for {name}.")
            continue

        if mode == "historical":
            pattern = _historical_pattern(place_id, when)
            notes.append(
                _historical_note(name, pattern)
                if pattern is not None
                else f"There is no historical queue information for {name}."
            )
            sourced_ids.append(place_id)
            continue

        observation = observations.get(place_id)
        if observation is not None:
            notes.append(_current_queue_note(name, observation))
            sourced_ids.append(place_id)
            continue

        pattern = (
            _historical_pattern(place_id, when)
            if place.get("open_status") == "open"
            else None
        )
        if pattern is not None:
            notes.append(
                f"There is no live queue information for {name}. "
                f"{_historical_note(name, pattern)}"
            )
            sourced_ids.append(place_id)
        elif mode == "decision":
            notes.append(f"There is no live queue information for {name}.")
            sourced_ids.append(place_id)

    return "\n".join(notes), damn_lines.source_for_places(sourced_ids)


def _presentation_time(value: object) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(_NYC)
    except ValueError:
        pass
    return datetime.now(_NYC)


def _historical_pattern(
    place_id: str, when: datetime
) -> damn_lines.HistoricalQueuePattern | None:
    try:
        return damn_lines.get_historical_pattern(place_id, when, now=when)
    except (TypeError, ValueError):
        return None


def _current_queue_note(
    name: str, observation: damn_lines.QueueObservation
) -> str:
    observed = _clock_time(observation.captured_at.astimezone(_NYC))
    wait = observation.wait_minutes
    people = observation.people_count
    if wait is not None:
        note = (
            f"The latest estimated wait for {name} was about "
            f"{_number(wait)} minutes"
        )
        if people is not None:
            note += f", with {people} people in line"
        return f"{note}, as of {observed}."
    return f"The latest line count for {name} was {people} people as of {observed}."


def _historical_note(
    name: str, pattern: damn_lines.HistoricalQueuePattern
) -> str:
    hour = _hour_time(pattern.hour)
    wait = pattern.wait_minutes_mean
    people = pattern.people_mean
    if pattern.comparable_dates == 1:
        prefix = f"On {_calendar_date(pattern.date_from)} around {hour}, "
        if wait is not None:
            note = f"an estimated wait of about {_number(wait)} minutes was recorded for {name}"
        else:
            note = f"an average line count of {_number(people)} people was recorded for {name}"
    else:
        weekday = pattern.date_from.strftime("%A")
        prefix = (
            f"Across {pattern.comparable_dates} recorded {weekday} periods "
            f"around {hour}, "
        )
        if wait is not None:
            note = f"the historical average wait for {name} was about {_number(wait)} minutes"
        else:
            note = f"the historical average line count for {name} was {_number(people)} people"
    if wait is not None and people is not None:
        note += f", with an average of {_number(people)} people in line"
    return f"{prefix}{note}."


def _number(value: float | None) -> str:
    if value is None:
        return "0"
    return f"{round(value, 1):.1f}".rstrip("0").rstrip(".")


def _clock_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _hour_time(hour: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12} {suffix}"


def _calendar_date(value) -> str:
    return f"{value.strftime('%B')} {value.day}"


def render_place_list(
    selections: list[dict[str, Any]],
    *,
    source_label: str | None,
    coverage_note: str | None = None,
) -> str:
    heading = (
        "Here are a few options:"
        if source_label is None
        else str(source_label).strip()
    )
    lines = [heading, ""] if heading else []
    for index, place in enumerate(selections, start=1):
        facts = _facts(place)
        suffix = f" — {' · '.join(facts)}" if facts else ""
        lines.append(f"{index}. {place.get('name') or 'Place'}{suffix}")
    if coverage_note:
        lines.extend(["", coverage_note])
    return "\n".join(lines).strip()


def try_deterministic_fallback(
    evidence: Any, *, session_id: str, limit: int
) -> str | None:
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None
    if any(
        goal.kind == GoalKind.ROUTE
        and _goal_is_unresolved(evidence, goal.goal_key)
        for goal in contract.goals
    ):
        return None
    place_goal_ready = any(
        goal.kind in {
            GoalKind.PLACE_RECOMMENDATION,
            GoalKind.DESTINATION_SELECTION,
        }
        and evidence.state_for(goal.goal_key) == GoalState.EVIDENCE_READY
        and not evidence.presented_for(goal.goal_key)
        for goal in contract.goals
    )
    if not place_goal_ready or not getattr(evidence, "discovery_set_id", None):
        return None
    record = discovery_store.load_discovery_set(
        evidence.discovery_set_id, session_id=session_id
    )
    if record is None:
        return None
    text = deterministic_fallback_text(record, limit=limit)
    mark_terminal = getattr(evidence, "mark_terminal", None)
    if callable(mark_terminal):
        mark_terminal(
            "deterministic_fallback",
            selection_source="deterministic_fallback",
        )
    return text


def _goal_is_unresolved(evidence: Any, goal_key: str) -> bool:
    state = evidence.state_for(goal_key)
    if state in {GoalState.SATISFIED, GoalState.CANCELLED_BY_RIDER, GoalState.SUPERSEDED}:
        return False
    return not (
        state == GoalState.EVIDENCE_READY and evidence.presented_for(goal_key)
    )


def deterministic_fallback_text(record: dict[str, Any], limit: int = 3) -> str:
    places = [
        place for place in (record.get("places") or []) if isinstance(place, dict)
    ]
    selected = []
    for place in places[:limit]:
        item = dict(place)
        item["reason"] = ""
        selected.append(item)
    return render_place_list(
        selected,
        source_label="Here are a few options:",
        coverage_note=_coverage_note(record),
    )


def _coverage_note(record: dict[str, Any]) -> str | None:
    coverage = record.get("coverage") if isinstance(record.get("coverage"), dict) else {}
    if coverage.get("status") == "partial":
        unavailable = [
            str(area).strip()
            for area in (coverage.get("unavailable_areas") or [])
            if str(area).strip()
        ]
        if unavailable:
            return f"Search coverage was limited in {', '.join(unavailable)}."
        return "Some requested areas were unavailable, so the list may be incomplete."
    return None


def _validated_selections(
    raw: object,
    by_id: dict[str, dict],
) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(raw, list) or not raw:
        return [], "one through five unique place selections are required"
    if len(raw) > 5:
        return [], "one through five unique place selections are required"
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            return [], "each selection must include place_id and reason"
        place_id = str(item.get("place_id") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if reason not in REASON_CODES:
            return [], "unsupported place reason"
        if not place_id or place_id not in by_id:
            return [], "place id is unknown for this discovery set"
        if place_id in seen:
            return [], "duplicate place selections are not allowed"
        seen.add(place_id)
        place = dict(by_id[place_id])
        place["reason"] = reason
        selected.append(place)
    return selected, None


def _normalize_reasons(selections: list[dict], places: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for index, selected in enumerate(selections):
        place = dict(selected)
        reason = place["reason"]
        supported = (
            reason not in OBJECTIVE_REASONS
            or (
                reason == "top_pick"
                and index == 0
                and _is_extreme(place, places, "baseline_score", maximum=True)
            )
            or (reason == "open_now" and place.get("open_status") == "open")
            or (
                reason == "highest_rating"
                and _is_extreme(place, places, "rating", maximum=True)
            )
            or (
                reason == "most_reviewed"
                and _is_extreme(place, places, "review_count", maximum=True)
            )
            or (
                reason == "budget_friendly"
                and _is_extreme(place, places, "price_level", maximum=False)
            )
        )
        if not supported:
            place["reason"] = "preference_match"
        normalized.append(place)
    return normalized


def _is_extreme(place: dict, places: list[dict], field: str, *, maximum: bool) -> bool:
    values = [_finite(item.get(field)) for item in places]
    known = [value for value in values if value is not None]
    current = _finite(place.get(field))
    if current is None or not known:
        return False
    target = max(known) if maximum else min(known)
    return current == target


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _facts(place: dict) -> list[str]:
    facts: list[str] = []
    location = str(
        place.get("neighborhood") or place.get("borough") or place.get("address") or ""
    ).strip()
    if location:
        facts.append(location)
    rating = _finite(place.get("rating"))
    if rating is not None:
        facts.append(f"{rating:.1f}★")
    open_status = str(place.get("open_status") or "")
    if open_status == "open":
        facts.append("open now")
    elif open_status == "closed":
        facts.append("closed now")
    return facts
