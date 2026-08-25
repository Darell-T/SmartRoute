"""Persist and project one finalized route candidate set."""

from __future__ import annotations

import dataclasses
import math
import time
from typing import Any

from app.services.agent import candidate_store
from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext, ToolOutcome, ToolResult
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.prepare_route_branches import aggregate_destination_ids
from app.services.trips.preparation.constraints import (
    ROUTE_STATUSES,
    candidate_digest,
    route_status,
)
from app.services.trips.preparation.prepare import AggregatePreparation
from app.services.trips.preparation.evidence import (
    coverage_for_prepared,
    serialize_evidence_envelopes,
)
from app.services.agent.tools.route.preparation_adapter import PreparedLeg
from app.services.agent.turn.finalization import record_phase_ms
from app.services.trips.route_incidents.scan import incident_scan_is_complete


def candidate_evidence(
    aggregate: AggregatePreparation,
    index: int,
) -> dict[str, Any]:
    if index < len(aggregate.candidate_evidence):
        return aggregate.candidate_evidence[index]
    return {
        "alerts": aggregate.relevant_alerts,
        "incidents": aggregate.incidents,
        "event_impacts": [
            impact
            for impact in aggregate.event_impacts
            if impact.get("route_index") == index
        ],
    }


def nonfatal_prepare_result(
    result: ToolResult,
    tool_input: dict,
    ctx: ToolContext,
    started: float,
) -> ToolResult:
    message = str(result.error or "route coverage is insufficient")
    message_lower = message.casefold()
    recoverable_tokens = (
        "no transit route",
        "no route",
        "no transit modes",
        "coverage",
    )
    if not any(token in message_lower for token in recoverable_tokens):
        return result
    route_status = (
        "no_hard_constraint_match"
        if "no transit modes" in message_lower
        else "insufficient_coverage"
    )
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    set_id = candidate_store.store_candidate_set(
        session_id=session_id,
        payload={
            "tool_input": tool_input,
            "candidates": [],
            "route_status": route_status,
            "scenario_mode": tool_input.get("scenario", "active"),
            "evidence_coverage": {"routes": "unavailable"},
        },
    )
    if isinstance(ctx.session, dict):
        if tool_input.get("scenario") == "what_if":
            trip_state_module.bind_temporary_candidate_set(ctx.session, set_id)
        else:
            # A non-presentable active preparation must not move the accepted
            # canonical selection. Keep the accepted route facts, active set,
            # and selected candidate bound, and store the new set only as an
            # audit record. Only an obsolete what-if scenario is discarded.
            trip_state_module.discard_scenario(ctx.session)
    return ToolResult(
        ok=True,
        data={
            "candidate_set_id": set_id,
            "route_status": route_status,
            "presentation_allowed": False,
            "candidates": [],
            "evidence_coverage": {"routes": "unavailable"},
        },
        summary=message,
        timings={"plan_trip_ms": (time.monotonic() - started) * 1000},
    )


def persist_route_candidates(
    aggregate: AggregatePreparation,
    prepared: AggregatePreparation | PreparedLeg,
    merged: dict[str, Any],
    ctx: ToolContext,
    *,
    session_id: str,
    started: float,
    timings: dict[str, float],
    destination_options: list[tuple[Any, str | None]],
    accepted_destination_label: str,
    used_discovery_set_id: str | None,
    destination_discovery_set_id: str | None,
    waypoint_discovery_set_id: str | None,
    resolved_place_id: str | None,
    waypoints: list[str],
    snapshot_id: str,
    snapshot_observed_at: str,
) -> ToolResult:
    """Store the immutable route snapshot and update active trip ownership."""

    timings.update(aggregate.timings)
    visible_destination_ids = aggregate_destination_ids(aggregate)
    if visible_destination_ids:
        merged["destination_place_ids"] = visible_destination_ids
    candidate_ids = [
        candidate_store.new_candidate_id() for _ in aggregate.parsed_routes
    ]
    hard_constraints = list(aggregate.candidate_constraints)
    digests = _build_digests(
        aggregate,
        merged,
        candidate_ids,
        hard_constraints,
        snapshot_id,
        snapshot_observed_at,
    )
    coverage = aggregate.coverage or coverage_for_prepared(prepared)
    status = route_status(
        candidates=digests,
        coverage=coverage,
        incident_impacts=aggregate.incidents,
    )
    candidates, public_digests = _candidate_surface(
        aggregate,
        candidate_ids,
        digests,
    )
    presentation_allowed = bool(public_digests)
    set_id = candidate_store.store_candidate_set(
        session_id=session_id,
        payload=_candidate_set_payload(
            aggregate=aggregate,
            merged=merged,
            accepted_destination_label=accepted_destination_label,
            used_discovery_set_id=used_discovery_set_id,
            destination_discovery_set_id=destination_discovery_set_id,
            waypoint_discovery_set_id=waypoint_discovery_set_id,
            resolved_place_id=resolved_place_id,
            candidates=candidates,
            coverage=coverage,
            status=status,
            waypoints=waypoints,
            snapshot_id=snapshot_id,
            snapshot_observed_at=snapshot_observed_at,
            destination_options=destination_options,
        ),
    )
    _update_trip_state(
        ctx,
        merged,
        set_id,
        presentation_allowed,
        destination_options,
        accepted_destination_label,
        destination_discovery_set_id,
        resolved_place_id,
    )
    timings["plan_trip_ms"] = (time.monotonic() - started) * 1000
    record_phase_ms(ctx.telemetry, "plan_trip_complete_ms", timings["plan_trip_ms"])
    incomplete = not incident_scan_is_complete(aggregate.incident_scan_metadata)
    return ToolResult(
        ok=True,
        outcome=(
            ToolOutcome.READY
            if presentation_allowed
            else ToolOutcome.UNAVAILABLE
        ),
        data={
            "candidate_set_id": set_id,
            "destination_place_ids": [
                place_id for place_id in visible_destination_ids if place_id
            ],
            "route_status": status
            if status in ROUTE_STATUSES
            else "insufficient_coverage",
            "presentation_allowed": presentation_allowed,
            "candidates": public_digests,
            "branch_coverage": aggregate.branch_coverage,
            "evidence_coverage": coverage,
            "incident_coverage_incomplete": incomplete,
            "candidate_count": len(public_digests),
        },
        summary=(
            f"prepared {len(public_digests)} route option(s); compare their factors and present one"
            if public_digests
            else "route coverage is insufficient for a safe recommendation"
        ),
        timings=timings,
    )


def _candidate_surface(
    aggregate: AggregatePreparation,
    candidate_ids: list[str],
    digests: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build private candidate records and their selectable public digests."""
    candidates = [
        {
            "candidate_id": candidate_ids[index],
            "index": index,
            "destination_place": (
                aggregate.candidate_destinations[index]
                if index < len(aggregate.candidate_destinations)
                else aggregate.destination_place
            ).to_event_point(),
            "digest": digest,
        }
        for index, digest in enumerate(digests)
    ]
    selectable = [
        digest for digest in digests if digest.get("hard_constraints_satisfied") is True
    ]
    public_digests = [public_candidate_digest(digest) for digest in selectable]
    return candidates, public_digests


def bind_canonical_destination_identities(
    aggregate: AggregatePreparation,
    destination_options: list[tuple[Any, str | None]],
    resolved_place_id: str | None,
) -> None:
    """Keep discovery endpoint ids opaque after provider preparation.

    A route/provider seam can return a destination-shaped ``ResolvedPlace``
    carrying the external provider id in ``place_id``.  The destination
    reference resolved before that seam is the authority for session identity;
    bind it back onto every aggregate candidate before finalization so the
    canonical itinerary, candidate digest, and presentation all agree.
    """

    options = [
        (place, place_id)
        for place, place_id in destination_options
        if discovery_store.is_opaque_place_id(place_id)
    ]
    fallback_id = _opaque_id(resolved_place_id)
    destinations = list(aggregate.candidate_destinations)
    if not destinations:
        destinations = [aggregate.destination_place]

    normalized: list[ResolvedPlace] = []
    for index, destination in enumerate(destinations):
        opaque_id = _destination_identity(
            destination,
            options=options,
            fallback_id=fallback_id,
        )
        normalized.append(
            _with_opaque_identity(destination, opaque_id) if opaque_id else destination
        )
    aggregate.candidate_destinations = normalized

    aggregate_destination = normalized[0] if normalized else aggregate.destination_place
    aggregate.destination_place = aggregate_destination


def _destination_identity(
    destination: ResolvedPlace,
    *,
    options: list[tuple[Any, str]],
    fallback_id: str | None,
) -> str | None:
    if len(options) == 1:
        return options[0][1]
    if options:
        destination_key = _place_match_key(destination)
        matches = [
            place_id
            for option_place, place_id in options
            if _place_match_key(option_place) == destination_key
        ]
        if len(matches) == 1:
            return matches[0]
    if fallback_id:
        return fallback_id
    current_id = _opaque_id(destination.place_id)
    return current_id


def _with_opaque_identity(
    place: ResolvedPlace,
    opaque_id: str,
) -> ResolvedPlace:
    current_id = str(place.place_id or "").strip()
    provider_id = str(place.provider_place_id or "").strip() or None
    if (
        not provider_id
        and current_id
        and not discovery_store.is_opaque_place_id(current_id)
    ):
        provider_id = current_id
    if place.place_id == opaque_id and place.provider_place_id == provider_id:
        return place
    return dataclasses.replace(
        place,
        place_id=opaque_id,
        provider_place_id=provider_id,
    )


def _opaque_id(value: object) -> str | None:
    text = str(value or "").strip()
    return text if discovery_store.is_opaque_place_id(text) else None


def _place_match_key(place: Any) -> tuple[Any, ...]:
    if isinstance(place, ResolvedPlace):
        provider_id = str(place.provider_place_id or "").strip().casefold()
        current_id = str(place.place_id or "").strip()
        if (
            not provider_id
            and current_id
            and not discovery_store.is_opaque_place_id(current_id)
        ):
            provider_id = current_id.casefold()
        if provider_id:
            return ("provider", provider_id)
        return (
            "coordinates",
            _finite_coordinate(place.latitude),
            _finite_coordinate(place.longitude),
        )
    if isinstance(place, dict):
        provider_id = str(place.get("provider_place_id") or "").strip().casefold()
        current_id = str(place.get("place_id") or "").strip()
        if (
            not provider_id
            and current_id
            and not discovery_store.is_opaque_place_id(current_id)
        ):
            provider_id = current_id.casefold()
        if provider_id:
            return ("provider", provider_id)
        return (
            "coordinates",
            _finite_coordinate(place.get("latitude", place.get("lat"))),
            _finite_coordinate(place.get("longitude", place.get("lng"))),
        )
    return ("unknown", str(place or "").strip().casefold())


def _finite_coordinate(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _build_digests(
    aggregate: AggregatePreparation,
    merged: dict[str, Any],
    candidate_ids: list[str],
    hard_constraints: list[dict[str, Any]],
    snapshot_id: str,
    snapshot_observed_at: str,
) -> list[dict[str, Any]]:
    digests: list[dict[str, Any]] = []
    for index, route in enumerate(aggregate.parsed_routes):
        evidence = candidate_evidence(aggregate, index)
        score = next(
            (row for row in aggregate.scored if int(row.get("index", -1)) == index),
            {"index": index},
        )
        digest = candidate_digest(
            route=route,
            candidate_id=candidate_ids[index],
            score=score,
            alerts=evidence["alerts"],
            incidents=evidence["incidents"],
            event_impacts=evidence["event_impacts"],
            prepared_arrival_by=merged.get("arrival_by"),
            hard_constraints=hard_constraints[index],
            unconfirmed_material_claims=evidence.get("unconfirmed_material_claims"),
            evidence_coverage=evidence.get("evidence_coverage"),
            itinerary=aggregate.candidate_itineraries[index],
            evidence_snapshot={"id": snapshot_id, "observed_at": snapshot_observed_at},
            soft_preferences={
                "routing_preference": merged.get("routing_preference")
                or "FEWER_TRANSFERS",
                "routing_preference_source": (
                    merged.get("routing_preference_source") or "default"
                ),
                "preferred_modes": list(merged.get("preferred_modes") or []),
                "avoid_crowds": bool(merged.get("avoid_crowds")),
                "avoid_crowds_source": merged.get("avoid_crowds_source") or "default",
            },
            destination_place_id=(
                aggregate.candidate_destinations[index].place_id
                if index < len(aggregate.candidate_destinations)
                else aggregate.destination_place.place_id
            ),
            destination_name=(
                aggregate.candidate_destinations[index].name
                if index < len(aggregate.candidate_destinations)
                else aggregate.destination_place.name
            ),
            branch_coverage=aggregate.branch_coverage,
            stage_a_factors=aggregate.stage_a_factors,
        )
        digest["_canonical_itinerary"] = aggregate.candidate_itineraries[index]
        digest["_evidence_snapshot"] = {
            "id": snapshot_id,
            "observed_at": snapshot_observed_at,
        }
        digest["_hard_constraints"] = hard_constraints[index]
        digests.append(digest)
    return digests


def _candidate_set_payload(
    *,
    aggregate: AggregatePreparation,
    merged: dict[str, Any],
    accepted_destination_label: str,
    used_discovery_set_id: str | None,
    destination_discovery_set_id: str | None,
    waypoint_discovery_set_id: str | None,
    resolved_place_id: str | None,
    candidates: list[dict[str, Any]],
    coverage: dict[str, str],
    status: str,
    waypoints: list[str],
    snapshot_id: str,
    snapshot_observed_at: str,
    destination_options: list[tuple[Any, str | None]],
) -> dict[str, Any]:
    destination_option_ids = {
        str(place_id or "").strip()
        for _place, place_id in destination_options
        if str(place_id or "").strip()
    }
    return {
        "tool_input": merged,
        "discovery_set_id": used_discovery_set_id,
        "destination_discovery_set_id": destination_discovery_set_id,
        "waypoint_discovery_set_id": waypoint_discovery_set_id,
        "destination_place_id": resolved_place_id,
        "destination_place_ids": [
            str(place.place_id)
            for place in aggregate.candidate_destinations
            if str(place.place_id or "").strip()
        ],
        "destination_selection_mode": (
            "comparison" if len(destination_option_ids) > 1 else "single"
        ),
        "origin_raw": merged.get("origin"),
        "destination_raw": accepted_destination_label or merged.get("destination"),
        "origin_place": aggregate.origin_place.to_event_point(),
        "destination_place": aggregate.destination_place.to_event_point(),
        "departure_time": merged.get("departure_time"),
        "arrival_by": merged.get("arrival_by"),
        "excluded": sorted(set(merged.get("exclude_modes") or [])),
        "excluded_route_ids": list(merged.get("excluded_route_ids") or []),
        "parsed_routes": aggregate.parsed_routes,
        "scored": aggregate.scored,
        "relevant_alerts": aggregate.relevant_alerts,
        "incidents": aggregate.incidents,
        "event_evidence_status": aggregate.event_evidence_status,
        "event_impacts": aggregate.event_impacts,
        "event_failures": aggregate.event_failures,
        "crowd_search_metadata": aggregate.crowd_search_metadata,
        "incident_scan_metadata": aggregate.incident_scan_metadata,
        "evidence_envelopes": serialize_evidence_envelopes(
            aggregate.evidence_envelopes
        ),
        "candidate_evidence": aggregate.candidate_evidence,
        "branch_coverage": aggregate.branch_coverage,
        "collect_crowd_evidence": aggregate.collect_crowd_evidence,
        "candidates": candidates,
        "evidence_coverage": coverage,
        "route_status": status,
        "hard_constraints": {"required": True},
        "candidate_kind": "multi_stop" if waypoints else "single_leg",
        "aggregate_segments": aggregate.aggregate_segments,
        "scenario_mode": merged["scenario"],
        "waypoints": merged.get("waypoints") or [],
        "timings": aggregate.timings,
        "snapshot_id": snapshot_id,
        "snapshot_observed_at": snapshot_observed_at,
    }


def _update_trip_state(
    ctx: ToolContext,
    merged: dict[str, Any],
    set_id: str,
    presentation_allowed: bool,
    destination_options: list[tuple[Any, str | None]],
    accepted_destination_label: str,
    destination_discovery_set_id: str | None,
    resolved_place_id: str | None,
) -> None:
    session = ctx.session if isinstance(ctx.session, dict) else None
    if session is None:
        return
    if merged["scenario"] == "what_if":
        trip_state_module.bind_temporary_candidate_set(
            session,
            set_id,
            base_candidate_set_id=trip_state_module.get_trip_state(session).get(
                "active_candidate_set_id"
            ),
        )
        return
    trip_state_module.discard_scenario(session)
    if presentation_allowed:
        trip_state_module.update_trip_state(
            session,
            origin=merged.get("origin"),
            destination=accepted_destination_label or merged.get("destination"),
            waypoints=merged.get("waypoints") or [],
            planning_mode=(
                "arrive_by"
                if merged.get("arrival_by")
                else "depart_at"
                if merged.get("departure_time")
                else "leave_now"
            ),
            requested_departure=merged.get("departure_time"),
            requested_arrival=merged.get("arrival_by"),
            active_candidate_set_id=set_id,
            selected_candidate_id=None,
        )
    if len(destination_options) > 1 and destination_discovery_set_id:
        trip_state_module.bind_discovery_set(session, destination_discovery_set_id)
    elif destination_discovery_set_id and resolved_place_id:
        trip_state_module.bind_discovery_context(
            session,
            discovery_set_id=destination_discovery_set_id,
            selected_place_id=resolved_place_id,
        )
    elif resolved_place_id:
        # Keep the opaque selected identity without granting discovery-set
        # authority.  This is the safe legacy/provider-resolution fallback:
        # only an explicitly returned server-owned destination set may bind
        # discovery context.
        trip_state_module.bind_selected_place(session, resolved_place_id)


def public_candidate_digest(digest: dict[str, Any]) -> dict[str, Any]:
    """Expose finalized comparison facts without ranking or provider details."""

    return {
        **_public_identity(digest),
        "branch_coverage": _public_branch_coverage(digest.get("branch_coverage")),
        "stage_a_factors": _public_stage_a_factors(digest.get("stage_a_factors")),
        "finalized": bool(digest.get("finalized")),
        "evidence_snapshot": dict(digest.get("evidence_snapshot") or {}),
        "comparison": _public_comparison(digest),
    }


def _public_identity(digest: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": str(digest.get("candidate_id") or ""),
        "destination_place_id": str(digest.get("destination_place_id") or "") or None,
        "destination_name": str(digest.get("destination_name") or "") or None,
    }


def _public_comparison(digest: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "unordered_factor_comparison",
        "timing": _public_timing(digest),
        "service_chain": _public_service_chain(digest),
        "hard_constraints": _public_hard_constraints(digest),
        "service_conditions": _public_service_conditions(digest),
        "coverage": dict(digest.get("evidence_coverage") or {}),
        "soft_preferences": _public_soft_preferences(digest.get("soft_preferences")),
    }


def _public_timing(digest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: digest.get(key)
        for key in (
            "duration_minutes",
            "wait_minutes",
            "in_vehicle_minutes",
            "walking_minutes",
            "in_station_transfer_minutes",
            "transfers",
            "departure_at",
            "arrival_at",
            "arrival_context",
            "waypoint_count",
        )
    }


def _public_service_chain(digest: dict[str, Any]) -> dict[str, Any]:
    return {
        "transit_lines": list(digest.get("transit_lines") or []),
        "transfer_facts": list(digest.get("transfer_facts") or []),
    }


def _public_hard_constraints(digest: dict[str, Any]) -> dict[str, Any]:
    return {
        "satisfied": bool(digest.get("hard_constraints_satisfied")),
        "accessibility_required": bool(digest.get("accessibility_required")),
        "accessibility": digest.get("accessibility_status", "unknown"),
    }


def _public_service_conditions(digest: dict[str, Any]) -> dict[str, Any]:
    return {
        "official_alerts": list(digest.get("official_service_impacts") or []),
        "confirmed_incidents": list(digest.get("confirmed_incident_impacts") or []),
        "possible_vehicle_signals": list(
            digest.get("unconfirmed_material_claims") or []
        ),
        "event_or_crowd": [
            _public_event_impact(value)
            for value in digest.get("event_or_crowd_impacts") or []
        ],
    }


def _public_soft_preferences(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in ("routing_preference", "preferred_modes", "avoid_crowds")
        if key in value
    }


def _public_event_impact(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "event_name": value.get("event_name"),
        "venue_name": value.get("venue_name"),
        "exposure_window": value.get("exposure_window"),
        "confidence": value.get("confidence"),
        "potential_event_risk": True,
    }
    if value.get("scoring_authorized") is True:
        result["potential_event_risk_level"] = value.get("crowd_level")
    return {key: item for key, item in result.items() if item not in (None, "")}


def _public_branch_coverage(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = {
            "place_id": str(item.get("place_id") or "") or None,
            "name": str(item.get("name") or "") or None,
            "status": str(item.get("status") or "") or "unavailable",
            "coverage": str(item.get("coverage") or "") or "unavailable",
        }
        if row["place_id"] or row["name"]:
            rows.append(row)
    return rows


def _public_stage_a_factors(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    factors: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        factors.append(
            {
                "code": code,
                "status": str(item.get("status") or "validated"),
                "basis": [
                    str(basis)
                    for basis in item.get("basis") or []
                    if str(basis).strip()
                ],
            }
        )
    return factors


__all__ = ("persist_route_candidates", "public_candidate_digest")
