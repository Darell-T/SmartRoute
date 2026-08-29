from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx

from app.services.mta.config import ALERTS_URL, NYC_TZ
from app.services.mta.feeds import parse_feed_message

_ALERTS_METADATA_KEY = f"{ALERTS_URL}:metadata"
_ALERT_SOURCE = "mta_service_alerts"
_ALERT_ID_LIMIT = 120
_TEXT_LIMIT = 480
_LIST_LIMIT = 24
_SEGMENT_LIMIT = 24
_DIRECTION_SCOPES = frozenset({"both_directions", "direction_specific", "unspecified"})
_PLANNED_STATUSES = frozenset({"planned", "unplanned", "unknown"})
_CHANGE_TYPES = frozenset({
    "express_to_local",
    "suspension",
    "severe_delay",
    "delay",
    "planned_service_change",
    "unknown",
})
_NO_SERVICE_PHRASES = (
    "suspend",
    "no service",
    "not running",
    "does not run",
    "will not run",
)
_LOCAL_OPERATION_PHRASES = (
    "runs local",
    "run local",
    "running local",
    "operates local",
    "operate local",
    "to local",
)
_PLANNED_STATUS_PREFIXES = (
    ("lmm:planned_work", "planned"),
    ("lmm:alert", "unplanned"),
)


def _content_digest(content: object) -> str:
    if isinstance(content, bytes):
        payload = content
    elif isinstance(content, (bytearray, memoryview)):
        payload = bytes(content)
    else:
        payload = str(content).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cached_observed_at(cache_get, content: object) -> str | None:
    try:
        raw = cache_get(_ALERTS_METADATA_KEY, fail_open=True)
        if not raw:
            return None
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        if not isinstance(payload, dict) or payload.get("content_sha256") != _content_digest(content):
            return None
        value = payload.get("observed_at") if isinstance(payload, dict) else None
        return str(value).strip() or None
    except (TypeError, ValueError, UnicodeDecodeError, AttributeError):
        return None


def _alerts_payload(
    content: bytes,
    freshness: str,
    observed_at: str | None,
    with_metadata: bool,
) -> bytes | dict[str, object]:
    if with_metadata:
        return {
            "content": content,
            "freshness": freshness,
            "observed_at": observed_at,
        }
    return content


def _store_alerts_cache(cache_set, content: bytes, observed_at: str) -> None:
    cache_set(ALERTS_URL, content, 60, fail_open=True)
    cache_set(
        _ALERTS_METADATA_KEY,
        json.dumps(
            {
                "observed_at": observed_at,
                "content_sha256": _content_digest(content),
            }
        ),
        60,
        fail_open=True,
    )


async def fetch_service_alerts(
    force_refresh: bool = False,
    *,
    cache_result: bool = True,
    with_metadata: bool = False,
) -> bytes | dict[str, object]:
    from app.services.cache import cache_get, cache_set
    cached = cache_get(ALERTS_URL, fail_open=True)
    cached_observed_at = _cached_observed_at(cache_get, cached)
    if cached and not force_refresh:
        return _alerts_payload(cached, "cached", cached_observed_at, with_metadata)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:  # noqa: TID251
            response = await client.get(ALERTS_URL)
        _require_alerts_http_ok(response)
    except Exception as exc:  # noqa: BLE001 alert-feed faults fall back to cache or empty
        print(f"[mta_feed] alerts feed failed: {type(exc).__name__}: {exc!r}")
        if cached:
            return _alerts_payload(cached, "stale", cached_observed_at, with_metadata)
        return _alerts_payload(b"", "unavailable", None, with_metadata)
    observed_at = datetime.now(UTC).isoformat()
    if cache_result:
        _store_alerts_cache(cache_set, response.content, observed_at)
    return _alerts_payload(response.content, "live", observed_at, with_metadata)


def _require_alerts_http_ok(response) -> None:
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")


def _period_bounds(period) -> tuple[int | None, int | None]:
    start = period.start or None
    end = period.end or None
    return start, end


def _period_is_active(start: int | None, end: int | None, now: float) -> bool:
    if start and now < start:
        return False
    return not (end and end > 0 and now > end)


def _period_is_today_or_unexpired(start: int | None, end: int | None, now: float) -> bool:
    if end and end > 0 and now > end:
        return False
    today = datetime.fromtimestamp(now, tz=NYC_TZ).date()
    today_start = datetime(today.year, today.month, today.day, tzinfo=NYC_TZ).timestamp()
    tomorrow_start = today_start + 24 * 60 * 60
    effective_start = start or today_start
    effective_end = end if end and end > 0 else tomorrow_start
    return effective_start < tomorrow_start and effective_end >= today_start


def _english_text(text_field) -> str:
    if not text_field or not text_field.translation:
        return ""
    fallback = ""
    for translation in text_field.translation:
        if not fallback:
            fallback = translation.text
        if translation.language == "en":
            return translation.text
    return fallback


def is_material_service_alert(alert: object) -> bool:
    return not (isinstance(alert, dict) and alert.get("material_disruption") is False)


def project_service_alert(alert: object) -> dict[str, object] | None:
    if isinstance(alert, str):
        alert = {"header": alert}
    if not isinstance(alert, dict):
        return None

    source = _bounded_text(alert.get("source"), 80) or "unknown"
    source_id = _bounded_text(
        alert.get("source_id") or alert.get("alert_id"), _ALERT_ID_LIMIT
    )
    header = _bounded_text(alert.get("header"), _TEXT_LIMIT)
    description = _bounded_text(alert.get("description"), _TEXT_LIMIT)
    route_ids = _bounded_ids(alert.get("route_ids"), upper=True)
    stop_ids = _bounded_ids(alert.get("stop_ids"))
    direction_ids = _bounded_ids(alert.get("direction_ids"))
    segments = _bounded_segments(
        alert.get("affected_segments") or alert.get("segment_evidence")
    )
    start = alert.get("effective_start", alert.get("start"))
    end = alert.get("effective_end", alert.get("end"))
    direction_scope = _whitelisted_text(
        alert.get("direction_scope"), 32, _DIRECTION_SCOPES, "unspecified"
    )
    planned_status = _whitelisted_text(
        alert.get("planned_status"), 16, _PLANNED_STATUSES, "unknown"
    )
    change_type = _whitelisted_text(
        alert.get("change_type"), 32, _CHANGE_TYPES, "unknown"
    )
    service_operating = _service_operating(alert.get("service_operating"))
    material_disruption = alert.get("material_disruption") is not False
    observed_at = _bounded_text(
        alert.get("feed_observed_at") or alert.get("observed_at"), 64
    ) or None
    last_verified_at = _bounded_text(
        alert.get("local_verified_at") or alert.get("last_verified_at"), 64
    ) or None
    alert_id = source_id
    result: dict[str, object] = {
        "source": source,
        "source_id": source_id,
        "alert_id": alert_id,
        "header": header,
        "description": description,
        "route_ids": route_ids,
        "stop_ids": stop_ids,
        "direction_ids": direction_ids,
        "direction_scope": direction_scope,
        "affected_segments": segments,
        "planned_status": planned_status,
        "change_type": change_type,
        "service_operating": service_operating,
        "material_disruption": material_disruption,
        "start": start,
        "end": end,
        "effective_start": start,
        "effective_end": end,
        "effective_window": {"start": start, "end": end},
        "observed_at": observed_at,
        "last_verified_at": last_verified_at,
        "feed_observed_at": observed_at,
        "local_verified_at": last_verified_at,
    }
    return result


def _bounded_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _whitelisted_text(
    value: object, limit: int, allowed: frozenset[str], default: str
) -> str:
    text = _bounded_text(value, limit)
    if text in allowed:
        return text
    return default


def _bounded_ids(value: object, *, upper: bool = False) -> list[str]:
    values = [value] if isinstance(value, str) else value or []
    result: list[str] = []
    for item in values:
        text = _bounded_text(item, _ALERT_ID_LIMIT)
        if upper:
            text = text.upper()
        if text and text not in result:
            result.append(text)
        if len(result) >= _LIST_LIMIT:
            break
    return result


def _bounded_segment(item: object) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    segment: dict[str, str] = {}
    for key in ("route_id", "stop_id", "direction_id"):
        text = _bounded_text(item.get(key), _ALERT_ID_LIMIT)
        if text:
            segment[key] = text
    if not segment:
        return None
    return segment


def _bounded_segments(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        segment = _bounded_segment(item)
        if segment is None:
            continue
        identity = (
            segment.get("route_id", ""),
            segment.get("stop_id", ""),
            segment.get("direction_id", ""),
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(segment)
        if len(result) >= _SEGMENT_LIMIT:
            break
    return result


def _service_operating(value: object) -> bool | str:
    if value is True or value is False:
        return value
    normalized = _bounded_text(value, 16).casefold()
    return normalized if normalized in {"true", "false", "unknown"} else "unknown"


def _epoch_iso(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _direction_id(informed_entity) -> str | None:
    for field, value in informed_entity.ListFields():
        if field.name == "direction_id":
            return str(value)
    return None


def _change_type_from_text(
    text: str,
    planned_status: str,
    no_service: bool,
    local_operation: bool,
) -> str:
    if local_operation and planned_status == "planned":
        return "express_to_local"
    if local_operation and "express" in text:
        return "express_to_local"
    if no_service:
        return "suspension"
    if "severe" in text and "delay" in text:
        return "severe_delay"
    if "delay" in text:
        return "delay"
    if planned_status == "planned":
        return "planned_service_change"
    return "unknown"


def _alert_semantics(
    source_id: str,
    header: str,
    description: str,
) -> tuple[str, str, bool | str, bool]:
    text = f"{header} {description}".casefold()
    folded = source_id.casefold()
    planned_status = "unknown"
    for prefix, status in _PLANNED_STATUS_PREFIXES:
        if folded.startswith(prefix):
            planned_status = status
            break
    no_service = any(phrase in text for phrase in _NO_SERVICE_PHRASES)
    local_operation = any(phrase in text for phrase in _LOCAL_OPERATION_PHRASES)
    change_type = _change_type_from_text(
        text, planned_status, no_service, local_operation
    )
    if no_service:
        service_operating: bool | str = False
    elif local_operation or "service operates" in text:
        service_operating = True
    else:
        service_operating = "unknown"
    material_disruption = not (
        planned_status == "planned"
        and change_type == "express_to_local"
        and service_operating is True
    )
    return planned_status, change_type, service_operating, material_disruption


def _alert_period(
    alert, include_same_day: bool, now: float
) -> tuple[int | None, int | None] | None:
    if not alert.active_period:
        return (None, None)
    for period in alert.active_period:
        period_start, period_end = _period_bounds(period)
        matches = (
            _period_is_today_or_unexpired(period_start, period_end, now)
            if include_same_day
            else _period_is_active(period_start, period_end, now)
        )
        if matches:
            return _period_bounds(period)
    return None


def _direction_scope_label(direction_ids: set[str]) -> str:
    values = sorted(direction_ids)
    if len(values) > 1:
        return "both_directions"
    if values:
        return "direction_specific"
    return "unspecified"


def _add_informed_entity(
    informed_entity,
    route_ids: set[str],
    stop_ids: set[str],
    direction_ids: set[str],
    segments: list[dict[str, str]],
) -> bool:
    route_id = _bounded_text(informed_entity.route_id, _ALERT_ID_LIMIT)
    stop_id = _bounded_text(informed_entity.stop_id, _ALERT_ID_LIMIT)
    direction_id = _direction_id(informed_entity)
    if route_id:
        route_ids.add(route_id)
    if stop_id:
        stop_ids.add(stop_id)
    if direction_id:
        direction_ids.add(direction_id)
    segment = {
        key: value
        for key, value in (
            ("route_id", route_id),
            ("stop_id", stop_id),
            ("direction_id", direction_id or ""),
        )
        if value
    }
    if segment and segment not in segments:
        segments.append(segment)
        return len(segments) >= _SEGMENT_LIMIT
    return False


def _informed_scope(alert) -> tuple[list[str], list[str], list[str], list[dict[str, str]], str]:
    route_ids: set[str] = set()
    stop_ids: set[str] = set()
    direction_ids: set[str] = set()
    segments: list[dict[str, str]] = []
    for informed_entity in alert.informed_entity:
        if _add_informed_entity(
            informed_entity, route_ids, stop_ids, direction_ids, segments
        ):
            break
    return (
        sorted(route_ids),
        sorted(stop_ids),
        sorted(direction_ids),
        segments,
        _direction_scope_label(direction_ids),
    )


def _parse_service_alerts(
    raw_bytes: bytes,
    *,
    include_same_day: bool,
    now_timestamp: float | None = None,
) -> list:
    feed = parse_feed_message(raw_bytes)
    now = now_timestamp if now_timestamp is not None else datetime.now(tz=NYC_TZ).timestamp()
    feed_observed_at = _epoch_iso(feed.header.timestamp or None)
    local_verified_at = _epoch_iso(now)
    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        period = _alert_period(entity.alert, include_same_day, now)
        if period is None:
            continue
        start, end = period
        header = _bounded_text(_english_text(entity.alert.header_text), _TEXT_LIMIT)
        description = _bounded_text(
            _english_text(entity.alert.description_text), _TEXT_LIMIT
        )
        source_id = _bounded_text(entity.id, _ALERT_ID_LIMIT)
        planned_status, change_type, service_operating, material_disruption = (
            _alert_semantics(source_id, header, description)
        )
        route_ids, stop_ids, direction_values, segments, direction_scope = (
            _informed_scope(entity.alert)
        )
        alerts.append(
            {
                "source": _ALERT_SOURCE,
                "source_id": source_id,
                "alert_id": source_id,
                "header": header,
                "description": description,
                "route_ids": route_ids,
                "stop_ids": stop_ids,
                "direction_ids": direction_values,
                "direction_scope": direction_scope,
                "affected_segments": segments,
                "planned_status": planned_status,
                "change_type": change_type,
                "service_operating": service_operating,
                "material_disruption": material_disruption,
                "start": start,
                "end": end,
                "effective_start": start,
                "effective_end": end,
                "effective_window": {"start": start, "end": end},
                "observed_at": feed_observed_at,
                "last_verified_at": local_verified_at,
                "feed_observed_at": feed_observed_at,
                "local_verified_at": local_verified_at,
            }
        )
    return alerts


def parse_service_alerts(raw_bytes: bytes) -> list:
    return _parse_service_alerts(raw_bytes, include_same_day=False)


def parse_service_alerts_for_service_board(raw_bytes: bytes) -> list:
    return _parse_service_alerts(raw_bytes, include_same_day=True)


def filter_alerts_for_routes(alerts: list, route_ids: set) -> list:
    wanted = {str(route_id).strip().upper() for route_id in route_ids}
    return [
        alert
        for alert in alerts
        if isinstance(alert, dict)
        and {
            str(route_id).strip().upper()
            for route_id in alert.get("route_ids") or []
        }
        & wanted
    ]
