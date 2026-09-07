"""Pure parsing and evidence normalization for the background incident scout.

Owns no transport or policy: untrusted model JSON plus exact citations become
bounded, deterministic claim/evidence records and canonical incident-index
inputs. Citation identity rules are reused from the incidents evidence module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.incidents.evidence import (
    canonical_citation_url,
    source_identity_from_url,
    source_type_matches_url,
)
from app.services.incidents.normalization import (
    bounded_ids,
    bounded_text,
    sanitize_source_records,
)

SIX_HOURS = timedelta(hours=6)
FUTURE_SKEW = timedelta(minutes=10)
ALLOWED_SEVERITIES = frozenset({"low", "medium", "high"})
ALLOWED_SCOPES = frozenset(
    {"nearby", "station_access", "subway_operations", "bus_corridor", "walking"}
)
_MAX_CLAIMS = 12
_UNCONFIRMED_TTL_S = 30 * 60
_CONFIRMED_TTL_S = 6 * 3600
_X_POST_ID_DIGEST_LEN = 24
_ACCOUNT_BOUND = 48


def per_post_source_id(canonical_url: str) -> str | None:
    """Stable bounded per-post identity from a canonical citation URL.

    Non-X URLs keep the shared domain identity; X posts use a bounded account
    prefix plus a digest of the full canonical URL, so distinct posts from one
    account never collide and no path length is ever exposed.
    """
    canonical = canonical_citation_url(canonical_url)
    identity = source_identity_from_url(canonical)
    if identity is None or not identity.startswith("x:"):
        return identity
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    account = bounded_text(identity, _ACCOUNT_BOUND)
    return f"{account}:{digest[:_X_POST_ID_DIGEST_LEN]}"


def claim_ref_for(source_id: str) -> str:
    """Opaque stable claim reference shared by the X and Web phases."""
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]
    return f"cr_{digest}"


def observed_at_iso(value: object, *, now: datetime) -> str | None:
    """Offset-aware timestamp inside [now-6h, now+10min], returned as Z ISO."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    observed = parsed.astimezone(UTC)
    if observed < now - SIX_HOURS or observed > now + FUTURE_SKEW:
        return None
    return observed.isoformat().replace("+00:00", "Z")


def is_valid_x_payload(payload: object) -> bool:
    """X is a valid JSON contract only when ``incidents`` is a list."""
    return isinstance(payload, Mapping) and isinstance(payload.get("incidents"), list)


def is_valid_web_payload(payload: object) -> bool:
    """Web is a valid JSON contract only when ``corroborations`` is a list."""
    return isinstance(payload, Mapping) and isinstance(payload.get("corroborations"), list)


def _accepted_x_claim(raw: object, *, allowed: set[str], now: datetime) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    location = bounded_text(raw.get("location"), 120)
    description = bounded_text(raw.get("description"), 280)
    severity = bounded_text(raw.get("severity"), 16).casefold()
    scope = bounded_text(raw.get("impact_scope"), 40).casefold()
    source_url = canonical_citation_url(raw.get("source_url"))
    source_id = per_post_source_id(source_url) if source_url is not None else None
    observed_at = observed_at_iso(raw.get("observed_at"), now=now)
    if (
        not location or not description
        or severity not in ALLOWED_SEVERITIES
        or scope not in ALLOWED_SCOPES
        or source_url is None or source_url not in allowed
        or not source_type_matches_url("x_search", source_url)
        or observed_at is None or source_id is None
    ):
        return None
    return {
        "claim_ref": claim_ref_for(source_id),
        "location": location, "description": description,
        "severity": severity, "impact_scope": scope,
        "route_ids": bounded_ids(raw.get("route_ids"), 24, upper=True),
        "stop_ids": bounded_ids(raw.get("stop_ids"), 24),
        "corridor_ids": bounded_ids(raw.get("corridor_ids"), 12),
        "source_url": source_url, "source_id": source_id,
        "observed_at": observed_at,
    }


def normalize_x_claims(
    payload: Mapping[str, Any], *, citations: Iterable[str], now: datetime
) -> list[dict[str, Any]]:
    """Accept only current, cited X claims; dedupe by stable post identity."""
    allowed = {
        canonical for canonical in (canonical_citation_url(c) for c in citations) if canonical
    }
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in payload.get("incidents") or []:
        claim = _accepted_x_claim(raw, allowed=allowed, now=now)
        if claim is None or claim["source_id"] in seen:
            continue
        seen.add(claim["source_id"])
        claims.append(claim)
        if len(claims) >= _MAX_CLAIMS:
            break
    return claims


def _accepted_web_evidence(
    raw: object, *, claims_by_ref: Mapping[str, Any], allowed: set[str], now: datetime
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    claim_ref = bounded_text(raw.get("claim_ref"), 40)
    source_url = canonical_citation_url(raw.get("source_url"))
    source_id = source_identity_from_url(source_url) if source_url is not None else None
    observed_at = observed_at_iso(raw.get("observed_at"), now=now)
    if (
        claims_by_ref.get(claim_ref) is None
        or source_url is None or source_url not in allowed
        or not source_type_matches_url("web_search", source_url)
        or observed_at is None or source_id is None
    ):
        return None
    return {
        "claim_ref": claim_ref,
        "source_url": source_url,
        "source_id": source_id,
        "observed_at": observed_at,
    }


def normalize_web_corroborations(
    payload: Mapping[str, Any],
    *,
    claims_by_ref: Mapping[str, Any],
    citations: Iterable[str],
    now: datetime,
) -> list[dict[str, Any]]:
    """Independent web evidence for known claims; same domain counts once."""
    allowed = {
        canonical for canonical in (canonical_citation_url(c) for c in citations) if canonical
    }
    corroborations: list[dict[str, Any]] = []
    seen_per_claim: dict[str, set[str]] = {}
    for raw in payload.get("corroborations") or []:
        item = _accepted_web_evidence(
            raw, claims_by_ref=claims_by_ref, allowed=allowed, now=now
        )
        if item is None:
            continue
        seen = seen_per_claim.setdefault(item["claim_ref"], set())
        if item["source_id"] in seen or len(seen) >= 4:
            continue
        seen.add(item["source_id"])
        corroborations.append(item)
        if len(corroborations) >= 24:
            break
    return corroborations


def _source_record(source: str, item: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source": source,
        "source_id": str(item.get("source_id", "")),
        "source_url": str(item.get("source_url", "")),
        "observed_at": str(item.get("observed_at", "")),
    }


def build_incident_inputs(
    claims: Sequence[Mapping[str, Any]],
    corroborations: Iterable[Mapping[str, Any]],
    *,
    batch_id: str,
    now: datetime,
) -> tuple[dict[str, Any], ...]:
    """Canonical incident-index inputs; X-only stays unconfirmed and short-lived.

    Every incident carries the stable top-level X source identity, so the
    incident-index hash never changes when Web corroboration is added or
    removed; emitted provenance is bounded by sanitize_source_records.
    """
    by_ref: dict[str, list[Mapping[str, Any]]] = {}
    for corroboration in corroborations:
        by_ref.setdefault(str(corroboration.get("claim_ref", "")), []).append(corroboration)
    incidents: list[dict[str, Any]] = []
    for claim in claims:
        supporting = by_ref.get(str(claim.get("claim_ref", "")), ())
        confirmed = bool(supporting)
        source_records = [_source_record("x_search", claim)]
        if confirmed:
            source_records.extend(_source_record("web_search", item) for item in supporting)
        incidents.append(
            {
                "state": "confirmed" if confirmed else "unconfirmed",
                "source": "x_search",
                "source_id": str(claim.get("source_id", "")),
                "location_name": claim.get("location", ""),
                "description": claim.get("description", ""),
                "severity": claim.get("severity", ""),
                "impact_scope": claim.get("impact_scope", ""),
                "observed_at": claim.get("observed_at", ""),
                "expires_at": now.timestamp()
                + (_CONFIRMED_TTL_S if confirmed else _UNCONFIRMED_TTL_S),
                "source_coverage": ["x_search", "web_search"] if confirmed else ["x_search"],
                "corroboration_state": "corroborated" if confirmed else "uncorroborated",
                "advisor_eligible": bool(confirmed and claim.get("impact_scope") != "nearby"),
                "source_records": sanitize_source_records(source_records),
                "affected_stop_ids": list(claim.get("stop_ids", [])),
                "affected_route_ids": list(claim.get("route_ids", [])),
                "affected_corridor_ids": list(claim.get("corridor_ids", [])),
                "affected_batch_ids": [batch_id],
            }
        )
    return tuple(incidents)
