"""Route, direction, concern, and coverage matching for transit evidence."""

from __future__ import annotations

from typing import Any


def normalized_text(value: object) -> str:
    return " ".join(str(value or "").split())


def unique_evidence_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))[:12]


def normalized_route_ids(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else []
    normalized = []
    for item in values:
        route = normalized_text(item).upper()
        if route:
            normalized.append(route)
    return unique_evidence_values(normalized)


_TYPED_CONCERN_FIELDS = ("kind", "cause", "category", "incident_type")
_TYPED_CONCERN_ALIASES = {
    "stalled_train": frozenset(
        {
            "stalled",
            "stalled train",
            "possible stalled train",
            "disabled train",
            "stopped train",
        }
    ),
    "delay": frozenset({"delay", "delayed", "service delay", "slower", "holding"}),
}
_PROVIDER_TEXT_CONCERN_PHRASES = {
    "stalled_train": ("stalled train", "stalled", "disabled train", "train stopped"),
    "delay": ("delay", "delayed"),
}


def route_match(row: dict[str, Any], requested_routes: list[str]) -> bool:
    if not requested_routes:
        return True
    return bool(set(normalized_route_ids(row.get("route_ids"))) & set(requested_routes))


def concern_match(row: dict[str, Any], concerns: list[str]) -> bool:
    if not concerns:
        return True
    typed_values = _typed_concern_values(row)
    if typed_values:
        return any(
            _typed_concern_matches(concern, typed_values) for concern in concerns
        )
    provider_text = " ".join(
        _provider_scalar_text(row.get(key))
        for key in ("header", "description", "title")
    ).casefold()[:1200]
    return any(
        phrase in provider_text
        for concern in concerns
        for phrase in _PROVIDER_TEXT_CONCERN_PHRASES.get(concern, ())
    )


def _typed_concern_values(row: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in _TYPED_CONCERN_FIELDS:
        raw = row.get(key)
        candidates = raw if isinstance(raw, (list, tuple)) else (raw,)
        for candidate in candidates:
            if isinstance(candidate, str):
                normalized = _concern_tag(candidate)
                if normalized:
                    values.add(normalized)
    return values


def _typed_concern_matches(concern: str, values: set[str]) -> bool:
    normalized = _concern_tag(concern)
    aliases = _TYPED_CONCERN_ALIASES.get(concern)
    if aliases:
        return bool(values & aliases)
    return normalized in values


def _concern_tag(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("_", " ").replace("-", " ").split()).casefold()


def _provider_scalar_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return normalized_text(value)[:400]


def confirmed(row: dict[str, Any], source: str) -> bool:
    state = normalized_text(row.get("state") or row.get("confirmation")).casefold()
    if state:
        return state not in {"unconfirmed", "possible", "suspected"}
    return source in {"", "mta_alerts", "mta_service_alerts"}


def coverage(row: dict[str, Any]) -> str:
    raw_value = row.get("freshness") or row.get("source_status")
    if isinstance(raw_value, dict):
        raw_value = raw_value.get("status") or raw_value.get("state")
    value = normalized_text(raw_value).casefold()
    if value in {"live", "current", "scheduled"}:
        return "current"
    if value in {
        "partial",
        "stale",
        "unavailable",
        "provider_unavailable",
        "unknown",
        "unscanned",
    }:
        return "unavailable" if value == "provider_unavailable" else value
    return "unknown"


def arrival_coverage(row: dict[str, Any]) -> str:
    envelope = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    return coverage({"freshness": envelope.get("status") or row.get("source_status")})


def concerns(value: object) -> list[str]:
    if isinstance(value, str):
        values = value.split("+")
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        values = []
    normalized = []
    for item in values:
        concern = normalized_text(item).casefold().replace(" ", "_")
        if concern:
            normalized.append(concern)
    return unique_evidence_values(normalized)
