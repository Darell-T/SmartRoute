from __future__ import annotations

import asyncio
import os
import time

import httpx

from app.services.mta.config import BASE_URL, route_to_feed

_FETCH_FAILURE_LOGS: dict[str, float] = {}
_FETCH_SUMMARY_LOGS: dict[str, float] = {}
_FETCH_FAILURE_LOG_COOLDOWN = 60
_FETCH_SUMMARY_LOG_COOLDOWN = 15


def _gtfs_realtime_pb2():
    from google.transit import gtfs_realtime_pb2

    return gtfs_realtime_pb2


def _log_fetch_failure(url: str, message: str):
    now = time.monotonic()
    last = _FETCH_FAILURE_LOGS.get(url, 0)
    if now - last < _FETCH_FAILURE_LOG_COOLDOWN:
        return
    _FETCH_FAILURE_LOGS[url] = now
    print(message)


def _feed_url_for_suffix(suffix: str) -> str:
    return f"{BASE_URL}-{suffix}" if suffix else BASE_URL


def _routes_for_suffix(suffix: str, requested_routes: set[str]) -> list[str]:
    return sorted(
        route
        for route, feed_suffix in route_to_feed.items()
        if feed_suffix == suffix and route in requested_routes
    )


def _log_fetch_summary(context: str, message: str):
    if os.getenv("BACKEND_VERBOSE_LOGS", "0") != "1":
        return
    now = time.monotonic()
    last = _FETCH_SUMMARY_LOGS.get(context, 0)
    if now - last < _FETCH_SUMMARY_LOG_COOLDOWN:
        return
    _FETCH_SUMMARY_LOGS[context] = now
    print(message)


def _split_cached_feeds(feed_requests, force_refresh, cache_get):
    results = []
    pending = []
    for feed_request in feed_requests:
        cached = None if force_refresh else cache_get(feed_request["url"], fail_open=True)
        if cached:
            results.append({
                **feed_request,
                "content": cached,
                "bytes": len(cached),
                "from_cache": True,
            })
        else:
            pending.append(feed_request)
    return results, pending


def _append_live_feed(feed_request, response, cache_result, cache_set, results) -> None:
    url = feed_request["url"]
    if isinstance(response, Exception):
        _log_fetch_failure(
            url,
            f"[mta_feed] feed fetch failed for {url}: {type(response).__name__}: {response!r}",
        )
        return
    if response.status_code != 200:
        _log_fetch_failure(
            url,
            f"[mta_feed] feed {url} returned {response.status_code}",
        )
        return
    if cache_result:
        cache_set(url, response.content, 30, fail_open=True)
    results.append({
        **feed_request,
        "content": response.content,
        "bytes": len(response.content),
        "from_cache": False,
    })


async def fetch_feeds_with_metadata(
    routes: list,
    log_context: str | None = None,
    force_refresh: bool = False,
    *,
    cache_result: bool = True,
) -> list[dict]:
    from app.services.cache import cache_get, cache_set

    requested_routes = {str(route).upper() for route in routes}
    unique_suffixes = set()
    for route in routes:
        if route in route_to_feed:
            unique_suffixes.add(route_to_feed[route])

    if not unique_suffixes:
        print("Error: No valid train routes provided.")
        return []

    feed_requests = [
        {
            "suffix": suffix or "numbered",
            "url": _feed_url_for_suffix(suffix),
            "routes": _routes_for_suffix(suffix, requested_routes),
        }
        for suffix in unique_suffixes
    ]

    results, urls_to_fetch = _split_cached_feeds(
        feed_requests, force_refresh, cache_get
    )

    if urls_to_fetch:
        async with httpx.AsyncClient(timeout=10.0) as client:  # noqa: TID251
            tasks = [client.get(feed_request["url"]) for feed_request in urls_to_fetch]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
        for feed_request, response in zip(urls_to_fetch, responses, strict=False):
            _append_live_feed(
                feed_request, response, cache_result, cache_set, results
            )

    if log_context:
        ok = ", ".join(
            f"{item['suffix']}:{item['bytes']}b:{'cache' if item['from_cache'] else 'net'}"
            for item in sorted(results, key=lambda x: x["suffix"])
        ) or "none"
        failed = len(feed_requests) - len(results)
        _log_fetch_summary(
            log_context,
            (
                f"[mta_feed][{log_context}] fetch requested_feeds={len(feed_requests)} "
                f"ok={len(results)} failed={failed} routes={sorted(requested_routes)} feeds={ok}"
            ),
        )

    return results


async def fetch_feeds(routes: list, log_context: str | None = None) -> list:
    results = await fetch_feeds_with_metadata(routes, log_context)
    return [item["content"] for item in results]


def parse_feed_message(raw_bytes: bytes):
    feed = _gtfs_realtime_pb2().FeedMessage()
    feed.ParseFromString(raw_bytes)
    return feed


def parse_bytes(raw_bytes: bytes) -> list:
    user_feed = parse_feed_message(raw_bytes)

    trip_updates = []
    for entity in user_feed.entity:
        if entity.HasField("trip_update"):
            trip = entity.trip_update
            trip_id = trip.trip.trip_id
            route_id = trip.trip.route_id

            for stop in trip.stop_time_update:
                stop_id = stop.stop_id
                if stop_id.endswith("N"):
                    direction = "Uptown"
                elif stop_id.endswith("S"):
                    direction = "Downtown"
                else:
                    direction = None
                trip_updates.append({
                    "route_id": route_id,
                    "trip_id": trip_id,
                    "stop_id": stop_id,
                    "stop_sequence": stop.stop_sequence or None,
                    "arrival_time": stop.arrival.time or None,
                    "delay": stop.arrival.delay,
                    "direction": direction,
                })

    return trip_updates
