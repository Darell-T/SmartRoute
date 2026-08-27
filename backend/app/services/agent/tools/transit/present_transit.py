"""Facade for presenting one server-owned transit evidence set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.agent import events as agent_events
from app.services.agent.passenger_output import framed_events, validated_framing
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools.transit.evidence_projection import (
    accessibility_text,
    arrivals_text,
    operation_facts_text,
    renderable_arrival_card,
)
from app.services.agent.turn.contract import GoalState

PRESENT_TRANSIT_SCHEMA = {
    "name": "present_transit",
    "description": (
        "Present the checked transit evidence set; the server owns all status "
        "and arrival facts."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "evidence_set_id": {
                "type": "string",
                "description": "Opaque id returned by check_transit.",
            },
            "goal_key": {
                "type": "string",
                "description": "Turn goal associated with this presentation.",
            },
            "lead_in": {
                "type": "string",
                "description": (
                    "Brief natural interpretation that introduces the canonical "
                    "transit facts without repeating or inventing status, arrival, "
                    "incident, event, crowd, route, or timing facts. Pass an empty "
                    "string when no framing helps."
                ),
            },
            "follow_up": {
                "type": "string",
                "description": (
                    "Use an empty string unless the backend has explicitly made a "
                    "follow-up eligible for this goal and evidence state. Never add "
                    "an automatic question or transit facts."
                ),
            },
        },
        "required": ["evidence_set_id", "goal_key", "lead_in", "follow_up"],
        "additionalProperties": False,
    },
}

_PRESENTABLE_OPERATIONS = {
    "service_status",
    "arrivals",
    "accessibility",
    "fact",
    "area_conditions",
    "event_schedule",
    "venue_crowd_window",
}

def _passenger_text(evidence: dict[str, Any], operation: str) -> str:
    if operation == "service_status":
        return service_status_text(evidence)
    view = StatusView.from_evidence(evidence)
    if operation == "arrivals":
        return arrivals_text(evidence, view.routes)
    if operation == "accessibility":
        return accessibility_text(evidence, view.unknowns)
    return operation_facts_text(operation, evidence.get("operation_facts") or {})


def _build_events(
    ctx: ToolContext,
    evidence: dict[str, Any],
    operation: str,
    text: str,
) -> list:
    if operation != "arrivals":
        result: list[agent_events.AgentEvent] = [agent_events.TokenEvent(text=text)]
        findings = [
            *(
                item
                for item in evidence.get("confirmed_matching_alerts") or []
                if isinstance(item, dict)
            ),
            *(
                item
                for item in evidence.get("incidents") or []
                if isinstance(item, dict) and item.get("confirmed") is True
            ),
        ]
        if (
            operation == "service_status"
            and not evidence.get("checked_routes")
            and (
                any(isinstance(item, dict) for item in findings)
                or any(
                    isinstance(item, dict)
                    for item in evidence.get("unconfirmed_signals") or []
                )
            )
        ):
            result.append(agent_events.TransitStatusActionEvent(turn_id=ctx.turn_id))
        return result
    result = [
        agent_events.ArrivalCardEvent.from_lookup(ctx.turn_id, row)
        for row in evidence.get("results") or []
        if isinstance(row, dict)
        and row.get("route_id")
        and renderable_arrival_card(row)
    ]
    return result or [agent_events.TokenEvent(text=text)]


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    lead_in, follow_up, framing_error = validated_framing(
        {
            "lead_in": tool_input.get("lead_in", ""),
            "follow_up": tool_input.get("follow_up", ""),
        }
    )

    if framing_error:
        return ToolResult(ok=False, error=framing_error, internal_diagnostic=True)
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    evidence_set_id = str(tool_input.get("evidence_set_id") or "").strip()
    goal_key = str(tool_input.get("goal_key") or "").strip()
    if not session_id:
        return ToolResult(ok=False, error="session is required for transit presentation")
    if not evidence_set_id or not goal_key:
        return ToolResult(ok=False, error="evidence_set_id and goal_key are required")
    turn_evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(turn_evidence, "turn_contract", None)
    if contract is not None:
        if contract.get_goal(goal_key) is None:
            return ToolResult(
                ok=False,
                error="goal_key is unknown for this turn contract",
                internal_diagnostic=True,
            )
        if turn_evidence.handle_for(goal_key) != evidence_set_id:
            return ToolResult(
                ok=False,
                error="evidence_set_id does not belong to this goal",
                internal_diagnostic=True,
            )
    evidence = transit_evidence.load_evidence_set(
        evidence_set_id, session_id=session_id
    )
    if evidence is None:
        return ToolResult(
            ok=False,
            error="transit evidence set is unknown, expired, or not owned by this session",
            internal_diagnostic=True,
        )
    operation = str(evidence.get("requested_operation") or "transit")
    if operation not in _PRESENTABLE_OPERATIONS:
        return ToolResult(ok=False, error="this transit evidence is not passenger-presentable")
    presented = ctx.telemetry.setdefault("presented_transit_evidence", [])
    if evidence_set_id in presented:
        return ToolResult(
            ok=True,
            data={
                "evidence_set_id": evidence_set_id,
                "goal_key": goal_key,
                "operation": operation,
                "already_presented": True,
                "passenger_text": "",
                "lead_in": "",
                "follow_up": "",
            },
            summary="Transit result already shown",
        )
    text = _passenger_text(evidence, operation)
    outcome = {"status": "presented", "goal_key": goal_key, "operation": operation}
    data = {
        "evidence_set_id": evidence_set_id,
        "goal_key": goal_key,
        "operation": operation,
        "presentation_outcome": outcome,
        "passenger_text": text,
        "lead_in": lead_in,
        "follow_up": follow_up,
    }
    if contract is not None:
        turn_evidence.record_goal(
            goal_key,
            GoalState.SATISFIED,
            attempted=True,
            presented=True,
        )
    follow_up = ""
    data["follow_up"] = follow_up
    presented.append(evidence_set_id)
    return ToolResult(
        ok=True,
        data=data,
        summary=f"Presented checked {operation.replace('_', ' ')}",
        events=framed_events(
            _build_events(ctx, evidence, operation, text),
            lead_in,
            follow_up,
        ),
    )


_INCOMPLETE_COVERAGE = frozenset(
    {"partial", "unavailable", "stale", "unknown", "unscanned"}
)


def _route_names(value: object) -> tuple[str, ...]:
    items = value if isinstance(value, list) else []
    return tuple(str(route).strip().upper() for route in items if str(route).strip())


def _dict_rows(value: object) -> tuple[dict[str, Any], ...]:
    items = value if isinstance(value, list) else []
    return tuple(item for item in items if isinstance(item, dict))


@dataclass(frozen=True)
class StatusView:
    checked_routes: tuple[str, ...]
    routes: str
    scope: dict[str, Any]
    target: str
    unknowns: tuple[str, ...]
    findings: tuple[dict[str, Any], ...]
    signals: tuple[dict[str, Any], ...]
    coverage_note: str

    @classmethod
    def from_evidence(cls, evidence: dict[str, Any]) -> StatusView:
        checked_routes = _route_names(evidence.get("checked_routes"))
        routes = ", ".join(checked_routes) or "the requested route"
        raw_scope = evidence.get("direction_scope")
        scope = raw_scope if isinstance(raw_scope, dict) else {}
        target = (
            f"{scope['resolved']} {routes}"
            if scope.get("authoritative") and scope.get("resolved")
            else routes
        )
        findings = _dict_rows(evidence.get("confirmed_matching_alerts")) + tuple(
            item
            for item in _dict_rows(evidence.get("incidents"))
            if item.get("confirmed") is True
        )
        if scope.get("requested"):
            findings = tuple(
                item
                for item in findings
                if _finding_matches_direction(item, scope.get("requested"))
                or not item.get("direction")
            )
        signals = _dict_rows(evidence.get("unconfirmed_signals"))
        if scope.get("requested") and any(
            not str(item.get("direction") or "").strip()
            and item.get("direction_scope") != "both_directions"
            for item in (*findings, *signals)
        ):
            target = routes
        return cls(
            checked_routes=checked_routes,
            routes=routes,
            scope=scope,
            target=target,
            unknowns=tuple(str(item) for item in evidence.get("unknowns") or []),
            findings=findings,
            signals=signals,
            coverage_note=_coverage_note(evidence),
        )


def service_status_text(evidence: dict[str, Any]) -> str:
    view = StatusView.from_evidence(evidence)
    direction_error = _direction_status_error(evidence, view)
    if direction_error is not None:
        return _with_decision_continuity(direction_error, evidence)
    text = (
        _systemwide_status_text(evidence, view)
        if not view.checked_routes
        else _route_specific_status_text(evidence, view)
    )
    return _with_decision_continuity(text, evidence)


def _with_decision_continuity(text: str, evidence: dict[str, Any]) -> str:
    freshness = evidence.get("freshness")
    marker = freshness.get("continuity") if isinstance(freshness, dict) else None
    if isinstance(marker, dict) and marker.get("changed") is True:
        return _sentence(
            f"{text} Official alert evidence has changed since the route was prepared"
        )
    return text


def _direction_status_error(
    evidence: dict[str, Any], view: StatusView
) -> str | None:
    if not view.scope.get("requested") or _direction_is_verified(evidence):
        return None
    missing_scope = (
        "a matching alert, incident, or vehicle signal did not specify the requested direction"
    )
    if not view.findings and not view.signals and missing_scope in view.unknowns:
        return _sentence(
            f"I can't confirm the current status of {view.routes} for the requested "
            "direction because the available alert or incident did not specify one."
        )
    return None


def _systemwide_status_text(evidence: dict[str, Any], view: StatusView) -> str:
    if view.findings:
        return _systemwide_alert_text(
            list(view.findings), view.coverage_note, list(view.signals)
        )
    if view.signals:
        return _with_coverage(
            "I found possible unconfirmed delay signals, but they are not enough "
            "to identify an affected train systemwide.",
            view.coverage_note,
        )
    if not view.unknowns:
        return "No affected service was identified in the current official alerts."
    if _official_alerts_are_current(evidence):
        return _with_coverage(
            "No affected service was identified in the current official MTA alert feed.",
            view.coverage_note,
        )
    return _sentence(
        "I can't confirm current systemwide transit status because "
        f"{view.coverage_note or 'the available transit information is incomplete'}"
    )


def _route_specific_status_text(evidence: dict[str, Any], view: StatusView) -> str:
    if view.findings:
        text = _route_status_text(
            target=view.target,
            findings=list(view.findings),
            signals=list(view.signals),
        )
        text = _with_direction_caveat(text, view)
        return _with_coverage(text, view.coverage_note)
    if view.signals:
        vehicle = "bus" if view.signals[0].get("mode") == "bus" else "train"
        text = (
            f"I found a possible stalled {vehicle} relevant to {view.target}, "
            "but it isn't confirmed."
        )
        text = _with_direction_caveat(text, view)
        return _with_coverage(text, view.coverage_note)
    if not view.unknowns:
        return (
            f"No matching official alert or confirmed incident was found for {view.target}, "
            "and no relevant stalled train or bus signal was detected in the "
            "available information."
        )
    if _official_alerts_are_current(evidence) and _direction_is_verified(evidence):
        return _with_coverage(
            f"I didn't find a matching official alert for {view.target} in the "
            "current MTA alerts, and no relevant stalled train or bus signal "
            "was detected in the available live vehicle information.",
            view.coverage_note,
        )
    return _sentence(
        f"I can't confirm the current status of {view.target} because "
        f"{view.coverage_note or 'the available transit information is incomplete'}"
    )


def _with_direction_caveat(text: str, view: StatusView) -> str:
    if not _has_unscoped_scope(view):
        return text
    return _sentence(
        f"{text} This is route-level evidence and does not confirm the "
        "requested direction"
    )


def _systemwide_alert_text(
    alerts: list[dict[str, Any]],
    coverage_note: str,
    signals: list[dict[str, Any]] | None = None,
) -> str:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in alerts[:8]:
        routes = ", ".join(
            str(route).strip().upper()
            for route in item.get("route_ids") or []
            if str(route).strip()
        ) or "Affected service"
        header = _alert_detail(item)
        if _is_planned_service_change(item):
            header = f"Official planned service change: {header}"
        key = (routes, header)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {routes}: {header}")
    for signal in (signals or [])[:4]:
        mode = "bus" if signal.get("mode") == "bus" else "train"
        route = str(signal.get("route_id") or "").strip().upper()
        suffix = f" on {route}" if route else ""
        lines.append(f"- Possible stalled {mode}{suffix} (not confirmed)")
    text = "Affected services right now:\n" + "\n".join(lines)
    return f"{text} {coverage_note}" if coverage_note else text


def _confirmed_finding_text(item: dict[str, Any]) -> str:
    planned_change = _is_planned_service_change(item)
    detail = _alert_detail(item)
    source_label = "Confirmed incident" if item.get("incident_id") else "Official alert"
    if planned_change and not item.get("incident_id"):
        source_label = "Official planned service change"
    return f"{source_label}: {detail}"


def _is_planned_service_change(item: dict[str, Any]) -> bool:
    return (
        item.get("planned_status") == "planned"
        and item.get("service_operating") is True
        and item.get("material_disruption") is False
    )


def _alert_detail(item: dict[str, Any]) -> str:
    if _is_planned_service_change(item):
        return str(
            item.get("description")
            or item.get("header")
            or "A planned service change was reported"
        ).strip()
    return str(
        item.get("header")
        or item.get("description")
        or "A current transit disruption was reported"
    ).strip()


def _route_status_text(
    *,
    target: str,
    findings: list[dict[str, Any]],
    signals: list[dict[str, Any]],
) -> str:
    details: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        detail = _confirmed_finding_text(finding)
        key = detail.casefold()
        if key in seen:
            continue
        seen.add(key)
        details.append(detail)
        if len(details) >= 3:
            break
    if signals:
        mode = "bus" if signals[0].get("mode") == "bus" else "train"
        details.append(f"Possible stalled {mode} (not confirmed)")
    return _sentence(f"{target}: {' '.join(details)}")


def _sentence(text: str) -> str:
    value = str(text or "").strip()
    while len(value) > 1 and value[-1] in ".!?" and value[-2] in ".!?":
        value = value[:-1]
    return value if value.endswith((".", "!", "?", "…")) else f"{value}."


def _with_coverage(text: str, coverage_note: str) -> str:
    if not coverage_note:
        return text
    return f"{text.rstrip()} {coverage_note.lstrip()}".strip()


def _official_alerts_are_current(evidence: dict[str, Any]) -> bool:
    coverage = evidence.get("source_coverage")
    return isinstance(coverage, dict) and str(
        coverage.get("alerts") or "unknown"
    ).casefold() == "current"


def _direction_is_verified(evidence: dict[str, Any]) -> bool:
    scope = evidence.get("direction_scope")
    if not isinstance(scope, dict) or not scope.get("requested"):
        return True
    if not (scope.get("authoritative") and scope.get("resolved")):
        return False
    return "a matching alert, incident, or vehicle signal did not specify the requested direction" not in {
        str(value) for value in (evidence.get("unknowns") or [])
    }


def _finding_matches_direction(item: dict[str, Any], requested: object) -> bool:
    direction = str(item.get("direction") or "").strip()
    requested_text = str(requested or "").strip()
    return bool(direction and requested_text and direction.casefold() == requested_text.casefold())


def _has_unscoped_scope(view: StatusView) -> bool:
    if not view.scope.get("requested"):
        return False
    return any(
        not str(item.get("direction") or "").strip()
        and item.get("direction_scope") != "both_directions"
        for item in (*view.findings, *view.signals)
    )


def _coverage_note(evidence: dict[str, Any]) -> str:
    coverage = evidence.get("source_coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    notes: list[str] = []
    alerts = str(coverage.get("alerts") or "").casefold()
    gtfs = str(coverage.get("gtfs_rt") or "").casefold()
    incidents = str(coverage.get("incidents") or "").casefold()
    bustime = str(coverage.get("bustime") or "").casefold()
    if alerts in _INCOMPLETE_COVERAGE:
        notes.append(_coverage_gap("official MTA alerts", alerts))
    if gtfs in _INCOMPLETE_COVERAGE:
        notes.append(
            f"{_coverage_gap('live train positions', gtfs)} "
            "I can't rule out an additional delay."
        )
    if incidents in _INCOMPLETE_COVERAGE:
        notes.append(
            f"{_coverage_gap('recent incident reports', incidents)} "
            "Another disruption may not be reflected here."
        )
    if bustime in _INCOMPLETE_COVERAGE:
        notes.append(
            f"{_coverage_gap('live bus positions', bustime)} "
            "I can't rule out another traffic delay."
        )
    return " ".join(notes[:2])


def _coverage_gap(source: str, status: str) -> str:
    if status == "stale":
        return f"The {source} may be out of date."
    if status == "partial":
        return f"I could only check part of the {source}."
    if status == "unavailable":
        return f"The {source} weren't available."
    return f"I couldn't fully check the {source}."
