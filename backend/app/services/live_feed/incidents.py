"""ATLAS nearby incident scan (toggle-gated).

When the rider turns ATLAS scan on, we look for incidents within the same
half-mile radius as nearby transit. Grok + X-search is slow (5-30s), so the
scan runs as a single-flight BACKGROUND task and each snapshot serves the most
recent cached result. Keyed to a coarse location bucket so moving to a new
area forces a fresh scan instead of showing the old neighborhood's incidents.
"""

import asyncio
import time

from app.services.incident_monitor import get_incidents
from app.services.live_feed.log import _vlog

_NEARBY_INCIDENTS: list[dict] = []
_NEARBY_SCAN_BUCKET: tuple | None = None
_NEARBY_SCAN_TS = 0.0
_NEARBY_SCAN_INFLIGHT = False
_NEARBY_SCAN_TASKS: set = set()
_NEARBY_SCAN_TTL_S = 120.0

_INCIDENT_TYPE_KEYWORDS = [
    ("fire", "fire"), ("smoke", "fire"),
    ("stab", "stabbing"), ("shot", "weapon"), ("shoot", "weapon"),
    ("gun", "weapon"), ("weapon", "weapon"),
    ("assault", "assault"), ("fight", "assault"), ("robb", "police"),
    ("police", "police"), ("nypd", "police"), ("arrest", "police"),
    ("medical", "medical"), ("injur", "medical"), ("ems", "medical"), ("sick", "medical"),
    ("flood", "hazard"), ("hazard", "hazard"), ("track fire", "hazard"), ("debris", "hazard"),
]


def _incident_type(text: str) -> str:
    low = (text or "").lower()
    for needle, kind in _INCIDENT_TYPE_KEYWORDS:
        if needle in low:
            return kind
    return "incident"


def _loc_bucket(lat: float, lng: float) -> tuple:
    # ~0.7 mi cells -- coarse enough that small GPS jitter reuses the cache.
    return (round(lat, 2), round(lng, 2))


def _nearby_incident_markers(incidents: list, meta_by_name: dict) -> list[dict]:
    """Convert raw Grok incidents to the frontend LiveFeedIncident shape, placing
    each at its station's coordinates. Incidents whose station is not among the
    scanned nearby stops are dropped (no coordinate to anchor the marker)."""
    markers = []
    now = int(time.time())
    for index, inc in enumerate(incidents or []):
        if not isinstance(inc, dict):
            continue
        meta = meta_by_name.get(inc.get("nearby_station"))
        if not meta or meta["lat"] is None or meta["lng"] is None:
            continue
        description = inc.get("description") or "Incident reported nearby."
        detail = " - ".join(p for p in (inc.get("location"), inc.get("source")) if p)
        markers.append({
            "id": f"nearby-incident-{index}",
            "type": _incident_type(f"{description} {inc.get('location', '')}"),
            "lat": meta["lat"],
            "lng": meta["lng"],
            "title": description,
            "detail": detail,
            "severity": inc.get("severity") or "medium",
            "source": inc.get("source"),
            "station": inc.get("nearby_station"),
            "routeIds": meta.get("routes", []),
            "updated_at": now,
        })
    return markers


async def _refresh_nearby_incidents_bg(station_names: list[str], meta_by_name: dict, bucket: tuple):
    global _NEARBY_SCAN_INFLIGHT, _NEARBY_INCIDENTS, _NEARBY_SCAN_TS, _NEARBY_SCAN_BUCKET
    try:
        result = await get_incidents(station_names)
        incidents = result.get("incidents", []) if isinstance(result, dict) else []
        _NEARBY_INCIDENTS = _nearby_incident_markers(incidents, meta_by_name)
        _NEARBY_SCAN_TS = time.monotonic()
        _NEARBY_SCAN_BUCKET = bucket
        _vlog(f"[atlas_scan] nearby incidents: {len(_NEARBY_INCIDENTS)} near {len(station_names)} stops")
    except Exception as exc:
        print(f"[atlas_scan] nearby incident scan failed: {exc!r}")
    finally:
        _NEARBY_SCAN_INFLIGHT = False


def _serve_nearby_incidents(enriched_stops: list, lat: float, lng: float) -> list[dict]:
    """Cached, single-flight half-mile incident scan around the rider. Serves the
    last result immediately and refreshes in the background when stale or when
    the rider has moved to a new area -- never blocks the snapshot on Grok."""
    global _NEARBY_SCAN_INFLIGHT
    meta_by_name: dict[str, dict] = {}
    for stop in enriched_stops or []:
        name = stop.get("stop_name")
        if name and name not in meta_by_name:
            meta_by_name[name] = {
                "lat": stop.get("stop_lat"),
                "lng": stop.get("stop_lon"),
                "routes": list(stop.get("route_ids", [])),
            }
    if not meta_by_name:
        return list(_NEARBY_INCIDENTS)

    bucket = _loc_bucket(lat, lng)
    moved = bucket != _NEARBY_SCAN_BUCKET
    stale = (time.monotonic() - _NEARBY_SCAN_TS) > _NEARBY_SCAN_TTL_S
    if not _NEARBY_SCAN_INFLIGHT and (moved or stale):
        _NEARBY_SCAN_INFLIGHT = True
        try:
            task = asyncio.create_task(
                _refresh_nearby_incidents_bg(list(meta_by_name.keys()), meta_by_name, bucket)
            )
            _NEARBY_SCAN_TASKS.add(task)
            task.add_done_callback(_NEARBY_SCAN_TASKS.discard)
        except Exception:
            _NEARBY_SCAN_INFLIGHT = False
    # While the rider has just moved to a new bucket, the cached incidents belong
    # to the old area -- withhold them until the refresh for this bucket lands.
    return [] if moved else list(_NEARBY_INCIDENTS)
