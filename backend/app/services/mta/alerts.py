from __future__ import annotations

from datetime import datetime

from app.services.mta.config import ALERTS_URL, NYC_TZ
from app.services.mta.feeds import parse_feed_message


async def fetch_service_alerts(force_refresh: bool = False) -> bytes:
    from app.utils.cache import cache_get, cache_set

    cached = None if force_refresh else cache_get(ALERTS_URL)
    if cached:
        return cached

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(ALERTS_URL)
        if response.status_code != 200:
            print(f"[mta_feed] alerts feed returned {response.status_code}")
            return b""
        cache_set(ALERTS_URL, response.content, 60)
        return response.content
    except Exception as exc:
        print(f"[mta_feed] alerts feed failed: {type(exc).__name__}: {exc!r}")
        return b""


def _period_bounds(period) -> tuple[int | None, int | None]:
    start = period.start if period.start else None
    end = period.end if period.end else None
    return start, end


def _period_is_active(start: int | None, end: int | None, now: float) -> bool:
    if start and now < start:
        return False
    if end and end > 0 and now > end:
        return False
    return True


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


def _parse_service_alerts(
    rawBytes: bytes,
    *,
    include_same_day: bool,
    now_timestamp: float | None = None,
) -> list:
    feed = parse_feed_message(rawBytes)
    now = now_timestamp if now_timestamp is not None else datetime.now(tz=NYC_TZ).timestamp()
    alerts = []

    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue

        alert = entity.alert
        start = None
        end = None
        if alert.active_period:
            matching_period = None
            for period in alert.active_period:
                period_start, period_end = _period_bounds(period)
                matches = (
                    _period_is_today_or_unexpired(period_start, period_end, now)
                    if include_same_day
                    else _period_is_active(period_start, period_end, now)
                )
                if matches:
                    matching_period = period
                    break

            if matching_period is None:
                continue

            start, end = _period_bounds(matching_period)

        route_ids = set()
        stop_ids = set()
        for informed_entity in alert.informed_entity:
            if informed_entity.route_id:
                route_ids.add(informed_entity.route_id)
            if informed_entity.stop_id:
                stop_ids.add(informed_entity.stop_id)

        alerts.append({
            "alert_id": entity.id,
            "header": _english_text(alert.header_text),
            "description": _english_text(alert.description_text),
            "route_ids": list(route_ids),
            "stop_ids": list(stop_ids),
            "start": start,
            "end": end,
        })

    return alerts


def parse_service_alerts(rawBytes: bytes) -> list:
    return _parse_service_alerts(rawBytes, include_same_day=False)


def parse_service_alerts_for_service_board(rawBytes: bytes) -> list:
    return _parse_service_alerts(rawBytes, include_same_day=True)


def filter_alerts_for_routes(alerts: list, route_ids: set) -> list:
    return [alert for alert in alerts if set(alert["route_ids"]) & route_ids]
