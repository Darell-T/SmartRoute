"""Validate and normalize untrusted Grok crowd-search output."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from app.services.trips.crowd_hotspots import HotspotHit

_MAX_EVENTS = 12
_ALLOWED_CATEGORIES = {
    "concert",
    "sports",
    "parade",
    "protest",
    "rally",
    "street_fair",
    "race",
    "convention",
    "theater",
    "civic_event",
    "other",
}
_OFFICIAL_DOMAINS = {
    "nyc.gov",
    "mta.info",
    "lincolncenter.org",
    "msg.com",
    "barclayscenter.com",
    "mlb.com",
    "usta.com",
    "javitscenter.com",
    "timessquarenyc.org",
    "broadway.org",
}
_OFFICIAL_X_HANDLES = {
    "mta",
    "nycemergencymgt",
    "nypdnews",
    "lincolncenter",
    "thegarden",
    "barclayscenter",
    "yankees",
    "mets",
    "javitscenter",
    "timessquarenyc",
}


def parse_json(text: str) -> Mapping[str, Any] | None:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, Mapping) else None


def response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    outputs = getattr(response, "outputs", None)
    if outputs:
        message = getattr(outputs[0], "message", None)
        return str(getattr(message, "content", "") or "")
    return ""


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _source_class(source_ref: str) -> str:
    parsed = urlparse(source_ref)
    host = parsed.netloc.casefold().removeprefix("www.")
    if host in {"x.com", "twitter.com"}:
        handle = next((part.casefold() for part in parsed.path.split("/") if part), "")
        return "official_x" if handle in _OFFICIAL_X_HANDLES else "independent_x"
    if any(host == domain or host.endswith("." + domain) for domain in _OFFICIAL_DOMAINS):
        return "official_web"
    return "independent_web"


def _parse_time(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat() if parsed.tzinfo is not None else None


def normalize_search_payload(
    payload: Mapping[str, Any],
    *,
    areas: Mapping[str, HotspotHit],
    citations: Iterable[str],
    observed_at: datetime,
) -> list[dict[str, Any]]:
    allowed_citations = {
        citation.strip()
        for citation in citations
        if isinstance(citation, str) and citation.startswith(("https://", "http://"))
    }
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_events = payload.get("events")
    for raw in raw_events if isinstance(raw_events, list) else []:
        if not isinstance(raw, Mapping):
            continue
        hotspot_key = _bounded_text(raw.get("hotspot_key"), 64)
        area = areas.get(hotspot_key)
        source_ref = _bounded_text(raw.get("source_ref"), 500)
        title = _bounded_text(raw.get("title"), 140)
        if area is None or source_ref not in allowed_citations or not title:
            continue
        category = _bounded_text(raw.get("category"), 24).casefold()
        if category not in _ALLOWED_CATEGORIES:
            category = "other"
        source_class = _source_class(source_ref)
        venue_name = _bounded_text(raw.get("venue"), 100)
        start_iso = _parse_time(raw.get("start_iso"))
        end_iso = _parse_time(raw.get("end_iso"))
        scoring_authorized = (
            source_class in {"official_web", "official_x"}
            and start_iso is not None
            and bool(venue_name)
        )
        identity = hashlib.sha256(
            f"{hotspot_key}|{title.casefold()}|{start_iso or ''}".encode()
        ).hexdigest()[:20]
        if identity in seen:
            continue
        seen.add(identity)
        events.append(
            {
                "event_id": f"grok:{identity}",
                "source_reference": f"{source_class}:{identity}",
                "name": title,
                "category": category,
                "venue_name": venue_name or area.hotspot_name,
                "venue_latitude": area.latitude,
                "venue_longitude": area.longitude,
                "start_iso": start_iso,
                "estimated_end_iso": end_iso,
                "start_time_status": "confirmed" if start_iso else "unknown",
                "source_class": source_class,
                "verification_tier": (
                    "official"
                    if source_class in {"official_web", "official_x"}
                    else "corroborative"
                ),
                "confidence": {
                    "official_web": 0.85,
                    "official_x": 0.75,
                    "independent_web": 0.55,
                    "independent_x": 0.4,
                }[source_class],
                "scoring_authorized": scoring_authorized,
                "observed_at": observed_at.isoformat(),
                "hotspot_key": hotspot_key,
                # Retained inside the provider boundary for audit/replay only.
                "source_ref": source_ref,
            }
        )
        if len(events) >= _MAX_EVENTS:
            break
    return events
