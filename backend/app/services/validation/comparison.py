"""Offline baseline-versus-intelligence route replay comparisons.

This runner deliberately consumes :mod:`validation.replay` inputs, which have
already travelled through the application's provider parsers, geospatial
matcher and conservative incident merger.  It never invokes an advisor or a
provider: recorded advisor transcripts exercise the production selection
parser instead.  That keeps replay results deterministic while preserving the
same advisor payload boundary used by trip planning.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.services import ai_advisor
from app.services.agent.tools import venue_crowd_window
from app.services.agent.tools._types import ToolContext
from app.services.trips import advisor_context, scoring
from app.services.trips.incident_merge import merge_incident_evidence
from app.services.trips.incidents import _normalize_advisor_incident

from .replay import (
    ReplayFixtureAdapters,
    ReplayScenario,
    ScenarioValidationError,
    load_all_scenarios,
    load_scenario,
    network_disabled,
)


def _candidate_id(index: int) -> str:
    return f"candidate-{index}"


def _safe_text(value: object, limit: int = 180) -> str:
    """Keep reports useful without copying model output, URLs, or coordinates.

    ``trips.text`` exposes only its private rider-text helper. Validation has
    different reporting rules, so it owns this tiny whitespace-and-length
    sanitizer rather than depending on that private implementation.
    """
    value = re.sub(r"https?://\S+", "[link removed]", str(value or ""))
    # Precise coordinates are not needed to explain a fixture decision.
    value = re.sub(r"-?\d{1,3}\.\d{4,}", "[coordinate removed]", value)
    value = " ".join(value.split()).strip()
    return value if len(value) <= limit else value[: max(0, limit - 3)].rstrip() + "..."


def _source_tokens(row: Mapping[str, Any]) -> set[str]:
    raw = row.get("sources") or row.get("source") or ""
    values = raw if isinstance(raw, (list, tuple, set)) else re.split(r"[,|]", str(raw))
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _grok_incidents_for_sources(rows: Iterable[Mapping[str, Any]], enabled: frozenset[str]) -> list[dict[str, Any]]:
    allowed: set[str] = set()
    if "grok_x" in enabled:
        allowed.update({"grok_x", "x", "x_search"})
    if "grok_web" in enabled:
        allowed.update({"grok_web", "web", "web_search"})
    if not allowed:
        return []
    return [dict(row) for row in rows if _source_tokens(row) & allowed]


def _matched_511_as_incident(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project the production matcher result into the existing advisor contract."""
    nearest = row.get("nearest_stop") if isinstance(row.get("nearest_stop"), Mapping) else {}
    return {
        "source": "511ny",
        "location": _safe_text(row.get("roadway_name") or nearest.get("stop_name"), 100),
        "nearby_station": _safe_text(nearest.get("stop_name"), 80),
        "severity": _safe_text(row.get("severity"), 24).lower() or "medium",
        "description": _safe_text(row.get("description"), 220),
        # Used only during deterministic merge; it never appears in reports.
        "source_id": _safe_text(row.get("source_id"), 100),
        "impact_scope": _safe_text(row.get("impact_scope"), 48),
    }


def _inside_window(now: datetime, start: object, end: object) -> bool:
    try:
        parsed_start = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        parsed_end = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed_start.tzinfo is None or parsed_end.tzinfo is None:
        return False
    return parsed_start <= now <= parsed_end


async def ticketmaster_impacts_for_replay(
    events: Iterable[Mapping[str, Any]], *, frozen_time: datetime, enabled: bool
) -> list[dict[str, Any]]:
    """Use the production crowd-window tool, retaining only active windows.

    The static venue tool is intentionally called rather than reproduced here;
    cancelled, unrecognized, distant (no recognized venue) and out-of-window
    events cannot influence an advisor payload.
    """
    if not enabled:
        return []
    impacts: list[dict[str, Any]] = []
    for event in events:
        venue = str(event.get("venue_key") or "").strip()
        end = event.get("estimated_end_iso")
        if not venue or not end:
            continue
        result = await venue_crowd_window.execute(
            {
                "venue": venue,
                "event_end_iso": end,
                "event_start_iso": event.get("start_iso") or "",
                "event_status": event.get("status") or "",
                "start_time_status": event.get("start_time_status") or "",
            },
            ToolContext(now_et=frozen_time.isoformat()),
        )
        if not result.ok or not isinstance(result.data, Mapping):
            continue
        window = result.data
        active = _inside_window(frozen_time, window.get("pre_event_start_iso"), window.get("pre_event_end_iso")) or _inside_window(
            frozen_time, window.get("surge_start_iso"), window.get("surge_end_iso")
        )
        if not active:
            continue
        starts = [value for value in (window.get("pre_event_start_iso"), window.get("surge_start_iso")) if value]
        ends = [value for value in (window.get("pre_event_end_iso"), window.get("surge_end_iso")) if value]
        impacts.append(
            {
                "event_id": _safe_text(event.get("event_id"), 80),
                "title": _safe_text(event.get("name"), 140),
                "venue": _safe_text(event.get("venue_name") or venue, 100),
                "stations": list(window.get("stations") or ()),
                "lines": list(window.get("lines") or ()),
                "impact_scope": "station_crowding",
                "crowd_level": "moderate",
                "window_start_iso": min(starts) if starts else "",
                "window_end_iso": max(ends) if ends else "",
            }
        )
    return impacts


def _expectation(scenario: ReplayScenario) -> dict[str, Any]:
    expected = scenario.expected
    required = {"baseline_route_id", "intelligence_route_id", "route_should_change"}
    missing = required - set(expected)
    if missing:
        raise ScenarioValidationError(f"expected is missing required fields: {sorted(missing)}")
    baseline = expected["baseline_route_id"]
    intelligence = expected["intelligence_route_id"]
    changed = expected["route_should_change"]
    if not isinstance(baseline, str) or not re.fullmatch(r"candidate-\d+", baseline):
        raise ScenarioValidationError("expected.baseline_route_id must be a candidate id")
    if not isinstance(intelligence, str) or not re.fullmatch(r"candidate-\d+", intelligence):
        raise ScenarioValidationError("expected.intelligence_route_id must be a candidate id")
    if not isinstance(changed, bool):
        raise ScenarioValidationError("expected.route_should_change must be boolean")
    result = {"baseline_route_id": baseline, "intelligence_route_id": intelligence, "route_should_change": changed}
    optional_statuses = {
        "scan_status": {"complete", "partial", "failed", "disabled"},
        "ny511_snapshot_status": {"fresh", "stale", "unavailable", "disabled"},
    }
    for key, allowed in optional_statuses.items():
        if key not in expected:
            continue
        value = expected[key]
        if not isinstance(value, str) or value not in allowed:
            raise ScenarioValidationError(f"expected.{key} must be one of {sorted(allowed)}")
        result[key] = value
    return result


def _summarize_selection(
    *, transcript: str, payload: Mapping[str, Any], mode: str
) -> dict[str, Any]:
    routes = payload["routes"]
    selected_index, analysis = advisor_context.parse_advisor_selection(transcript, len(routes))
    labels = payload.get("route_candidate_labels") or []
    label = labels[selected_index].get("displayLabel") if selected_index < len(labels) else _candidate_id(selected_index)
    selected_reason = analysis.get(selected_index, {}).get("recommendation_reason") or ""
    # This is explicitly non-decision diagnostic data. The selection always
    # comes from the production parser over the recorded advisor transcript.
    core_diagnostics = scoring._score_routes(routes, payload.get("service_alerts") or [])
    return {
        "mode": mode,
        "selected_route_id": _candidate_id(selected_index),
        "selected_route_label": _safe_text(label, 100),
        "recommendation_reason": _safe_text(selected_reason, 180),
        "core_mta_display_diagnostics": core_diagnostics,
    }


def _source_summary(
    payload: Mapping[str, Any],
    snapshot_status: str,
    *,
    evidence_ids: Iterable[str],
    associations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    incidents = payload.get("incidents") or []
    source_counts: dict[str, int] = {}
    for row in incidents:
        if isinstance(row, Mapping):
            for source in _source_tokens(row):
                source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "incident_count": len(incidents),
        "incident_ids": [item for item in dict.fromkeys(_safe_text(value, 100) for value in evidence_ids) if item],
        "source_counts": source_counts,
        "stalled_train_count": len(payload.get("stalled_trains") or []),
        "stalled_bus_count": len(payload.get("stalled_buses") or []),
        "ticketmaster_event_ids": [
            _safe_text(row.get("event_id"), 80) for row in payload.get("ticketmaster_event_impacts") or []
            if isinstance(row, Mapping)
        ],
        "ny511_snapshot_status": snapshot_status,
        "association_diagnostics": [
            {
                "source_id": _safe_text(row.get("source_id"), 100),
                "source": _safe_text(row.get("source"), 32),
                "candidate_route_ids": [
                    _safe_text(value, 80) for value in row.get("affected_candidate_route_ids", [])
                    if _safe_text(value, 80)
                ],
                "impact_scope": _safe_text(row.get("impact_scope"), 48),
            }
            for row in associations
            if _safe_text(row.get("source_id"), 100)
        ],
    }


async def compare_scenario(
    scenario: ReplayScenario | str | Path,
    *,
    enabled_sources: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compare recorded baseline and intelligence decisions for one scenario."""
    parsed = scenario if isinstance(scenario, ReplayScenario) else load_scenario(scenario)
    sources = frozenset(enabled_sources) if enabled_sources is not None else parsed.enabled_sources
    unknown = sources - {
        "mta", "vehicle_detection", "subway_vehicle_detection", "bus_vehicle_detection",
        "grok_x", "grok_web", "511ny", "ticketmaster",
    }
    if unknown:
        raise ScenarioValidationError(f"unknown enabled sources: {sorted(unknown)}")
    expected = _expectation(parsed)
    total_started = time.perf_counter()
    with network_disabled():
        fixture_started = time.perf_counter()
        inputs = await ReplayFixtureAdapters(parsed).load()
        fixture_normalization_ms = (time.perf_counter() - fixture_started) * 1000
        service_alerts = inputs.mta_alerts if "mta" in sources else []
        baseline_started = time.perf_counter()
        baseline_payload = advisor_context.build_advisor_payload(
            routes=inputs.route_candidates,
            service_alerts=service_alerts,
            mode=advisor_context.PlanningMode.BASELINE,
        )
        baseline = _summarize_selection(
            transcript=inputs.advisor_outputs["baseline"], payload=baseline_payload, mode="baseline"
        )
        baseline_processing_ms = (time.perf_counter() - baseline_started) * 1000

        intelligence_started = time.perf_counter()
        raw_incidents = _grok_incidents_for_sources(inputs.grok_incidents, sources)
        associations: list[Mapping[str, Any]] = []
        fixture_snapshot_status = str(getattr(inputs.ny511_snapshot, "status", "unavailable"))
        snapshot_status = fixture_snapshot_status if "511ny" in sources else "disabled"
        # A stale or unavailable snapshot is diagnostic information, not
        # actionable evidence. It must never influence a replay decision.
        if "511ny" in sources and fixture_snapshot_status == "fresh":
            associations = [dict(row) for row in inputs.ny511_matches if isinstance(row, Mapping)]
            raw_incidents.extend(_matched_511_as_incident(row) for row in associations)
        evidence_ids = [
            _safe_text(row.get("source_id") or row.get("id"), 100)
            for row in raw_incidents
        ]
        # This invokes the same conservative current/deduplication logic used
        # before advisor normalization, not a replay-only merger.
        intelligence_incidents = [
            _normalize_advisor_incident(row)
            for row in merge_incident_evidence(raw_incidents, now=parsed.clock.now())
            if isinstance(row, Mapping)
        ]
        ticketmaster_impacts = await ticketmaster_impacts_for_replay(
            inputs.ticketmaster_events,
            frozen_time=parsed.clock.now(),
            enabled="ticketmaster" in sources,
        )
        intelligence_payload = advisor_context.build_advisor_payload(
            routes=inputs.route_candidates,
            service_alerts=service_alerts,
            incidents=intelligence_incidents,
            stalled_trains=inputs.stalled_trains if {"vehicle_detection", "subway_vehicle_detection"} & sources else [],
            stalled_buses=inputs.stalled_buses if {"vehicle_detection", "bus_vehicle_detection"} & sources else [],
            ticketmaster_event_impacts=ticketmaster_impacts,
            mode=advisor_context.PlanningMode.INTELLIGENCE,
        )
        intelligence = _summarize_selection(
            transcript=inputs.advisor_outputs["intelligence"], payload=intelligence_payload, mode="intelligence"
        )
        intelligence_processing_ms = (time.perf_counter() - intelligence_started) * 1000
    latency_ms = max(0, round((time.perf_counter() - total_started) * 1000, 3))
    route_changed = baseline["selected_route_id"] != intelligence["selected_route_id"]
    scan_sources = {"grok_x", "grok_web", "511ny"} & sources
    if not scan_sources:
        scan_status = "disabled"
    elif "511ny" in sources and snapshot_status != "fresh":
        scan_status = "partial"
    else:
        scan_status = "complete"
    matched = (
        baseline["selected_route_id"] == expected["baseline_route_id"]
        and intelligence["selected_route_id"] == expected["intelligence_route_id"]
        and route_changed is expected["route_should_change"]
        and ("scan_status" not in expected or scan_status == expected["scan_status"])
        and ("ny511_snapshot_status" not in expected or snapshot_status == expected["ny511_snapshot_status"])
    )
    return {
        "scenario_id": parsed.scenario_id,
        "description": _safe_text(parsed.description, 220),
        "frozen_time": parsed.clock.now().isoformat(),
        "advisor_identity": ai_advisor.advisor_identity(),
        "enabled_sources": sorted(sources),
        "baseline": baseline,
        "intelligence": intelligence,
        "evidence": _source_summary(
            intelligence_payload, snapshot_status, evidence_ids=evidence_ids, associations=associations
        ),
        "scan_status": scan_status,
        "comparison": {
            "route_changed": route_changed,
            "expected_route_change": expected["route_should_change"],
            "matched_expectation": matched,
            "decision_latency_ms": latency_ms,
            "local_replay_timings_ms": {
                "fixture_normalization": round(max(0, fixture_normalization_ms), 3),
                "baseline_construction_and_parse": round(max(0, baseline_processing_ms), 3),
                "intelligence_evidence_payload_and_parse": round(max(0, intelligence_processing_ms), 3),
                "total_replay_comparison": latency_ms,
            },
        },
    }


def semantic_projection(report: Mapping[str, Any]) -> dict[str, Any]:
    """Stable report projection for snapshots; excludes measured latency."""
    copy = json.loads(json.dumps(report))
    comparison = copy.get("comparison")
    if isinstance(comparison, dict):
        comparison.pop("decision_latency_ms", None)
        comparison.pop("local_replay_timings_ms", None)
    return copy


async def compare_all_scenarios(*, replay_root: Path | None = None) -> list[dict[str, Any]]:
    return [await compare_scenario(scenario) for scenario in load_all_scenarios(replay_root=replay_root)]


def render_human_report(report: Mapping[str, Any]) -> str:
    """Return a concise, public-safe explanation without model transcripts."""
    baseline = report.get("baseline") if isinstance(report.get("baseline"), Mapping) else {}
    intelligence = report.get("intelligence") if isinstance(report.get("intelligence"), Mapping) else {}
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    comparison = report.get("comparison") if isinstance(report.get("comparison"), Mapping) else {}
    lines = [
        f"Scenario: {_safe_text(report.get('scenario_id'), 100)}",
        f"Baseline: {_safe_text(baseline.get('selected_route_label') or baseline.get('selected_route_id'), 120)}",
        f"Intelligence: {_safe_text(intelligence.get('selected_route_label') or intelligence.get('selected_route_id'), 120)}",
    ]
    if intelligence.get("recommendation_reason"):
        lines.append(f"Why: {_safe_text(intelligence.get('recommendation_reason'), 180)}")
    lines.append(
        "Evidence: "
        f"{int(evidence.get('incident_count') or 0)} incident(s), "
        f"{int(evidence.get('stalled_train_count') or 0)} stalled train signal(s), "
        f"{int(evidence.get('stalled_bus_count') or 0)} stalled bus signal(s), "
        f"{len(evidence.get('ticketmaster_event_ids') or [])} active event(s)."
    )
    if report.get("scan_status") or evidence.get("ny511_snapshot_status"):
        lines.append(
            "Scan: "
            f"{_safe_text(report.get('scan_status') or 'unknown', 24)}; "
            f"511NY snapshot {_safe_text(evidence.get('ny511_snapshot_status') or 'unknown', 24)}."
        )
    lines.append("Result: " + ("PASS" if comparison.get("matched_expectation") else "FAIL"))
    return "\n".join(lines)
