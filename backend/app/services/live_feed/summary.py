"""Live network-health summary.

Derives a network_status (healthy / caution / disrupted) from the active service
alerts + system-wide vehicle telemetry, then narrates it via ATLAS
(``generate_live_network_summary``), caching the narration by a content hash so
identical network states reuse the same prose. ``_build_live_network_summary_bundle``
is the orchestrator the snapshot calls; it returns (summary, signals).
"""

import hashlib
import json

from app.services import mta_feed
from app.services.ai_advisor import generate_live_network_summary
from app.utils.cache import cache_get, cache_set

_LIVE_SUMMARY_CACHE_TTL = 3600
_SUMMARY_DISRUPTION_KEYWORDS = (
    "SUSPEND",
    "NO ",
    "SKIP",
    "BYPASS",
    "REROUT",
    "SLOW SPEED",
    "MAJOR DELAY",
    "PART SUSPEND",
)
_SUMMARY_CAUTION_KEYWORDS = (
    "DELAY",
    "SERVICE CHANGE",
    "PLANNED WORK",
    "LOCAL TO EXPRESS",
    "EXPRESS TO LOCAL",
    "SHUTTLE",
)


def _summary_text(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def _alert_summary_severity(alert: dict) -> str:
    text = f"{_summary_text(alert.get('header'))} {_summary_text(alert.get('description'))}".upper()
    if any(keyword in text for keyword in _SUMMARY_DISRUPTION_KEYWORDS):
        return "disrupted"
    if any(keyword in text for keyword in _SUMMARY_CAUTION_KEYWORDS):
        return "caution"
    return "caution"


def _derive_live_summary_status(
    active_alert_count: int,
    major_alert_count: int,
    stale_count: int,
    feed_failures: int,
    vehicle_entities: int,
    vehicles_without_position: int,
) -> str:
    no_position_ratio = (
        vehicles_without_position / vehicle_entities if vehicle_entities > 0 else 0
    )
    if (
        major_alert_count >= 2
        or active_alert_count >= 8
        or stale_count >= 20
        or (feed_failures > 0 and no_position_ratio >= 0.5)
        or no_position_ratio >= 0.85
    ):
        return "disrupted"
    if (
        active_alert_count > 0
        or stale_count > 0
        or feed_failures > 0
        or no_position_ratio >= 0.35
    ):
        return "caution"
    return "healthy"


def _build_live_summary_package(parsed_alerts: list[dict], network_vehicles: list[dict], vehicle_debug: dict) -> dict:
    affected_routes: set[str] = set()
    top_alerts = []
    major_alert_count = 0
    caution_alert_count = 0

    for alert in parsed_alerts:
        routes = sorted(
            {
                str(route_id).strip().upper()
                for route_id in (alert.get("route_ids") or [])
                if str(route_id).strip()
            }
        )
        affected_routes.update(routes)
        severity = _alert_summary_severity(alert)
        if severity == "disrupted":
            major_alert_count += 1
        else:
            caution_alert_count += 1
        header = _summary_text(alert.get("header"))
        if header:
            top_alerts.append(
                {
                    "severity": severity,
                    "header": header[:220],
                    "routes": routes[:8],
                }
            )

    severity_rank = {"disrupted": 0, "caution": 1}
    top_alerts.sort(
        key=lambda item: (
            severity_rank.get(item["severity"], 9),
            -len(item["routes"]),
            item["header"],
        )
    )

    stale_vehicles = [vehicle for vehicle in network_vehicles if vehicle.get("stale")]
    vehicle_entities = int(vehicle_debug.get("vehicle_entities") or 0)
    vehicles_without_position = int(vehicle_debug.get("vehicles_without_position") or 0)
    status = _derive_live_summary_status(
        active_alert_count=len(parsed_alerts),
        major_alert_count=major_alert_count,
        stale_count=len(stale_vehicles),
        feed_failures=int(vehicle_debug.get("feed_failures") or 0),
        vehicle_entities=vehicle_entities,
        vehicles_without_position=vehicles_without_position,
    )

    return {
        "network_status": status,
        "alerts": {
            "active_count": len(parsed_alerts),
            "major_count": major_alert_count,
            "caution_count": caution_alert_count,
            "affected_route_count": len(affected_routes),
            "affected_routes": sorted(affected_routes),
            "top_alerts": top_alerts[:3],
        },
        "vehicles": {
            "tracked_count": len(network_vehicles),
            "stale_count": len(stale_vehicles),
            "stale_routes": sorted({str(vehicle.get("route_id") or "").upper() for vehicle in stale_vehicles if vehicle.get("route_id")}),
            "routes_reporting": sorted({str(vehicle.get("route_id") or "").upper() for vehicle in network_vehicles if vehicle.get("route_id")}),
            "feeds_ok": int(vehicle_debug.get("feeds_ok") or 0),
            "feed_failures": int(vehicle_debug.get("feed_failures") or 0),
            "vehicle_entities": vehicle_entities,
            "vehicles_with_position": int(vehicle_debug.get("vehicles_with_position") or 0),
            "vehicles_without_position": vehicles_without_position,
            "stop_only_candidates": int(vehicle_debug.get("stop_only_candidates") or 0),
            "final_markers": int(vehicle_debug.get("final_markers") or 0),
        },
    }


def _load_cached_live_summary(cache_key: str) -> dict | None:
    cached = cache_get(cache_key)
    if not cached:
        return None
    if isinstance(cached, bytes):
        cached = cached.decode("utf-8")
    try:
        payload = json.loads(cached)
        if not isinstance(payload, dict):
            return None
        if payload.get("source") != "fallback":
            payload["source"] = "cached"
        return payload
    except json.JSONDecodeError:
        return None


def _build_live_signals(package: dict, updated_at: int) -> dict:
    alerts = package.get("alerts", {}) if isinstance(package, dict) else {}
    vehicles = package.get("vehicles", {}) if isinstance(package, dict) else {}
    return {
        "network_status": package.get("network_status") or "caution",
        "active_alert_count": int(alerts.get("active_count") or 0),
        "major_alert_count": int(alerts.get("major_count") or 0),
        "affected_route_count": int(alerts.get("affected_route_count") or 0),
        "tracked_vehicle_count": int(vehicles.get("tracked_count") or 0),
        "stale_vehicle_count": int(vehicles.get("stale_count") or 0),
        "routes_reporting_count": len(vehicles.get("routes_reporting") or []),
        "feed_failures": int(vehicles.get("feed_failures") or 0),
        "vehicles_with_position": int(vehicles.get("vehicles_with_position") or 0),
        "vehicles_without_position": int(vehicles.get("vehicles_without_position") or 0),
        "updated_at": updated_at,
    }


async def _build_live_network_summary_bundle(parsed_alerts: list[dict], updated_at: int) -> tuple[dict, dict]:
    network_vehicles, network_debug = await mta_feed.get_all_subway_vehicle_positions(
        None,
        debug=True,
        include_stop_only=True,
    )
    package = _build_live_summary_package(parsed_alerts, network_vehicles, network_debug)
    serialized = json.dumps(package, sort_keys=True, separators=(",", ":"))
    summary_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    cache_key = f"live_summary:{summary_hash}"

    cached = _load_cached_live_summary(cache_key)
    if cached:
        return cached, _build_live_signals(package, updated_at)

    summary = await generate_live_network_summary(package)
    cache_set(cache_key, json.dumps(summary), _LIVE_SUMMARY_CACHE_TTL)
    return summary, _build_live_signals(package, updated_at)
