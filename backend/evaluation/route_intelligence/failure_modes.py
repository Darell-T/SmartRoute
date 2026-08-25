"""Deterministic evaluation source-ablation reports for route-intelligence replays.

This module makes no selection and does not estimate a score.  It re-runs the
same comparison with one production source removed, then reports only observed
differences in recorded advisor selection, rider-facing explanation, and
structured evidence.  A scenario with recorded ablation transcripts is strict:
every requested active source must have an exact transcript.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from evaluation.route_intelligence.comparison import compare_scenario
from evaluation.route_intelligence.metrics import SourceEffect
from evaluation.route_intelligence.replay import (
    CANONICAL_SOURCE_NAMES,
    ReplayScenario,
    ScenarioValidationError,
    canonical_sources,
    load_scenario,
)
from evaluation.route_intelligence.shadow import SourceContribution


SOURCE_CONTRIBUTIONS: Mapping[str, SourceContribution] = {
    "mta": SourceContribution.MTA_ALERTS,
    "subway_vehicle_detection": SourceContribution.STALLED_SUBWAY,
    "bus_vehicle_detection": SourceContribution.STALLED_BUS,
    "grok_x": SourceContribution.GROK_X,
    "grok_web": SourceContribution.GROK_WEB,
    "511ny": SourceContribution.CACHED_511NY,
    "ticketmaster": SourceContribution.TICKETMASTER,
}


def _without_source(enabled_sources: Iterable[str], source: str) -> frozenset[str]:
    """Disable exactly one canonical source while respecting the legacy alias."""

    enabled = set(enabled_sources)
    if source not in CANONICAL_SOURCE_NAMES:
        raise ScenarioValidationError(f"unknown ablation source: {source}")
    if source not in canonical_sources(enabled):
        raise ScenarioValidationError(f"cannot ablate inactive source: {source}")
    if source in {"subway_vehicle_detection", "bus_vehicle_detection"} and "vehicle_detection" in enabled:
        enabled.remove("vehicle_detection")
        enabled.add(
            "bus_vehicle_detection"
            if source == "subway_vehicle_detection"
            else "subway_vehicle_detection"
        )
    else:
        enabled.discard(source)
    return frozenset(enabled)


def _evidence_projection(report: Mapping[str, Any]) -> dict[str, Any]:
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    return {
        "mta_alert_count": int(evidence.get("mta_alert_count") or 0),
        "incident_ids": tuple(evidence.get("incident_ids") or ()),
        "source_counts": dict(evidence.get("source_counts") or {}),
        "stalled_train_count": int(evidence.get("stalled_train_count") or 0),
        "stalled_bus_count": int(evidence.get("stalled_bus_count") or 0),
        "ticketmaster_event_ids": tuple(evidence.get("ticketmaster_event_ids") or ()),
        "ny511_snapshot_status": evidence.get("ny511_snapshot_status"),
        "scan_status": report.get("scan_status"),
    }


def _effect(full: Mapping[str, Any], ablated: Mapping[str, Any]) -> SourceEffect:
    full_intelligence = full.get("intelligence") if isinstance(full.get("intelligence"), Mapping) else {}
    ablated_intelligence = ablated.get("intelligence") if isinstance(ablated.get("intelligence"), Mapping) else {}
    if full_intelligence.get("selected_route_id") != ablated_intelligence.get("selected_route_id"):
        return SourceEffect.CHANGED_ROUTE
    # No deterministic numeric scorer participates in these replays.  A
    # changed evidence set or rider-visible recommendation reason is therefore
    # truthfully reported as explanation-only, never as a fabricated score.
    if (
        full_intelligence.get("recommendation_reason") != ablated_intelligence.get("recommendation_reason")
        or _evidence_projection(full) != _evidence_projection(ablated)
    ):
        return SourceEffect.CHANGED_EXPLANATION_ONLY
    return SourceEffect.HAD_NO_EFFECT


async def run_source_ablations(
    scenario: ReplayScenario | str,
) -> dict[str, Any]:
    """Compare all active sources against the same deterministic scenario.

    ``recorded`` is false for legacy fixtures that predate dedicated ablation
    transcripts.  They can still be replayed normally, but this runner refuses
    to turn the ordinary intelligence transcript into a claim about a source's
    decision contribution.
    """

    parsed = scenario if isinstance(scenario, ReplayScenario) else load_scenario(scenario)
    full = await compare_scenario(parsed, check_expected=False)
    active_sources = sorted(canonical_sources(parsed.enabled_sources))
    # Comparison exposes only variant names after it validates the transcripts.
    # This avoids a second fixture-normalization pass (and keeps the ablation
    # report wholly inside the same no-network comparison boundary).
    variants = set(full.get("recorded_ablation_sources") or ())
    if variants:
        missing = set(active_sources) - variants
        if missing:
            raise ScenarioValidationError(
                f"advisor_outputs.ablations is missing active source variants: {sorted(missing)}"
            )

    ablations: list[dict[str, Any]] = []
    for source in active_sources:
        if not variants:
            ablations.append(
                {
                    "source": source,
                    "source_contribution": SOURCE_CONTRIBUTIONS[source].value,
                    "recorded": False,
                    "status": "not_recorded",
                }
            )
            continue
        disabled_sources = _without_source(parsed.enabled_sources, source)
        report = await compare_scenario(
            parsed, enabled_sources=disabled_sources, check_expected=False
        )
        ablations.append(
            {
                "source": source,
                "source_contribution": SOURCE_CONTRIBUTIONS[source].value,
                "recorded": True,
                "status": "complete",
                "enabled_sources": report["enabled_sources"],
                "selected_route_id": report["intelligence"]["selected_route_id"],
                "source_effect": _effect(full, report).value,
                "evidence": _evidence_projection(report),
                "advisor_variant": report["advisor_variant"],
            }
        )
    return {
        "scenario_id": parsed.scenario_id,
        "all_sources": {
            "selected_route_id": full["intelligence"]["selected_route_id"],
            "evidence": _evidence_projection(full),
            "advisor_variant": full["advisor_variant"],
        },
        "ablations": ablations,
    }
