"""Present one validated server-owned route candidate as the route card."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.services.agent import events as agent_events
from app.services.agent import transcript_store
from app.services.agent.passenger_output import framed_events, validated_framing
from app.services.agent.turn.contract import GoalKind, GoalState
from app.services.agent.model.output_projection import project_presented_route
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.route.present_route_commit import (
    record_presentation as _record_presentation,
    reserve_and_commit as _reserve_and_commit,
)
from app.services.agent.tools.route.present_route_state import (
    ValidatedRoutePresentation,
    canonical_facts,
    owned_candidate as _owned_candidate,
    rebind_to_entry,
)
from app.services.trips.route_incidents.scan import (
    contains_unsafe_incident_clear,
    incident_scan_is_complete,
)
from app.services.trips.selection_decision import (
    evaluate_candidate_decision,
    evaluate_dominated_selection,
    select_fallback_candidate,
)

PRESENT_ROUTE_SCHEMA = {
    "name": "present_route",
    "description": (
        "Present exactly one previously prepared candidate. Use only the "
        "opaque candidate_id returned by prepare_route_options; the server "
        "owns the canonical route, timing, geometry, and transfer facts."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "candidate_id": {
                "type": "string",
                "description": "Server-issued candidate id.",
            },
            "commit_scenario": {
                "type": "boolean",
                "description": "Commit a temporary what-if only when the rider explicitly asks.",
            },
            "goal_key": {
                "type": "string",
                "description": "Turn goal associated with this route presentation.",
            },
            "lead_in": {
                "type": "string",
                "description": (
                    "Concise rider-facing explanation of why this selected candidate was "
                    "chosen, grounded in reason_code. Every successful route needs one "
                    "concrete supported qualitative reason, including an unqualified "
                    "request. Qualitative transfer, walking, disruption, crowd, "
                    "accessibility, and Stage A relevance wording is allowed when the "
                    "server validates that reason. Name the supported factor directly; "
                    "generic wording about satisfying the trip or being practical is "
                    "not an explanation. When no comparative factor or explicit "
                    "rider constraint is supported, explain in plain passenger "
                    "language that the options were close or nothing had a clear "
                    "edge, so you chose one that covers what the rider asked for. "
                    "Do not invent a specific advantage or expose backend language "
                    "such as hard-valid, evidence, tradeoff advantage, constraints, "
                    "or verified choice. Use route-shape wording such as "
                    "straightforward or direct only when the selected canonical "
                    "itinerary supports it; hard validity alone does not prove "
                    "route shape. Do not restate canonical facts: no "
                    "digits, transit line names, exact times, counts, or invented "
                    "itinerary details."
                ),
            },
            "follow_up": {
                "type": "string",
                "description": (
                    "The server currently supplies no eligible follow-up signal, so "
                    "pass an empty string."
                ),
            },
            "reason_code": {
                "type": "string",
                "enum": [
                    "fastest",
                    "less_walking",
                    "fewer_transfers",
                    "avoids_active_disruption",
                    "lower_event_crowd_exposure",
                    "meets_hard_constraints",
                    "accessibility",
                    "coverage_gap",
                    "reasonable_local_option",
                ],
                "description": (
                    "One qualitative reason inferred from the selected candidate's finalized "
                    "factors and explained by lead_in. Every successful route presentation "
                    "requires one. For an unqualified request, name a concrete supported "
                    "route-quality distinction rather than only saying it fits, satisfies "
                    "constraints, is best, is practical, or satisfies the trip. Qualitative "
                    "tradeoffs are allowed only when the server validates them against private "
                    "candidate evidence."
                ),
            },
        },
        "required": ["candidate_id", "goal_key", "lead_in", "follow_up", "reason_code"],
        "additionalProperties": False,
    },
}

_REPLAY_LEAD_IN = "Here’s the accepted route again."


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    replay = _accepted_route_replay(tool_input, ctx)
    if replay is not None:
        return _replay_result(replay, ctx)
    presentation = await load_validated_presentation(tool_input, ctx)
    if isinstance(presentation, ToolResult):
        return presentation
    await ctx.emit_progress("comparing_options", "active")
    projected = _project(presentation, ctx)
    if not projected.ok:
        await ctx.emit_progress("comparing_options", "complete")
        return projected
    reserved = _reserve_and_commit(presentation, projected, ctx)
    if reserved is not None:
        await ctx.emit_progress("comparing_options", "complete")
        return reserved
    _record_presentation(presentation, ctx)
    await ctx.emit_progress("comparing_options", "complete")
    return projected


def _project(presentation: ValidatedRoutePresentation, ctx: ToolContext) -> ToolResult:
    """Render canonical route facts while keeping private selection metadata hidden."""
    from app.services.agent.tools.route.route_projection import project_canonical_route

    projected = project_canonical_route(presentation, ctx)
    if not projected.ok:
        return projected
    if isinstance(projected.data, dict):
        projected.data = project_presented_route(projected.data)
        projected.data.update(
            {
                "selection_source": presentation.selection_source,
                "route_status": presentation.status,
                "lead_in": presentation.lead_in,
                "follow_up": presentation.follow_up,
                "reason_code": presentation.reason_code or "",
            }
        )
    projected.events = framed_events(
        projected.events,
        presentation.lead_in,
        presentation.follow_up,
    )
    return projected


async def load_validated_presentation(
    tool_input: dict, ctx: ToolContext
) -> ValidatedRoutePresentation | ToolResult:
    owned = _owned_candidate(tool_input, ctx)
    if isinstance(owned, ToolResult):
        return owned
    facts = canonical_facts_with_fallback(owned, ctx)
    if isinstance(facts, ToolResult):
        return facts
    return with_optional_framing(facts, tool_input, ctx)


def _accepted_route_replay(tool_input: dict, ctx: ToolContext) -> dict | None:
    if not isinstance(ctx.session, dict):
        return None
    goal_key = str(tool_input.get("goal_key") or "").strip()
    candidate_id = str(tool_input.get("candidate_id") or "").strip()
    evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(evidence, "turn_contract", None)
    goal = contract.get_goal(goal_key) if contract is not None else None
    if (
        not goal_key
        or not candidate_id
        or goal is None
        or goal.kind != GoalKind.ROUTE
    ):
        return None
    state = ctx.session.get("trip_state")
    selected_id = (
        str(state.get("selected_candidate_id") or "").strip()
        if isinstance(state, dict)
        else ""
    )
    if not selected_id or candidate_id != selected_id:
        return None
    card = transcript_store.active_accepted_route_card(ctx.session)
    return {"card": card, "goal_key": goal_key} if isinstance(card, dict) else None


def _replay_result(card: dict, ctx: ToolContext) -> ToolResult:
    replay_card = card["card"]
    goal_key = card["goal_key"]
    payload = copy.deepcopy(replay_card)
    payload["turn_id"] = ctx.turn_id
    event = agent_events.RouteCardEvent(
        card_id=payload["card_id"],
        turn_id=payload["turn_id"],
        role=payload["role"],
        origin=payload["origin"],
        destination=payload["destination"],
        summary=payload["summary"],
        route=payload["route"],
        alerts=payload["alerts"],
        leg_label=payload.get("leg_label"),
        depart_iso=payload.get("depart_iso"),
        itinerary=payload.get("itinerary"),
        selection_decision=payload.get("selection_decision"),
    )
    evidence = getattr(ctx, "turn_evidence", None)
    if evidence is not None and goal_key:
        record_goal = getattr(evidence, "record_goal", None)
        if callable(record_goal):
            record_goal(goal_key, GoalState.SATISFIED, attempted=True, presented=True)
    return ToolResult(
        ok=True,
        data={
            "presented": True,
            "already_presented": True,
            "goal_key": goal_key,
            "presentation_outcome": "accepted_route_replay",
            "card_id": event.card_id,
            "route_card": event.to_data(),
        },
        summary="Replayed accepted route",
        events=[agent_events.TokenEvent(text=f"{_REPLAY_LEAD_IN}\n\n"), event],
        session_route_cards=[],
    )


_ROUTE_REASON_CODES = frozenset(
    {
        "fastest",
        "less_walking",
        "fewer_transfers",
        "avoids_active_disruption",
        "lower_event_crowd_exposure",
        "meets_hard_constraints",
        "accessibility",
        "coverage_gap",
        "reasonable_local_option",
    }
)
_UNSAFE_ROUTE_FRAMING_RE = re.compile(
    r"(?:"
    r"\b\d+(?:\.\d+)?\s*(?:min(?:ute)?s?|hr(?:s|ours)?|hours?)\b"
    r"|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|\d+)\s+(?:transfers?|stops?)\b"
    r"|\b(?:the\s+)?[A-Z]\d{0,3}(?:-[A-Z]+)?\s+"
    r"(?:train|line|bus|subway|route)\b"
    r"|\b(?:(?i:take|ride|use|via|on|avoid))\s+(?:the\s+)?"
    r"[A-Z]\d{0,3}(?:-[A-Z]+)?\b"
    r")",
)
_GENERIC_ROUTE_FRAMING_RE = re.compile(
    r"\b(?:fit|fits|fitting|satisfy|satisfies|satisfying)\b"
    r".{0,80}\b(?:trip|request|constraints?|preferences?|what\s+you\s+asked)\b"
    r"|\bverified\s+(?:route|choice|option)\b",
    re.IGNORECASE,
)
_GENERIC_QUALIFIER_RE = re.compile(
    r"\b(?:best|practical)(?:\s+\w+){0,3}\s+(?:option|route|choice|fit)"
    r"\s*[.!?…]*$",
    re.IGNORECASE,
)
_UNGROUNDED_SUCCESS_RE = re.compile(
    r"\bwithout\s+any\s+(?:complications?|issues?|problems?)\b",
    re.IGNORECASE,
)
_NEUTRAL_ROUTE_FALLBACK = "Here's the route I found."


def canonical_facts_with_fallback(
    owned: dict[str, Any], ctx: ToolContext
) -> dict[str, Any] | ToolResult:
    """Load canonical facts and correct one invalid or dominated model choice."""

    facts = canonical_facts(owned)
    hard_invalid = (
        isinstance(facts, ToolResult)
        and facts.error
        == "selected candidate does not satisfy the server-owned hard constraints"
    )
    dominated = (
        evaluate_dominated_selection(facts["record"], facts["entry"])
        if isinstance(facts, dict)
        else None
    )
    if not hard_invalid and not (dominated and dominated["challenged"]):
        return facts
    correction_facts = facts if isinstance(facts, dict) else owned
    if _request_decision_correction(ctx, correction_facts):
        if dominated and dominated["challenged"]:
            preference = str(dominated["preference"] or "").casefold().replace("_", "-")
            error = (
                f"The selected candidate is clearly dominated for the rider's explicit "
                f"{preference} preference. Choose again from the existing Candidate Set. "
                "Do not prepare routes again."
            )
        else:
            error = (
                "The selected candidate does not satisfy the server-owned hard "
                "constraints. Retry present_route once with another candidate from "
                "the existing candidate set. Do not prepare routes again."
            )
        return ToolResult(
            ok=False,
            error=error,
            internal_diagnostic=True,
        )
    fallback = select_fallback_candidate(owned["record"])
    fallback_entry = fallback["entry"] if fallback is not None else None
    rebound = rebind_to_entry(owned, fallback_entry)
    if rebound is None:
        return facts
    fallback_facts = canonical_facts(rebound)
    if isinstance(fallback_facts, dict):
        fallback_facts["selection_source"] = "deterministic_fallback"
        fallback_facts["selection_reason"] = "deterministic_fallback"
    return fallback_facts


def with_optional_framing(
    facts: dict[str, Any], tool_input: dict, ctx: ToolContext
) -> ValidatedRoutePresentation | ToolResult:
    """Bind a validated structured reason to passenger-facing framing."""
    lead_in, follow_up, reason_code, explanation_error = _requested_framing(
        facts, tool_input
    )
    correction = _framing_correction(ctx, facts, explanation_error)
    if correction is not None:
        return correction
    facts, lead_in, follow_up, reason_code, selection_source, selection_reason = (
        _apply_selection_framing(
            facts,
            lead_in,
            follow_up,
            reason_code,
            explanation_error,
        )
    )
    follow_up = ""
    evaluation = evaluate_candidate_decision(facts["record"], facts["entry"])
    structured_reason = evaluation["structured_reasons"].get(reason_code or "")
    return _presentation_from_framing(
        facts,
        lead_in=lead_in,
        follow_up=follow_up,
        reason_code=reason_code,
        structured_reason=structured_reason,
        selection_source=selection_source,
        selection_reason=selection_reason,
    )


def _requested_framing(
    facts: dict[str, Any], tool_input: dict
) -> tuple[str, str, str | None, str | None]:
    lead_in, follow_up, framing_error = validated_framing(tool_input)
    candidate_evidence = facts.get("candidate_evidence")
    incident_metadata = (
        candidate_evidence.get("incident_scan_metadata")
        if isinstance(candidate_evidence, dict)
        else None
    ) or facts["record"].get("incident_scan_metadata") or {}
    explanation_error = framing_error or _route_framing_error(
        lead_in,
        follow_up,
        incident_metadata,
    )
    raw_reason_code = tool_input.get("reason_code")
    reason_code = (
        raw_reason_code.strip()
        if isinstance(raw_reason_code, str) and raw_reason_code.strip()
        else None
    )
    if raw_reason_code not in (None, "") and reason_code is None:
        explanation_error = explanation_error or "reason_code is invalid"
    if not lead_in:
        explanation_error = explanation_error or (
            "route presentation requires a concise grounded explanation"
        )
    explanation_error = explanation_error or _route_reason_error(
        reason_code=reason_code,
        lead_in=lead_in,
        follow_up=follow_up,
        record=facts["record"],
        entry=facts["entry"],
    )
    return lead_in, follow_up, reason_code, explanation_error


def _framing_correction(
    ctx: ToolContext,
    facts: dict[str, Any],
    explanation_error: str | None,
) -> ToolResult | None:
    if not explanation_error or not _request_decision_correction(ctx, facts):
        return None
    supported = ", ".join(
        sorted(_supported_reason_codes(facts["record"], facts["entry"]))
    )
    return ToolResult(
        ok=False,
        error=(
            f"{explanation_error}. Retry present_route once using a non-empty "
            "qualitative explanation and one of these reason_code values "
            f"supported by the selected candidate: {supported}. "
            "Do not prepare routes again."
        ),
        internal_diagnostic=True,
    )


def _apply_selection_framing(
    facts: dict[str, Any],
    lead_in: str,
    follow_up: str,
    reason_code: str | None,
    explanation_error: str | None,
) -> tuple[dict[str, Any], str, str, str | None, str, str]:
    selection_source = str(facts.get("selection_source") or "model")
    selection_reason = str(facts.get("selection_reason") or "outer_agent_selection")
    if explanation_error or selection_source == "deterministic_fallback":
        fallback = _deterministic_fallback(facts)
        if fallback is None:
            return facts, "", "", None, selection_source, selection_reason
        facts, reason_code = fallback
        lead_in = _bound_reason_lead_in(
            facts["record"], facts["entry"], reason_code, ""
        )
        return (
            facts,
            lead_in,
            "",
            reason_code,
            "deterministic_fallback",
            "deterministic_fallback",
        )
    lead_in = _bound_reason_lead_in(
        facts["record"], facts["entry"], reason_code, lead_in
    )
    return facts, lead_in, follow_up, reason_code, selection_source, selection_reason


def _presentation_from_framing(
    facts: dict[str, Any],
    *,
    lead_in: str,
    follow_up: str,
    reason_code: str | None,
    structured_reason: dict[str, Any] | None,
    selection_source: str,
    selection_reason: str,
) -> ValidatedRoutePresentation:
    payload = {
        key: facts[key]
        for key in (
            "session_id",
            "session",
            "candidate_set_id",
            "candidate_id",
            "record",
            "entry",
            "chosen_index",
            "parsed_routes",
            "canonical_itinerary",
            "first_route",
            "origin_place",
            "destination_place",
            "candidate_evidence",
            "evidence_envelopes",
            "scenario_mode",
            "commit_scenario",
            "reuses_temporary_preview",
            "status",
            "tool_input_body",
            "goal_key",
            "scored",
            "first_leg_context",
            "timings",
            "plan_origin",
        )
    }
    payload.update(
        lead_in=lead_in,
        follow_up=follow_up,
        reason_code=reason_code,
        structured_reason=(
            dict(structured_reason) if isinstance(structured_reason, dict) else None
        ),
        selection_source=selection_source,
        selection_reason=selection_reason,
    )
    return ValidatedRoutePresentation(**payload)


def _deterministic_fallback(facts: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    decision = select_fallback_candidate(facts["record"])
    if decision is None:
        return None
    entry = decision["entry"]
    fallback_facts = facts
    if entry is not facts.get("entry"):
        rebound = rebind_to_entry(facts, entry)
        if rebound is None:
            return None
        rebuilt = canonical_facts(rebound)
        if isinstance(rebuilt, ToolResult):
            return None
        fallback_facts = rebuilt
    return fallback_facts, decision["reason_code"]


def _route_reason_error(
    *,
    reason_code: str | None,
    lead_in: str,
    follow_up: str,
    record: dict[str, Any],
    entry: dict[str, Any],
) -> str | None:
    explanatory = bool(lead_in or (follow_up and not follow_up.rstrip().endswith("?")))
    if not explanatory and not reason_code:
        return None
    if explanatory and not reason_code:
        return "route-choice framing requires one supported reason_code"
    if reason_code not in _ROUTE_REASON_CODES:
        return "reason_code is not supported for route framing"
    if reason_code not in _supported_reason_codes(record, entry):
        return "reason_code is not supported by the selected canonical route"
    generic_error = _generic_framing_error(lead_in)
    if generic_error:
        return generic_error
    reason_claim_error = _structured_reason_claim_error(
        lead_in, reason_code, record, entry
    )
    if reason_claim_error:
        return reason_claim_error
    return None


def _generic_framing_error(lead_in: str) -> str | None:
    normalized = " ".join(lead_in.split())
    if _GENERIC_ROUTE_FRAMING_RE.search(normalized):
        return (
            "lead_in must name a concrete supported route factor instead of "
            "generic fit or satisfaction wording"
        )
    if _GENERIC_QUALIFIER_RE.search(normalized):
        return (
            "lead_in must name a concrete supported route factor instead of "
            "generic best or practical wording"
        )
    if _UNGROUNDED_SUCCESS_RE.search(normalized):
        return (
            "lead_in must name a concrete supported route factor instead of "
            "ungrounded success wording"
        )
    return None


def _structured_reason_claim_error(
    lead_in: str,
    reason_code: str | None,
    record: dict[str, Any],
    entry: dict[str, Any],
) -> str | None:
    """Reject an explicit reason-code claim that names another factor.

    The model may wrap a validated structured reason in natural language, but
    the prose cannot become a second recommendation rationale.  Reason-code
    names are the canonical vocabulary here; no passenger-language synonym
    list is maintained at this boundary.
    """

    if not reason_code:
        return None
    evaluation = evaluate_candidate_decision(record, entry)
    if not isinstance(evaluation["structured_reasons"].get(reason_code), dict):
        return None
    normalized = " ".join(lead_in.casefold().split())
    for code in _ROUTE_REASON_CODES:
        if code == reason_code:
            continue
        claim = code.replace("_", " ")
        if re.search(rf"\b{re.escape(claim)}\b", normalized):
            return (
                "lead_in names a different route factor than the validated "
                f"structured reason_code {reason_code}"
            )
    if reason_code == "meets_hard_constraints":
        # A comparative claim is not implied by hard validity.  `fastest` is
        # checked even when it is not a supported alternative reason, because
        # that explicit claim cannot be grounded by this reason code.
        if re.search(r"\bfastest\b", normalized):
            return (
                "lead_in names a different route factor than the validated "
                "structured reason_code meets_hard_constraints"
            )
    return None


def _bound_reason_lead_in(
    record: dict[str, Any],
    entry: dict[str, Any],
    reason_code: str | None,
    lead_in: str,
) -> str:
    evaluation = evaluate_candidate_decision(record, entry)
    limitation = (
        _crowd_limitation(record)
        if evaluation["crowd_limitation_required"]
        else ""
    )
    base = lead_in.strip() or _NEUTRAL_ROUTE_FALLBACK
    if reason_code == "coverage_gap" and evaluation["has_missing_branch"]:
        missing_branch_note = (
            "One or more requested branches could not be checked."
        )
        if missing_branch_note.casefold() not in base.casefold():
            base = f"{base} {missing_branch_note}".strip()
    if not limitation:
        return base
    return f"{base} {limitation}".strip() if base else limitation


def _request_decision_correction(ctx: ToolContext, facts: dict[str, Any]) -> bool:
    """Consume the one model-correction attempt owned by this candidate set."""

    telemetry = getattr(ctx, "telemetry", None)
    if not isinstance(telemetry, dict):
        telemetry = {}
        ctx.telemetry = telemetry
    attempts = telemetry.setdefault("route_decision_corrections", {})
    key = (
        f"{str(facts.get('goal_key') or '').strip()}:"
        f"{str(facts.get('candidate_set_id') or '').strip()}"
    )
    count = int(attempts.get(key, 0) or 0)
    attempts[key] = count + 1
    return count == 0


def _route_framing_error(
    lead_in: str,
    follow_up: str,
    incident_metadata: dict[str, Any],
) -> str | None:
    for field, value in (("lead_in", lead_in), ("follow_up", follow_up)):
        if _UNSAFE_ROUTE_FRAMING_RE.search(value):
            return (
                f"{field} may provide only qualitative route framing; timing, "
                "transfers, and transit-line facts belong in the card"
            )
        if not incident_scan_is_complete(incident_metadata) and contains_unsafe_incident_clear(value):
            return (
                f"{field} cannot claim clear incident or disruption conditions "
                "while incident coverage is incomplete"
            )
    return None


def _supported_reason_codes(record: dict[str, Any], entry: dict[str, Any]) -> set[str]:
    return evaluate_candidate_decision(record, entry)["supported_reason_codes"]


def _crowd_limitation(record: dict[str, Any]) -> str:
    status = str(record.get("event_evidence_status") or "").strip().casefold()
    if status == "no_relevant_events":
        return (
            "I found an available route; no relevant event crowd evidence was found "
            "for the arrival window."
        )
    return (
        "I found an available route, but crowd conditions for the relevant window "
        "could not be verified."
    )


__all__ = (
    "PRESENT_ROUTE_SCHEMA",
    "ValidatedRoutePresentation",
    "canonical_facts_with_fallback",
    "execute",
    "load_validated_presentation",
    "with_optional_framing",
)
