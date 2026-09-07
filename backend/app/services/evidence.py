"""Small, shared freshness contract for model- and scoring-facing evidence."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

EvidenceStatus = Literal["current", "stale", "unavailable"]


def parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


@dataclasses.dataclass(frozen=True)
class EvidenceEnvelope[T]:
    source: str
    observed_at: datetime
    payload: T
    valid_until: datetime | None = None
    available: bool = True

    def status_at(self, now: datetime | None = None) -> EvidenceStatus:
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        if not self.available:
            return "unavailable"
        if self.valid_until is not None and current_time > self.valid_until:
            return "stale"
        return "current"

    def current_payload(self, now: datetime | None = None) -> T | None:
        return self.payload if self.status_at(now) == "current" else None

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "observedAt": self.observed_at.isoformat(),
            "status": self.status_at(now),
            "payload": self.payload,
        }
        if self.valid_until is not None:
            result["validUntil"] = self.valid_until.isoformat()
        return result

    def to_model_dict(self, *, empty: T, now: datetime | None = None) -> dict[str, Any]:
        """Serialize provenance while removing expired/unavailable payloads."""

        result = self.to_dict(now)
        if result["status"] != "current":
            result["payload"] = empty
        return result


def evidence_envelope[T](
    source: str,
    payload: T,
    *,
    observed_at: object = None,
    ttl_seconds: int | float | None = None,
    valid_until: object = None,
    available: bool = True,
) -> EvidenceEnvelope[T]:
    observed = parse_timestamp(observed_at) or datetime.now(UTC)
    expires = parse_timestamp(valid_until)
    if expires is None and ttl_seconds is not None:
        expires = observed + timedelta(seconds=max(0, float(ttl_seconds)))
    return EvidenceEnvelope(
        source=str(source)[:80],
        observed_at=observed,
        valid_until=expires,
        payload=payload,
        available=available,
    )


def current_payload[T](envelope: EvidenceEnvelope[T], *, now: datetime | None = None, empty: T) -> T:
    """Return only current evidence while retaining its envelope for audit."""

    payload = envelope.current_payload(now)
    return payload if payload is not None else empty
