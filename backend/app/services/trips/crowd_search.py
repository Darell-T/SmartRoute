"""Five-minute cache boundary for bounded Grok crowd research."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.services.trips.crowd_hotspots import HotspotHit
from app.services.trips.crowd_search_normalization import normalize_search_payload
from app.services.trips.crowd_search_provider import (
    PROMPT as _PROMPT,
    run_search as _run_search,
)
from app.utils import cache

_CACHE_TTL_S = 300
_CACHE_PREFIX = "agent:crowd-search:"
_NYC_TZ = ZoneInfo("America/New_York")


def _cache_key(areas: Mapping[str, HotspotHit], travel_at: datetime) -> str:
    bucket = travel_at.astimezone(_NYC_TZ).replace(
        minute=(travel_at.minute // 30) * 30,
        second=0,
        microsecond=0,
    )
    material = "|".join((*sorted(areas), bucket.isoformat()))
    return _CACHE_PREFIX + hashlib.sha256(material.encode()).hexdigest()


async def search_hotspots(
    hits: Iterable[HotspotHit],
    *,
    travel_at: datetime,
    allow_live_search: bool,
) -> dict[str, Any]:
    areas = {hit.hotspot_key: hit for hit in hits}
    if not areas:
        return {"status": "not_required", "events": [], "completed_sources": []}
    key = _cache_key(areas, travel_at)
    cached = cache.cache_get(key)
    if cached:
        try:
            value = json.loads(cached)
        except (TypeError, json.JSONDecodeError):
            value = None
        if isinstance(value, dict):
            return {**value, "cache_hit": True}
    if not allow_live_search:
        return {"status": "not_required", "events": [], "completed_sources": []}
    result = await asyncio.to_thread(_run_search, areas, travel_at)
    if result.get("status") in {"complete", "partial"}:
        cache.cache_set(key, json.dumps(result, separators=(",", ":")), _CACHE_TTL_S)
    return {**result, "cache_hit": False}
