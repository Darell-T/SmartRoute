"""Bounded incident display and evidence helpers for area conditions.

The non-routing area tool reads the shared background incident index and
combines confirmed records with imprecise nearby warnings for display. These
helpers keep that shaping payload-free and bounded, and they keep the area
tool's module small.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.trips import text

MAX_INCIDENTS = 5
TRUTHFUL_INCIDENT_STATUSES = frozenset(
    {"complete", "partial", "stale", "unavailable", "unscanned", "failed"}
)
LOOKUP_METADATA_KEYS = ("lookup_status", "coverage_status", "lookup_kind")


def safe_incidents(value: object) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, Mapping):
            continue
        severity = str(row.get("severity") or "medium").casefold()
        display: dict[str, Any] = {
            "location": text._safe_text(row.get("location") or row.get("location_name"), 100),
            "nearby_station": text._safe_text(row.get("nearby_station"), 80),
            "severity": severity if severity in {"low", "medium", "high", "critical"} else "medium",
            "description": text._safe_text(row.get("description"), 220),
            "source": text._safe_text(row.get("source"), 60),
        }
        state = str(row.get("state") or "").casefold()
        if state in {"unconfirmed", "confirmed", "rejected", "refreshing", "stale", "resolved"}:
            display["state"] = state
        if isinstance(row.get("corroborated"), bool):
            display["corroborated"] = row["corroborated"]
        incidents.append(display)
        if len(incidents) >= MAX_INCIDENTS:
            break
    return incidents


def display_incidents(value: object) -> list[dict[str, Any]]:
    """Bounded, deduplicated display rows from index incidents + warnings.

    Area contexts are ``area-*`` (never route candidate IDs), so confirmed and
    imprecise nearby index records arrive as warnings. This non-routing tool
    combines both for display; identity deduplication keeps one row per
    canonical incident while preserving each record's bounded ``state`` and
    ``corroborated`` provenance.
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, Mapping):
            continue
        incident_id = text._safe_text(row.get("incident_id"), 120)
        key = incident_id or text._safe_text(
            row.get("location") or row.get("location_name"), 100
        )
        if key and key not in merged:
            merged[key] = row
    return safe_incidents(list(merged.values()))


def safe_sources(value: object) -> dict[str, list[str]] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, list[str]] = {}
    for key in ("completed", "errors"):
        values = value.get(key)
        if isinstance(values, list):
            result[key] = [
                text._safe_text(item, 80) for item in values[:6] if text._safe_text(item, 80)
            ]
    return result or None


def incident_evidence(value: object) -> dict[str, Any]:
    """Bounded, payload-free evidence summary for the shared index lookup."""
    metadata = value.get("scan_metadata") if isinstance(value, Mapping) else None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    status = str(metadata.get("status") or "failed")
    evidence: dict[str, Any] = {
        "status": status if status in TRUTHFUL_INCIDENT_STATUSES else "failed",
    }
    if isinstance(metadata.get("scanned_at"), str):
        evidence["scanned_at"] = metadata["scanned_at"]
    if isinstance(metadata.get("cache_hit"), bool):
        evidence["cache_hit"] = metadata["cache_hit"]
    for key in LOOKUP_METADATA_KEYS:
        lookup_value = metadata.get(key)
        if isinstance(lookup_value, str) and lookup_value:
            evidence[key] = lookup_value
    if isinstance(metadata.get("warning_count"), int):
        evidence["warning_count"] = metadata["warning_count"]
    requested = metadata.get("requested_coverage_ids")
    if isinstance(requested, list):
        evidence["requested_coverage_ids"] = [
            text._safe_text(item, 120)
            for item in requested[:16]
            if text._safe_text(item, 120)
        ]
    sources = safe_sources(metadata.get("sources"))
    if sources is not None:
        evidence["sources"] = sources
    return evidence


__all__ = (
    "MAX_INCIDENTS",
    "TRUTHFUL_INCIDENT_STATUSES",
    "LOOKUP_METADATA_KEYS",
    "safe_incidents",
    "display_incidents",
    "safe_sources",
    "incident_evidence",
)
