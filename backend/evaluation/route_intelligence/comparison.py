"""Offline evaluation baseline-versus-intelligence route replay comparisons.

This runner deliberately consumes :mod:`evaluation.route_intelligence.replay` inputs, which have
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
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.transit import venue_crowd_window as venues
from app.services.trips import scoring
from app.services.trips.route_incidents.association import (
    attach_verified_match_association,
    normalize_matcher_association,
)
from app.services.trips.route_incidents.merge import (
    filter_current_incidents,
    merge_incident_evidence,
)

from evaluation.route_intelligence import advisor, advisor_context

from .replay import (
    CANONICAL_SOURCE_NAMES,
    ReplayFixtureAdapters,
    ReplayScenario,
    SourceStatus,
    _invalid,
    canonical_sources,
    load_all_scenarios,
    load_scenario,
    network_disabled,
    source_status_for,
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


def _grok_incidents_for_sources(rows: Iterable[Mapping[str, Any]], available: frozenset[str]) -> list[dict[str, Any]]:
    allowed: set[str] = set()
    if "grok_x" in available:
        allowed.update({"grok_x", "x", "x_search"})
    if "grok_web" in available:
        allowed.update({"grok_web", "web", "web_search"})
    if not allowed:
        return []
    return [dict(row) for row in rows if _source_tokens(row) & allowed]


_OFFICIAL_511_LIFECYCLE_FIELDS = (
    "latitude",
    "longitude",
    "reported_at",
    "updated_at",
    "starts_at",
    "expected_end_at",
    "event_type",
    "event_subtype",
    "roadway_name",
    "status",
    "status_text",
)


def _snapshot_rows_by_source_id(snapshot: object) -> dict[str, Mapping[str, Any]]:
    """Index normalized snapshot rows; matcher rows alone lack lifecycle data."""

    incidents = getattr(snapshot, "incidents", [])
    result: dict[str, Mapping[str, Any]] = {}
    for incident in incidents if isinstance(incidents, list) else []:
        if isinstance(incident, Mapping):
            row = incident
        else:
            dump = getattr(incident, "model_dump", None)
            row = dump() if callable(dump) else None
        if not isinstance(row, Mapping):
            continue
        source_id = _safe_text(row.get("source_id"), 100)
        if source_id:
            result[source_id] = row
    return result


def _matched_511_as_incident(
    match: Mapping[str, Any], authoritative_row: Mapping[str, Any]
) -> dict[str, Any]:
    """Join an exact matcher result to its normalized 511NY lifecycle row.

    ``MatchedIncident`` intentionally contains only display and candidate-link
    information.  Keeping lifecycle fields from the normalized snapshot here
    ensures the production merger can reject resolved and expired official
    evidence before it reaches an advisor payload.
    """

    nearest = match.get("nearest_stop") if isinstance(match.get("nearest_stop"), Mapping) else {}
    authoritative = {
        "source": "511ny",
        "location": _safe_text(authoritative_row.get("roadway_name") or match.get("roadway_name") or nearest.get("stop_name"), 100),
        "nearby_station": _safe_text(nearest.get("stop_name"), 80),
        "severity": _safe_text(
            authoritative_row.get("severity_normalized")
            or authoritative_row.get("severity_raw")
            or match.get("severity"),
            24,
        ).lower() or "medium",
        "description": _safe_text(
            authoritative_row.get("description")
            or authoritative_row.get("comment")
            or match.get("description"),
            220,
        ),
        "source_id": _safe_text(authoritative_row.get("source_id") or match.get("source_id"), 100),
    }
    for key in _OFFICIAL_511_LIFECYCLE_FIELDS:
        if authoritative_row.get(key) is not None:
            authoritative[key] = authoritative_row[key]
    # The matcher, not the replay fixture or a model transcript, owns route,
    # mode, and scope association.  The shared adapter adds provenance only
    # after this authoritative row has been formed.
    return attach_verified_match_association(authoritative, match)


def _inside_window(now: datetime, start: object, end: object) -> bool:
    try:
        parsed_start = datetime.fromisoformat(str(start))
        parsed_end = datetime.fromisoformat(str(end))
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
        result = await venues.execute(
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


def _effective_source_statuses(
    scenario: ReplayScenario, enabled_sources: Iterable[str]
) -> dict[str, SourceStatus]:
    """Resolve manifest status metadata after a comparison-mode override."""

    return {
        source: source_status_for(scenario, enabled_sources, source)
        for source in sorted(CANONICAL_SOURCE_NAMES)
    }


def _source_is_available(statuses: Mapping[str, SourceStatus], source: str) -> bool:
    """Only complete/partial fixtures may contribute recorded evidence."""

    return statuses[source].status in {"complete", "partial"}


def _snapshot_adjusted_status(status: SourceStatus, snapshot_status: str) -> SourceStatus:
    """A stale cache is partial; an unavailable cache is a failed source.

    The actual snapshot freshness remains visible separately in reports.  This
    conversion prevents an enabled 511NY source with no usable snapshot from
    being mislabeled as a complete scan.
    """

    if status.status in {"disabled", "failed"}:
        return status
    if snapshot_status == "fresh":
        return status
    if snapshot_status == "stale":
        return SourceStatus("partial", status.errors or ("511NY snapshot is stale",))
    return SourceStatus("failed", status.errors or ("511NY snapshot is unavailable",))


def _aggregate_scan_status(statuses: Mapping[str, SourceStatus]) -> str:
    """Aggregate Grok X/web and cached-511NY availability conservatively."""

    scan_statuses = [statuses[source].status for source in ("grok_x", "grok_web", "511ny")]
    active = [status for status in scan_statuses if status != "disabled"]
    if not active:
        return "disabled"
    if all(status == "complete" for status in active):
        return "complete"
    if all(status == "failed" for status in active):
        return "failed"
    # This includes a deliberately incomplete fixture, a mix of available and
    # failed providers, and a stale 511NY cache. Never call those all-clear.
    return "partial"


def _ablation_source(
    scenario: ReplayScenario, enabled_sources: Iterable[str]
) -> str | None:
    """Return the one real source disabled relative to a scenario's default.

    Legacy ``vehicle_detection`` expands to two sources before comparison, so
    disabling only subway or bus detection is still a single-source ablation.
    Supplying additional sources is not an ablation and intentionally uses the
    primary intelligence transcript instead.
    """

    default = canonical_sources(scenario.enabled_sources)
    actual = canonical_sources(enabled_sources)
    removed = default - actual
    added = actual - default
    return next(iter(removed)) if len(removed) == 1 and not added else None


def _intelligence_transcript(
    scenario: ReplayScenario, inputs: Any, enabled_sources: Iterable[str]
) -> tuple[str, str]:
    """Select the recorded advisor variant for an exact one-source ablation."""

    source = _ablation_source(scenario, enabled_sources)
    if source is None:
        return inputs.advisor_outputs["intelligence"], "intelligence"
    variants = inputs.advisor_ablation_outputs
    if variants:
        if source not in variants:
            missing_variant = (
                f"advisor_outputs.ablations is missing the requested {source} variant"
            )
            _invalid(missing_variant)
        return variants[source], f"ablation:{source}"
    # Older scenarios have no ablation recordings. They remain valid for the
    # ordinary comparison runner, but source-ablation reports flag them as not
    # recorded rather than treating this fallback as fresh evidence.
    return inputs.advisor_outputs["intelligence"], "intelligence"


def _require_expected_candidate_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"candidate-\d+", value):
        _invalid(f"{label} must be a candidate id")
    return value


def _optional_expected_status(
    expected: Mapping[str, Any], key: str, allowed: set[str]
) -> str | None:
    if key not in expected:
        return None
    value = expected[key]
    if not isinstance(value, str) or value not in allowed:
        _invalid(f"expected.{key} must be one of {sorted(allowed)}")
    return value


def _expectation(scenario: ReplayScenario) -> dict[str, Any]:
    expected = scenario.expected
    required = {"baseline_route_id", "intelligence_route_id", "route_should_change"}
    missing = required - set(expected)
    if missing:
        _invalid(f"expected is missing required fields: {sorted(missing)}")
    changed = expected["route_should_change"]
    if not isinstance(changed, bool):
        _invalid("expected.route_should_change must be boolean")
    result = {
        "baseline_route_id": _require_expected_candidate_id(
            expected["baseline_route_id"], "expected.baseline_route_id"
        ),
        "intelligence_route_id": _require_expected_candidate_id(
            expected["intelligence_route_id"], "expected.intelligence_route_id"
        ),
        "route_should_change": changed,
    }
    optional_statuses = {
        "scan_status": {"complete", "partial", "failed", "disabled"},
        "ny511_snapshot_status": {"fresh", "stale", "unavailable", "disabled"},
    }
    for key, allowed in optional_statuses.items():
        status = _optional_expected_status(expected, key, allowed)
        if status is not None:
            result[key] = status
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
    association_diagnostics: list[dict[str, Any]] = []
    for row in associations:
        source_id = _safe_text(row.get("source_id"), 100)
        if not source_id:
            continue
        normalized = normalize_matcher_association(row)
        association_diagnostics.append(
            {
                "source_id": source_id,
                "source": _safe_text(row.get("source"), 32),
                "candidate_route_ids": list(normalized.get("affected_candidate_route_ids") or ()),
                "modes": list(normalized.get("affected_modes") or ()),
                "relevance_by_mode": dict(normalized.get("relevance_by_mode") or {}),
                "impact_scope": _safe_text(normalized.get("impact_scope"), 48),
            }
        )
    return {
        "incident_count": len(incidents),
        "mta_alert_count": len(payload.get("service_alerts") or []),
        "incident_ids": [item for item in dict.fromkeys(_safe_text(value, 100) for value in evidence_ids) if item],
        "source_counts": source_counts,
        "stalled_train_count": len(payload.get("stalled_trains") or []),
        "stalled_bus_count": len(payload.get("stalled_buses") or []),
        "ticketmaster_event_ids": [
            _safe_text(row.get("event_id"), 80) for row in payload.get("ticketmaster_event_impacts") or []
            if isinstance(row, Mapping)
        ],
        "ny511_snapshot_status": snapshot_status,
        "association_diagnostics": association_diagnostics,
    }


async def compare_scenario(
    scenario: ReplayScenario | str | Path,
    *,
    enabled_sources: Iterable[str] | None = None,
    check_expected: bool = True,
) -> dict[str, Any]:
    """Compare recorded baseline and intelligence decisions for one scenario."""
    parsed = scenario if isinstance(scenario, ReplayScenario) else load_scenario(scenario)
    sources = frozenset(enabled_sources) if enabled_sources is not None else parsed.enabled_sources
    unknown = sources - {"vehicle_detection", *CANONICAL_SOURCE_NAMES}
    if unknown:
        _invalid(f"unknown enabled sources: {sorted(unknown)}")
    expected = _expectation(parsed)
    total_started = time.perf_counter()
    with network_disabled():
        fixture_started = time.perf_counter()
        inputs = await ReplayFixtureAdapters(parsed).load()
        fixture_normalization_ms = (time.perf_counter() - fixture_started) * 1000
        source_statuses = _effective_source_statuses(parsed, sources)
        fixture_snapshot_status = str(getattr(inputs.ny511_snapshot, "status", "unavailable"))
        source_statuses["511ny"] = _snapshot_adjusted_status(
            source_statuses["511ny"], fixture_snapshot_status
        )
        available_sources = frozenset(
            source for source, status in source_statuses.items() if _source_is_available(source_statuses, source)
        )
        service_alerts = inputs.mta_alerts if "mta" in available_sources else []
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
        raw_incidents = _grok_incidents_for_sources(inputs.grok_incidents, available_sources)
        associations: list[Mapping[str, Any]] = []
        snapshot_status = (
            fixture_snapshot_status
            if "511ny" in canonical_sources(sources)
            else "disabled"
        )
        # A stale or unavailable snapshot is diagnostic information, not
        # actionable evidence. It must never influence a replay decision.
        if "511ny" in available_sources and fixture_snapshot_status == "fresh":
            snapshot_rows = _snapshot_rows_by_source_id(inputs.ny511_snapshot)
            matched_pairs = [
                (dict(match), _matched_511_as_incident(match, snapshot_rows[source_id]))
                for match in inputs.ny511_matches
                if isinstance(match, Mapping)
                if (source_id := _safe_text(match.get("source_id"), 100)) in snapshot_rows
            ]
            # Apply the production lifecycle filter before retaining matching
            # diagnostics as well as before the final evidence merge. A match
            # cannot revive a resolved/expired official snapshot record.
            current_matches = filter_current_incidents(
                [incident for _match, incident in matched_pairs], now=parsed.clock.now()
            )
            current_match_ids = {
                _safe_text(incident.get("source_id"), 100)
                for incident in current_matches
                if _safe_text(incident.get("source_id"), 100)
            }
            associations = [
                match for match, incident in matched_pairs
                if _safe_text(incident.get("source_id"), 100) in current_match_ids
            ]
            raw_incidents.extend(current_matches)
        # This invokes the same conservative current/deduplication logic used
        # before advisor normalization, not a replay-only merger.
        merged_incidents = merge_incident_evidence(raw_incidents, now=parsed.clock.now())
        evidence_ids = [
            _safe_text(row.get("source_id") or row.get("id"), 100)
            for row in merged_incidents
            if isinstance(row, Mapping)
        ]
        # Replay fixtures deliberately retain provider-shaped multi-source
        # evidence so the offline merger and ablation reports can verify its
        # provenance. Production incident scanning has a stricter separate
        # citation/eligibility contract and never consumes these fixtures.
        intelligence_incidents = [
            dict(row) for row in merged_incidents if isinstance(row, Mapping)
        ]
        ticketmaster_impacts = await ticketmaster_impacts_for_replay(
            inputs.ticketmaster_events,
            frozen_time=parsed.clock.now(),
            enabled="ticketmaster" in available_sources,
        )
        intelligence_payload = advisor_context.build_advisor_payload(
            routes=inputs.route_candidates,
            service_alerts=service_alerts,
            incidents=intelligence_incidents,
            stalled_trains=inputs.stalled_trains if "subway_vehicle_detection" in available_sources else [],
            stalled_buses=inputs.stalled_buses if "bus_vehicle_detection" in available_sources else [],
            ticketmaster_event_impacts=ticketmaster_impacts,
            mode=advisor_context.PlanningMode.INTELLIGENCE,
        )
        intelligence_transcript, advisor_variant = _intelligence_transcript(parsed, inputs, sources)
        intelligence = _summarize_selection(
            transcript=intelligence_transcript, payload=intelligence_payload, mode="intelligence"
        )
        intelligence_processing_ms = (time.perf_counter() - intelligence_started) * 1000
    latency_ms = max(0, round((time.perf_counter() - total_started) * 1000, 3))
    route_changed = baseline["selected_route_id"] != intelligence["selected_route_id"]
    scan_status = _aggregate_scan_status(source_statuses)
    expectation_matches = (
        baseline["selected_route_id"] == expected["baseline_route_id"]
        and intelligence["selected_route_id"] == expected["intelligence_route_id"]
        and route_changed is expected["route_should_change"]
        and ("scan_status" not in expected or scan_status == expected["scan_status"])
        and ("ny511_snapshot_status" not in expected or snapshot_status == expected["ny511_snapshot_status"])
    )
    matched = expectation_matches if check_expected else None
    return {
        "scenario_id": parsed.scenario_id,
        "description": _safe_text(parsed.description, 220),
        "frozen_time": parsed.clock.now().isoformat(),
        "advisor_identity": advisor.advisor_identity(),
        "enabled_sources": sorted(sources),
        "source_status": {
            source: status.as_dict() for source, status in sorted(source_statuses.items())
        },
        "advisor_variant": advisor_variant,
        # Recorded transcripts intentionally isolate live-model nondeterminism.
        # This proves payload/parser contract conformance to an authored
        # expectation, not autonomous model accuracy or causal improvement.
        "decision_basis": "recorded_advisor_transcript",
        # Names only: enough for ablation orchestration to verify coverage
        # without re-normalizing fixtures or exposing model transcripts.
        "recorded_ablation_sources": sorted(inputs.advisor_ablation_outputs),
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
