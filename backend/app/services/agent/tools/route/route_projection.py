"""Canonical route projection for ``present_route``.

Builds one recommended route card and truthful copy from a validated
candidate. Does not mutate session or candidate state.
"""

from __future__ import annotations

import copy
import math
import re
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from app.services.agent import events as agent_events
from app.services.agent.model.output_projection import (
    opaque_place_id,
    project_model_value,
    project_place_point,
)
from app.services.agent.tools._types import ToolContext, ToolResult
from app.services.agent.tools.route.present_route_state import is_destination_comparison
from app.services.agent.tools.route.route_input import point_label, summary_eta_minutes
from app.services.trips import candidates, scoring, text
from app.services.trips.route_incidents.scan import (
    INCOMPLETE_INCIDENT_DISCLOSURE,
    contains_unsafe_incident_clear,
    incident_scan_is_complete,
)
from app.services.trips.selection_record import build_route_selection_decision
from app.services.mta.static_gtfs.stop_patterns import normalize_station_name

_INCOMPLETE_PATTERNS = (
    r"\bcurrent\s+incident\s+coverage\s+is\s+incomplete(?:,\s*so\s*allow\s+extra\s+time)?\b",
    r"\bincident\s+coverage\s+is\s+incomplete\b",
    r"\bincident\s+(?:information|evidence)\s+(?:is|was)\s+unavailable\b",
    r"\b(?:the\s+)?incident\s+scan\s+(?:has\s+)?timed\s+out\b",
    r"\b(?:the\s+)?incident\s+scan\s+(?:is|was)\s+unavailable\b",
    r"\b(?:could\s+not|couldn['’]t)\s+complete\s+(?:the\s+)?incident\s+scan\b",
)
_PASSENGER_DECISION_FIELDS = (
    "selection_source",
    "selection_reason",
    "reason_code",
)
_TRANSIT_MODES = frozenset({"SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL", "TRAM"})


def passenger_explanation(recommendation: str, incident_scan_metadata: dict) -> str:
    """Keep incomplete incident evidence truthful without duplicate rider copy."""
    explanation = text._safe_text(
        text._sanitize_recommendation(
            candidates._strip_model_control_blocks(recommendation)
        ),
        600,
    )
    incomplete = not incident_scan_is_complete(incident_scan_metadata)
    if incomplete and contains_unsafe_incident_clear(explanation):
        return (
            "I found the best available route from the current transit options. "
            f"{INCOMPLETE_INCIDENT_DISCLOSURE}"
        )
    if not explanation:
        explanation = (
            "I found the best available route from the current transit options."
        )
    if incomplete and not _mentions_incomplete(explanation):
        explanation = f"{explanation} {INCOMPLETE_INCIDENT_DISCLOSURE}"
    return explanation


def project_canonical_route(presentation: Any, ctx: ToolContext) -> ToolResult:
    """Emit one recommended route card from a validated presentation record."""
    evidence = presentation.candidate_evidence
    return _emit_recommended_card(presentation, ctx, evidence, presentation.scored)


def _emit_recommended_card(
    presentation: Any,
    ctx: ToolContext,
    evidence: dict[str, Any],
    scored: list[dict],
) -> ToolResult:
    core = _canonical_card_core(presentation, evidence, scored)
    digest, event, session_card = _card_surfaces(presentation, ctx, evidence, core)
    metadata = dict(
        evidence.get("incident_scan_metadata")
        or presentation.record.get("incident_scan_metadata")
        or {}
    )
    explanation = passenger_explanation(presentation.lead_in, metadata)
    incomplete = not incident_scan_is_complete(metadata)
    evidence_keys = (
        "status",
        "scanned_at",
        "cache_hit",
        "sources",
        "lookup_status",
        "coverage_status",
        "lookup_kind",
        "requested_coverage_ids",
        "warning_count",
        "lookup_latency_ms",
    )
    return ToolResult(
        ok=True,
        data={
            "candidates": [digest],
            "event_evidence": {
                "status": core["event_status"],
                "impact_count": len(core["event_impacts"]),
                "provider_failure_count": len(evidence.get("event_failures") or []),
                "search": dict(evidence.get("crowd_search_metadata") or {}),
            },
            "incident_evidence": {
                key: metadata[key] for key in evidence_keys if key in metadata
            },
            "evidence": {
                name: {
                    **envelope.to_model_dict(empty=[]),
                    "payload": {"count": len(envelope.current_payload() or [])},
                }
                for name, envelope in sorted(presentation.evidence_envelopes.items())
            },
            "selected_route_index": core["index"],
            "selection_decision": core["decision"],
            "passenger_explanation": explanation,
            "_passenger_explanation_core": (
                _strip_incomplete(explanation) if incomplete else explanation
            ),
            "_incident_coverage_incomplete": incomplete,
        },
        summary=(
            f"found {len(presentation.parsed_routes)} route(s) to {core['destination_label']}; "
            f"recommended {'/'.join(core['lines']) or 'a walking route'}"
        ),
        events=[event],
        session_route_cards=[session_card],
        timings=presentation.timings,
    )


def _canonical_card_core(
    presentation: Any,
    evidence: dict[str, Any],
    scored: list[dict],
) -> dict[str, Any]:
    index = presentation.chosen_index
    route = presentation.parsed_routes[index]
    display = candidates._build_route_candidates(
        presentation.parsed_routes,
        index,
        {index: {"recommendation_reason": "", "rejection_reason": ""}},
        scored,
    )
    scores = scoring._score_by_index(scored)
    card_id = f"rc_{secrets.token_hex(4)}"
    event_impacts = list(evidence.get("event_impacts") or [])
    event_status = str(evidence.get("event_evidence_status") or "unscanned")
    decision, passenger_decision, structured = _selection_projection(
        presentation,
        scores[index],
        event_status,
        event_impacts,
    )
    selected_destination_id = _selected_digest_destination_place_id(presentation.entry)
    selected_destination_name = _selected_digest_destination_name(presentation.entry)
    origin_point, destination_point, destination_label = _project_card_endpoints(
        presentation,
        selected_destination_id,
        selected_destination_name,
    )
    itinerary = _project_card_itinerary(
        presentation,
        selected_destination_id,
        passenger_decision,
        structured,
    )
    return {
        "index": index,
        "route": route,
        "card_id": card_id,
        "event_impacts": event_impacts,
        "event_status": event_status,
        "decision": decision,
        "passenger_decision": passenger_decision,
        "structured": structured,
        "origin_point": origin_point,
        "destination_point": destination_point,
        "destination_label": destination_label,
        "itinerary": itinerary,
        "eta": summary_eta_minutes(route, itinerary["total_duration_seconds"]),
        "transfers": int(itinerary["transfer_count"]),
        "lines": display[index]["score_breakdown"]["transit_lines"],
        "crowd_penalty": scores[index].get("event_crowd_penalty", 0),
    }


def _selection_projection(
    presentation: Any,
    selected_score: dict[str, Any],
    event_status: str,
    event_impacts: list[dict],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    decision = build_route_selection_decision(
        selected_index=presentation.chosen_index,
        selected_candidate_id=presentation.candidate_id,
        selected_score=selected_score,
        selection_reason=presentation.selection_reason,
        selection_source=(
            "deterministic_fallback"
            if presentation.selection_source == "deterministic_fallback"
            else "model"
        ),
        excluded_modes=set(presentation.record.get("excluded") or []),
        arrival_by=bool(presentation.record.get("arrival_by")),
        avoid_crowds=bool(presentation.record.get("collect_crowd_evidence")),
        event_evidence_status=event_status,
        event_impacts=event_impacts,
        reason_code=presentation.reason_code,
    )
    passenger_decision = {
        field: decision[field] for field in _PASSENGER_DECISION_FIELDS
    }
    structured = (
        [dict(presentation.structured_reason)]
        if isinstance(presentation.structured_reason, dict)
        else []
    )
    return decision, passenger_decision, structured


def _project_card_endpoints(
    presentation: Any,
    selected_destination_id: str | None,
    selected_destination_name: str | None,
) -> tuple[dict, dict, str]:
    origin_raw = str(presentation.record.get("origin_raw") or "")
    destination_raw = str(presentation.record.get("destination_raw") or "")
    origin_point = project_place_point(presentation.origin_place.to_event_point())
    destination_point = project_place_point(
        presentation.destination_place.to_event_point(),
        fallback_place_id=selected_destination_id,
    )
    if selected_destination_id is not None:
        destination_point["place_id"] = selected_destination_id
    origin_point["label"] = (
        point_label(origin_raw)
        if presentation.origin_place.source != "user"
        else "Your location"
    )
    if is_destination_comparison(presentation.record):
        destination_label = (
            selected_destination_name
            or presentation.destination_place.name
            or point_label(destination_raw)
        )
    else:
        destination_label = (
            point_label(destination_raw) or presentation.destination_place.name
        )
    destination_point["label"] = destination_label
    return origin_point, destination_point, destination_label


def _project_card_itinerary(
    presentation: Any,
    selected_destination_id: str | None,
    passenger_decision: dict[str, Any],
    structured: list[dict[str, Any]],
) -> dict[str, Any]:
    itinerary = project_model_value(presentation.canonical_itinerary)
    if not isinstance(itinerary, dict):
        itinerary = {}
    if selected_destination_id is not None:
        destination = itinerary.get("destination")
        if isinstance(destination, dict):
            itinerary["destination"] = {
                **destination,
                "place_id": selected_destination_id,
            }
    itinerary["selection_decision"] = passenger_decision
    itinerary["structured_recommendation_reasons"] = structured
    return itinerary


def _card_surfaces(
    presentation: Any,
    ctx: ToolContext,
    evidence: dict[str, Any],
    core: dict[str, Any],
) -> tuple[dict, Any, dict]:
    route = core["route"]
    alerts = list(evidence.get("alerts") or [])
    digest = _card_digest(core, alerts, presentation)
    event = _route_card_event(core, route, alerts, ctx, presentation)
    boarding = _boarding_context(core, route, ctx)
    session_card = _session_card(core, boarding)
    return digest, event, session_card


def _card_digest(
    core: dict[str, Any], alerts: list[dict], presentation: Any
) -> dict[str, Any]:
    route = core["route"]
    first_step = route[0] if route else {}
    last_step = route[-1] if route else {}
    return {
        "card_id": core["card_id"],
        "lines": core["lines"],
        "eta_minutes": core["eta"],
        "transfers": core["transfers"],
        "departs_iso": first_step.get("departure_time_iso"),
        "arrives_iso": last_step.get("arrival_time_iso"),
        "walk_minutes": round(int(core["itinerary"]["total_walk_seconds"]) / 60),
        "alert_headlines": [
            text._safe_text(alert.get("header") or "", 80) for alert in alerts
        ][:3],
        "reason": presentation.lead_in,
        "structured_recommendation_reasons": core["structured"],
        "event_evidence_status": core["event_status"],
        "event_crowd_penalty": core["crowd_penalty"],
        "event_impacts": _event_digest(core["event_impacts"], core["index"]),
        "first_leg_arrival": presentation.first_leg_context,
    }


def _route_card_event(
    core: dict[str, Any],
    route: list[dict],
    alerts: list[dict],
    ctx: ToolContext,
    presentation: Any,
) -> agent_events.RouteCardEvent:
    return agent_events.RouteCardEvent(
        card_id=core["card_id"],
        turn_id=ctx.turn_id,
        role="recommended",
        origin=core["origin_point"],
        destination=core["destination_point"],
        depart_iso=presentation.record.get("departure_time"),
        summary={
            "eta_minutes": core["eta"],
            "transfers": core["transfers"],
            "lines": core["lines"],
            "reason": presentation.lead_in or None,
            "event_evidence_status": core["event_status"],
            "first_leg_arrival": presentation.first_leg_context,
        },
        route=route,
        alerts=alerts,
        itinerary=core["itinerary"],
        selection_decision=core["passenger_decision"],
    )


def _boarding_context(
    core: dict[str, Any], route: list[dict], ctx: ToolContext
) -> dict | None:
    first_transit, walk_minutes, canonical_first_leg = _boarding_inputs(
        route,
        core["itinerary"],
    )
    if first_transit is None:
        return None
    boarding = first_boarding_context(
        ctx.gtfs,
        first_transit,
        walk_minutes,
    )
    return _merge_canonical_boarding(boarding, canonical_first_leg)


def _boarding_inputs(
    route: list[dict], itinerary: dict[str, Any]
) -> tuple[dict | None, int, dict | None]:
    first_transit = next(
        (step for step in route if step.get("type") in {"SUBWAY", "BUS"}),
        None,
    )
    walk_seconds = 0
    for leg in itinerary.get("legs") or []:
        if str(leg.get("mode") or "").upper() != "WALK":
            break
        walk_seconds += int(leg.get("walk_seconds") or 0)
    canonical_first_leg = next(
        (
            leg
            for leg in itinerary.get("legs") or []
            if str(leg.get("mode") or "").upper() in _TRANSIT_MODES
        ),
        None,
    )
    return first_transit, round(walk_seconds / 60), canonical_first_leg


def _merge_canonical_boarding(
    boarding: dict | None,
    canonical_first_leg: dict | None,
) -> dict | None:
    if not isinstance(boarding, dict) or not isinstance(canonical_first_leg, dict):
        return boarding
    for key in (
        "canonical_direction",
        "semantic_direction",
        "direction",
        "direction_label",
        "headsign",
        "destination_stop_name",
        "stop_order",
    ):
        if canonical_first_leg.get(key) not in (None, "", []):
            boarding[key] = canonical_first_leg[key]
    return boarding


def _session_card(core: dict[str, Any], boarding: dict | None) -> dict:
    return {
        "card_id": core["card_id"],
        "role": "recommended",
        "lines": core["lines"],
        "eta_minutes": core["eta"],
        "destination": core["destination_point"],
        "first_boarding": boarding,
        "canonical_itinerary": core["itinerary"],
        "selection_decision": core["decision"],
    }


def _event_digest(event_impacts: list[dict], index: int) -> list[dict]:
    keys = (
        ("event_name", "title"),
        ("venue_name", "venue"),
        ("exposure_window", "exposure_window"),
        ("distance_meters", "distance_meters"),
        ("risk_score", "risk_score"),
        ("confidence", "confidence"),
        ("source_class", "source_class"),
        ("verification_tier", "verification_tier"),
        ("scoring_authorized", "scoring_authorized"),
    )
    return [
        {out: impact.get(src) for out, src in keys}
        for impact in event_impacts
        if impact.get("route_index") == index
    ][:3]


def _mentions_incomplete(value: str) -> bool:
    normalized = value.casefold()
    markers = (
        "incomplete",
        "unavailable",
        "timed out",
        "timeout",
        "could not complete",
        "couldn't complete",
    )
    return "incident" in normalized and any(marker in normalized for marker in markers)


def _strip_incomplete(value: str) -> str:
    normalized = value
    for pattern in _INCOMPLETE_PATTERNS:
        normalized = re.sub(
            rf"{pattern}(?:\s*[.!?])?", "", normalized, flags=re.IGNORECASE
        )
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    return re.sub(r"\s{2,}", " ", normalized).strip(" \t\r\n,;:")


def _selected_digest_destination_place_id(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    digest = entry.get("digest")
    if not isinstance(digest, dict):
        return None
    return opaque_place_id(digest.get("destination_place_id"))


def _selected_digest_destination_name(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    digest = entry.get("digest")
    if not isinstance(digest, dict):
        return None
    name = str(digest.get("destination_name") or "").strip()
    return name or None


def first_boarding_context(gtfs, step: dict, walking_minutes: int) -> dict:
    """Resolve canonical stop/direction ids for one transit boarding."""
    route_id = scoring._step_route_id(step).strip().upper()
    headsign = step.get("headsign") or step.get("direction")
    context = {
        "route_id": route_id,
        "mode": str(step.get("type") or "").lower(),
        "stop_name": step.get("departure_stop"),
        "coordinates": step.get("departure_coords"),
        "direction_label": step.get("direction"),
        "headsign": headsign,
        "walking_minutes": walking_minutes,
    }
    pattern_index = getattr(gtfs, "_pattern_index", None)
    resolve = getattr(pattern_index, "resolve_route_segment", None)
    if not callable(resolve):
        return context
    resolved = resolve(
        route_id,
        step.get("departure_stop"),
        step.get("arrival_stop"),
        step.get("departure_coords"),
        step.get("arrival_coords"),
    )
    if resolved:
        context.update(
            {
                "stop_id": resolved.get("origin_stop_id"),
                "direction_id": resolved.get("direction_id"),
                "destination_stop_id": resolved.get("destination_stop_id"),
                "departure_stop_id": resolved.get("origin_stop_id"),
                "arrival_stop_id": resolved.get("destination_stop_id"),
            }
        )
        for key in (
            "canonical_direction",
            "semantic_direction",
            "direction",
            "direction_label",
            "headsign",
            "stop_order",
        ):
            if resolved.get(key) not in (None, "", []):
                context[key] = resolved[key]
        pattern_context = _validated_pattern_context(
            pattern_index,
            route_id,
            resolved.get("origin_stop_id"),
            resolved.get("destination_stop_id"),
            headsign,
        )
        if pattern_context:
            context.update(pattern_context)
    return context


def _validated_pattern_context(
    pattern_index: object,
    route_id: str,
    origin_stop_id: object,
    destination_stop_id: object,
    headsign: object,
) -> dict[str, object] | None:
    """Validate a provider headsign against one attached route pattern.

    A numeric GTFS direction id is intentionally not interpreted here.  The
    selected pattern must contain the ordered endpoints and its terminal must
    match the provider headsign.  The result is validation metadata; it does
    not assign meaning to a numeric direction id or invent uptown/downtown.
    """

    route_patterns = getattr(pattern_index, "route_patterns", {})
    stops = getattr(pattern_index, "stops", {})
    if not isinstance(route_patterns, Mapping) or not isinstance(stops, Mapping):
        return None
    origin = str(origin_stop_id or "").strip()
    destination = str(destination_stop_id or "").strip()
    headsign_key = normalize_station_name(str(headsign or ""))
    if not origin or not destination or not headsign_key:
        return None
    matches: list[Mapping[str, object]] = []
    for pattern in route_patterns.get(route_id, []):
        if not isinstance(pattern, Mapping):
            continue
        stop_ids = pattern.get("stop_ids")
        if not isinstance(stop_ids, list) or not stop_ids:
            continue
        try:
            origin_index = stop_ids.index(origin)
            destination_index = stop_ids.index(destination)
        except ValueError:
            continue
        if origin_index >= destination_index:
            continue
        terminal = stops.get(str(stop_ids[-1]))
        terminal_key = normalize_station_name(
            terminal.get("name") if isinstance(terminal, Mapping) else ""
        )
        if terminal_key == headsign_key:
            matches.append(pattern)
    if len(matches) != 1:
        return None
    return {
        "stop_order": {
            "origin_stop_id": origin,
            "destination_stop_id": destination,
            "headsign": headsign,
        },
    }


def reconcile_first_boarding_timing(
    itinerary: dict,
    first_leg_arrival: dict | None,
    *,
    now_iso: str | None,
) -> dict:
    """Replace a provider's first-train wait with grounded arrival evidence.

    A catchable-arrival minute is measured from *now* to boarding and already
    includes the access walk.  Adding the access walk to it again would double
    count.  The canonical total is therefore rebuilt as access + live wait +
    ride/transfer/egress components, with the first wait chosen so boarding
    occurs at the observed catchable minute.
    """

    context = first_leg_arrival if isinstance(first_leg_arrival, dict) else {}
    catchable = context.get("catchable_arrival_minutes")
    if (
        context.get("source_status") not in {"live", "scheduled"}
        or not isinstance(catchable, (int, float))
        or isinstance(catchable, bool)
        or not math.isfinite(float(catchable))
        or float(catchable) < 0
    ):
        return itinerary

    reconciled = copy.deepcopy(itinerary)
    legs = [leg for leg in (reconciled.get("legs") or []) if isinstance(leg, dict)]
    first_transit_index = next(
        (
            index
            for index, leg in enumerate(legs)
            if str(leg.get("mode") or "").upper() in _TRANSIT_MODES
        ),
        None,
    )
    if first_transit_index is None:
        return itinerary

    boarding_offset_seconds = max(0, int(round(float(catchable) * 60)))
    access_seconds = sum(
        _leg_component_seconds(leg) for leg in legs[:first_transit_index]
    )
    if boarding_offset_seconds < access_seconds:
        return itinerary
    first_transit = legs[first_transit_index]
    first_transit["wait_seconds"] = max(0, boarding_offset_seconds - access_seconds)

    total_walk = sum(int(leg.get("street_walking_seconds") or 0) for leg in legs)
    total_wait = sum(int(leg.get("wait_seconds") or 0) for leg in legs)
    total_in_vehicle = sum(int(leg.get("ride_seconds") or 0) for leg in legs)
    total_transfer = sum(int(leg.get("transfer_seconds") or 0) for leg in legs)
    total_in_station = sum(
        int(leg.get("in_station_transfer_seconds") or 0) for leg in legs
    )
    total_dwell = int(reconciled.get("total_dwell_seconds") or 0)
    total_duration = (
        total_walk + total_wait + total_in_vehicle + total_transfer + total_dwell
    )

    reconciled.update(
        {
            "legs": legs,
            "total_duration_seconds": total_duration,
            "total_walk_seconds": total_walk,
            "total_street_walking_seconds": total_walk,
            "total_in_station_transfer_seconds": total_in_station,
            "total_wait_seconds": total_wait,
            "total_in_vehicle_seconds": total_in_vehicle,
            "total_transfer_seconds": total_transfer,
        }
    )
    start = _parse_clock(now_iso)
    if start is not None:
        _retime_legs(
            legs,
            start=start,
            first_transit_index=first_transit_index,
            boarding_offset_seconds=boarding_offset_seconds,
        )
        reconciled["departure_at"] = start.isoformat()
        reconciled["arrival_at"] = (start + timedelta(seconds=total_duration)).isoformat()
        reconciled["generated_at"] = reconciled.get("generated_at") or start.isoformat()
        reconciled["data_freshness"] = context.get("observed_at") or start.isoformat()
    return reconciled


def _leg_component_seconds(leg: dict) -> int:
    return sum(
        max(0, int(leg.get(field) or 0))
        for field in (
            "street_walking_seconds",
            "wait_seconds",
            "ride_seconds",
            "transfer_seconds",
        )
    )


def _parse_clock(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _retime_legs(
    legs: list[dict],
    *,
    start: datetime,
    first_transit_index: int,
    boarding_offset_seconds: int,
) -> None:
    cursor = start
    for index, leg in enumerate(legs):
        mode = str(leg.get("mode") or "").upper()
        if index == first_transit_index:
            departure = start + timedelta(seconds=boarding_offset_seconds)
            arrival = departure + timedelta(
                seconds=max(0, int(leg.get("ride_seconds") or 0))
            )
        elif mode in _TRANSIT_MODES:
            departure = cursor + timedelta(
                seconds=max(0, int(leg.get("wait_seconds") or 0))
                + max(0, int(leg.get("transfer_seconds") or 0))
            )
            arrival = departure + timedelta(
                seconds=max(0, int(leg.get("ride_seconds") or 0))
            )
        else:
            departure = cursor
            arrival = departure + timedelta(seconds=_leg_component_seconds(leg))
        leg["departure_at"] = departure.isoformat()
        leg["arrival_at"] = arrival.isoformat()
        cursor = arrival
