"""Focused collection/status/provider-boundary tests for the bounded official
incident-source snapshot adapter.

All tests are deterministic: they inject callables and recorded provider-
shaped values (or output-independent empty payloads) and make zero network
or live-provider calls. Pure alert/stalled normalization, timing, bounds,
deterministic identity/dedupe, and raw-payload-shape coverage live in the
companion official-normalization test module.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.services.incidents.official import (
    SOURCE_ALERTS,
    SOURCE_GTFS_RT,
    STATUS_CURRENT,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    collect_official_incidents,
)
from app.services.mta.config import ALL_SUBWAY_ROUTES, route_to_feed

try:
    from google.transit import gtfs_realtime_pb2
except ModuleNotFoundError:
    gtfs_realtime_pb2 = None

FIXED_NOW = 1_800_000_000.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _new_feed() -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_778_595_600
    return feed


def _alert_feed(entities: list[dict]) -> gtfs_realtime_pb2.FeedMessage:
    feed = _new_feed()
    for item in entities:
        entity = feed.entity.add()
        entity.id = item["id"]
        alert = entity.alert
        if item.get("start") is not None or item.get("end") is not None:
            period = alert.active_period.add()
            if item.get("start") is not None:
                period.start = int(item["start"])
            if item.get("end") is not None:
                period.end = int(item["end"])
        for route_id in item.get("route_ids", []):
            alert.informed_entity.add().route_id = route_id
        for stop_id in item.get("stop_ids", []):
            alert.informed_entity.add().stop_id = stop_id
        header_text = alert.header_text.translation.add()
        header_text.language = "en"
        header_text.text = item.get("header", "")
        if item.get("description") is not None:
            desc_text = alert.description_text.translation.add()
            desc_text.language = "en"
            desc_text.text = item["description"]
    return feed


def _vehicle_feed(
    *, route_id: str, trip_id: str, stop_id: str, timestamp: float
) -> gtfs_realtime_pb2.FeedMessage:
    feed = _new_feed()
    entity = feed.entity.add()
    entity.id = trip_id
    vehicle = entity.vehicle
    vehicle.trip.trip_id = trip_id
    vehicle.trip.route_id = route_id
    vehicle.stop_id = stop_id
    vehicle.current_status = 1  # STOPPED_AT
    vehicle.timestamp = int(timestamp)
    vehicle.position.latitude = 40.65
    vehicle.position.longitude = -73.96
    return feed


def _expected_suffixes() -> set[str]:
    return {
        route_to_feed[route] or "numbered"
        for route in ALL_SUBWAY_ROUTES
        if route in route_to_feed
    }


def _feed_group_dicts(
    *,
    vehicle_suffix: str | None = None,
    vehicle_bytes: bytes | None = None,
    malformed_suffix: str | None = None,
    missing_suffixes: set[str] | None = None,
) -> list[dict]:
    groups: list[dict] = []
    for suffix in sorted(_expected_suffixes()):
        if missing_suffixes and suffix in missing_suffixes:
            continue
        content = _new_feed().SerializeToString()
        if suffix == malformed_suffix:
            content = b"\xff\xfe not a protobuf"
        if suffix == vehicle_suffix and vehicle_bytes is not None:
            content = vehicle_bytes
        groups.append({"suffix": suffix, "url": f"https://example.test/{suffix}", "content": content})
    return groups


@unittest.skipIf(
    gtfs_realtime_pb2 is None,
    "gtfs-realtime-bindings are not installed",
)
class CollectOfficialIncidentsTests(unittest.IsolatedAsyncioTestCase):
    async def _collect(
        self,
        *,
        alert_bytes: bytes = b"",
        groups: list[dict] | None = None,
        clock_value: float = FIXED_NOW,
        parse_alerts=None,
        parse_positions=None,
        detect_stalled=None,
    ):
        async def fetch_alerts() -> bytes:
            return alert_bytes

        async def fetch_feed_groups() -> list[dict]:
            return groups if groups is not None else []

        return await collect_official_incidents(
            fetch_alerts=fetch_alerts,
            fetch_feed_groups=fetch_feed_groups,
            parse_alerts=parse_alerts,
            parse_positions=parse_positions,
            detect_stalled=detect_stalled,
            clock=lambda: clock_value,
        )

    async def test_current_alert_feed_with_zero_alerts_is_current(self):
        alert_bytes = _new_feed().SerializeToString()
        snapshot = await self._collect(alert_bytes=alert_bytes)
        self.assertEqual(snapshot.incidents, ())
        self.assertEqual(snapshot.source_status[SOURCE_ALERTS], STATUS_CURRENT)
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_UNAVAILABLE)

    async def test_all_feed_groups_usable_is_current(self):
        snapshot = await self._collect(groups=_feed_group_dicts())
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_CURRENT)
        self.assertEqual(snapshot.incidents, ())

    async def test_partial_feed_keeps_usable_incidents(self):
        vehicle_bytes = _vehicle_feed(
            route_id="Q", trip_id="q-trip-9", stop_id="D25N", timestamp=FIXED_NOW - 500
        ).SerializeToString()
        groups = _feed_group_dicts(
            vehicle_suffix="nqrw",
            vehicle_bytes=vehicle_bytes,
            malformed_suffix="ace",
            missing_suffixes={"si"},
        )
        snapshot = await self._collect(groups=groups)
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_PARTIAL)
        self.assertEqual(len(snapshot.incidents), 1)
        self.assertEqual(snapshot.incidents[0]["affected_route_ids"], ["Q"])
        self.assertEqual(snapshot.incidents[0]["affected_stop_ids"], ["D25N"])

    async def test_zero_usable_feeds_is_unavailable(self):
        empty = await self._collect(groups=[])
        self.assertEqual(empty.source_status[SOURCE_GTFS_RT], STATUS_UNAVAILABLE)
        self.assertEqual(empty.incidents, ())
        malformed_only = await self._collect(
            groups=[{"suffix": "nqrw", "content": b"\xff\xfe garbage"}]
        )
        self.assertEqual(malformed_only.source_status[SOURCE_GTFS_RT], STATUS_UNAVAILABLE)
        self.assertEqual(malformed_only.incidents, ())

    async def test_empty_feed_group_bytes_are_not_usable(self):
        groups = _feed_group_dicts()
        for group in groups:
            if group["suffix"] == "ace":
                group["content"] = b""
        snapshot = await self._collect(groups=groups)
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_PARTIAL)
        self.assertEqual(snapshot.incidents, ())

    async def test_only_empty_feed_group_is_unavailable(self):
        snapshot = await self._collect(groups=[{"suffix": "nqrw", "content": b""}])
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_UNAVAILABLE)
        self.assertEqual(snapshot.incidents, ())

    async def test_non_list_parser_result_is_not_usable(self):
        snapshot = await self._collect(
            groups=_feed_group_dicts(),
            parse_positions=lambda _: {"not": "a list"},
        )
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_UNAVAILABLE)
        self.assertEqual(snapshot.incidents, ())

    async def test_invalid_parser_result_keeps_valid_groups(self):
        vehicle_bytes = _vehicle_feed(
            route_id="Q", trip_id="q-trip-11", stop_id="D30N", timestamp=FIXED_NOW - 400
        ).SerializeToString()

        def selective_parser(raw_bytes):
            if raw_bytes == vehicle_bytes:
                return [
                    {
                        "route_id": "Q",
                        "stop_id": "D30N",
                        "status": "STOPPED_AT",
                        "trip_id": "q-trip-11",
                        "timestamp": FIXED_NOW - 400,
                    }
                ]
            return None

        snapshot = await self._collect(
            groups=_feed_group_dicts(vehicle_suffix="nqrw", vehicle_bytes=vehicle_bytes),
            parse_positions=selective_parser,
        )
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_PARTIAL)
        self.assertEqual(len(snapshot.incidents), 1)
        self.assertEqual(snapshot.incidents[0]["affected_route_ids"], ["Q"])

    async def test_detector_exception_keeps_alerts_and_marks_gtfs_unavailable(self):
        alert_bytes = _alert_feed(
            [{"id": "a1", "route_ids": ["Q"], "header": "h", "description": "d"}]
        ).SerializeToString()
        vehicle_bytes = _vehicle_feed(
            route_id="Q", trip_id="q-trip-12", stop_id="D31N", timestamp=FIXED_NOW - 400
        ).SerializeToString()

        def failing_detector(_positions, _route_ids, *, now_timestamp):
            raise RuntimeError("stalled detector unavailable")

        snapshot = await self._collect(
            alert_bytes=alert_bytes,
            groups=_feed_group_dicts(vehicle_suffix="nqrw", vehicle_bytes=vehicle_bytes),
            detect_stalled=failing_detector,
        )
        self.assertEqual(snapshot.source_status[SOURCE_ALERTS], STATUS_CURRENT)
        self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_UNAVAILABLE)
        self.assertEqual(
            [incident["source"] for incident in snapshot.incidents], [SOURCE_ALERTS]
        )

    async def test_detector_non_list_result_marks_gtfs_unavailable_and_keeps_alerts(self):
        alert_bytes = _alert_feed(
            [{"id": "a1", "route_ids": ["Q"], "header": "h", "description": "d"}]
        ).SerializeToString()
        vehicle_bytes = _vehicle_feed(
            route_id="Q", trip_id="q-trip-13", stop_id="D32N", timestamp=FIXED_NOW - 400
        ).SerializeToString()
        groups = _feed_group_dicts(vehicle_suffix="nqrw", vehicle_bytes=vehicle_bytes)
        for invalid_result in ({"not": "a list"}, ("stalled", "records")):
            snapshot = await self._collect(
                alert_bytes=alert_bytes,
                groups=groups,
                detect_stalled=lambda _positions, _route_ids, *, now_timestamp, result=invalid_result: result,
            )
            self.assertEqual(snapshot.source_status[SOURCE_ALERTS], STATUS_CURRENT)
            self.assertEqual(snapshot.source_status[SOURCE_GTFS_RT], STATUS_UNAVAILABLE)
            self.assertEqual(
                [incident["source"] for incident in snapshot.incidents], [SOURCE_ALERTS]
            )

    async def test_empty_and_malformed_alerts_unavailable_while_gtfs_works(self):
        vehicle_bytes = _vehicle_feed(
            route_id="Q", trip_id="q-trip-3", stop_id="D26N", timestamp=FIXED_NOW - 450
        ).SerializeToString()
        groups = _feed_group_dicts(vehicle_suffix="nqrw", vehicle_bytes=vehicle_bytes)

        empty_alerts = await self._collect(alert_bytes=b"", groups=groups)
        self.assertEqual(empty_alerts.source_status[SOURCE_ALERTS], STATUS_UNAVAILABLE)
        self.assertEqual(empty_alerts.source_status[SOURCE_GTFS_RT], STATUS_CURRENT)
        self.assertEqual(len(empty_alerts.incidents), 1)
        self.assertEqual(empty_alerts.incidents[0]["source"], SOURCE_GTFS_RT)

        malformed_alerts = await self._collect(alert_bytes=b"\xff\xfe not a protobuf", groups=groups)
        self.assertEqual(malformed_alerts.source_status[SOURCE_ALERTS], STATUS_UNAVAILABLE)
        self.assertEqual(malformed_alerts.source_status[SOURCE_GTFS_RT], STATUS_CURRENT)
        self.assertEqual(len(malformed_alerts.incidents), 1)
