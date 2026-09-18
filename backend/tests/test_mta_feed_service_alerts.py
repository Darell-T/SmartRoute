from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis
from app.services import cache as cache_module

try:
    from google.transit import gtfs_realtime_pb2
except ModuleNotFoundError:
    gtfs_realtime_pb2 = None

try:
    NYC_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    NYC_TZ = None

if gtfs_realtime_pb2 is not None and NYC_TZ is not None:
    from app.services.mta import (
        alerts as mta_alerts,
    )
    from app.services.mta import (
        bus as mta_bus,
    )
    from app.services.mta import (
        feeds as mta_feeds,
    )
    from app.services.mta import (
        subway as mta_subway,
    )
else:
    mta_alerts = None
    mta_bus = None
    mta_feeds = None
    mta_subway = None


class MissingTimezoneDataError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("America/New_York timezone data is unavailable")


class SensitiveProviderDetailsError(redis.exceptions.ResponseError):
    def __init__(self) -> None:
        super().__init__("sensitive provider details")

def _localized_timestamp(year: int, month: int, day: int, hour: int) -> int:
    if NYC_TZ is None:
        raise MissingTimezoneDataError
    return int(datetime(year, month, day, hour, tzinfo=NYC_TZ).timestamp())


def _add_alert(
    feed: gtfs_realtime_pb2.FeedMessage,
    *,
    entity_id: str,
    route_id: str,
    start: int | None,
    end: int | None,
    header: str,
) -> None:
    entity = feed.entity.add()
    entity.id = entity_id
    alert = entity.alert

    if start is not None or end is not None:
        period = alert.active_period.add()
        if start is not None:
            period.start = start
        if end is not None:
            period.end = end

    informed_entity = alert.informed_entity.add()
    informed_entity.route_id = route_id

    header_text = alert.header_text.translation.add()
    header_text.language = "en"
    header_text.text = header

    description_text = alert.description_text.translation.add()
    description_text.language = "en"
    description_text.text = f"{header} detail"


def _new_feed() -> gtfs_realtime_pb2.FeedMessage:
    """Build a schema-valid GTFS-RT feed, including its required header."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_778_595_600
    return feed


class _HttpClient:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url):
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=200, content=self.content)


class _RejectingRedis:
    def get(self, _key):
        raise SensitiveProviderDetailsError

    def setex(self, _key, _ttl, _value):
        raise SensitiveProviderDetailsError


@unittest.skipIf(mta_feeds is None, "GTFS realtime dependencies are unavailable")
class MtaProviderCacheResilienceTests(unittest.IsolatedAsyncioTestCase):
    def _seed_alert_cache(self, metadata=None):
        cache_module.cache_set(mta_alerts.ALERTS_URL, b"cached-feed", 60, fail_open=True)
        if metadata is not None:
            cache_module.cache_set(
                mta_alerts._ALERTS_METADATA_KEY,
                json.dumps(metadata),
                60,
                fail_open=True,
            )

    async def _fetch_alerts(self, client):
        with patch("httpx.AsyncClient", return_value=client):
            return await mta_alerts.fetch_service_alerts(
                force_refresh=True, with_metadata=True
            )

    async def test_current_alert_read_bypasses_cache_and_records_live_timestamp(self):
        self._seed_alert_cache()
        result = await self._fetch_alerts(_HttpClient(b"fresh-feed"))
        assert result["content"] == b"fresh-feed"
        assert result["freshness"] == "live"
        assert result["observed_at"]
        metadata = json.loads(cache_module.cache_get(mta_alerts._ALERTS_METADATA_KEY, fail_open=True))
        assert metadata["content_sha256"] == mta_alerts._content_digest(b"fresh-feed")

    async def test_current_alert_read_uses_timestamped_stale_cache_on_provider_failure(self):
        self._seed_alert_cache(
            {
                "observed_at": "2026-08-22T12:00:00+00:00",
                "content_sha256": mta_alerts._content_digest(b"cached-feed"),
            }
        )
        result = await self._fetch_alerts(
            _HttpClient(error=OSError("provider unavailable"))
        )
        assert result["content"] == b"cached-feed"
        assert result["freshness"] == "stale"
        assert result["observed_at"] == "2026-08-22T12:00:00+00:00"

    async def test_stale_cache_without_matching_metadata_has_no_observation_time(self):
        for metadata in (
            {},
            {
                "observed_at": "2026-08-22T12:00:00+00:00",
                "content_sha256": mta_alerts._content_digest(b"different-feed"),
            },
        ):
            with self.subTest(metadata=metadata):
                self._seed_alert_cache(metadata)
                result = await self._fetch_alerts(
                    _HttpClient(error=OSError("provider unavailable"))
                )
                assert result["freshness"] == "stale"
                assert result["observed_at"] is None

    async def test_successful_mta_fetches_survive_redis_quota_errors(self):
        cache_module._mem.clear()
        with patch.object(
            cache_module,
            "redis_client",
            _RejectingRedis(),
        ), patch("httpx.AsyncClient", return_value=_HttpClient(b"provider-feed")):
            feeds = await mta_feeds.fetch_feeds_with_metadata(
                ["Q"],
                force_refresh=True,
            )
            alerts = await mta_alerts.fetch_service_alerts(force_refresh=True)

        assert [feed["content"] for feed in feeds] == [b"provider-feed"]
        assert alerts == b"provider-feed"

    async def test_process_owned_snapshot_fetches_can_skip_redis_writes(self):
        with patch("httpx.AsyncClient", return_value=_HttpClient(b"provider-feed")), patch.object(
            cache_module,
            "cache_set",
        ) as cache_set:
            feeds = await mta_feeds.fetch_feeds_with_metadata(
                ["Q"],
                force_refresh=True,
                cache_result=False,
            )
            alerts = await mta_alerts.fetch_service_alerts(
                force_refresh=True,
                cache_result=False,
            )

        assert [feed["content"] for feed in feeds] == [b"provider-feed"]
        assert alerts == b"provider-feed"
        cache_set.assert_not_called()

    def test_trip_update_parser_preserves_stop_sequence(self):
        feed = _new_feed()
        entity = feed.entity.add()
        entity.id = "trip-update"
        trip_update = entity.trip_update
        trip_update.trip.trip_id = "trip-1"
        trip_update.trip.route_id = "Q"
        stop = trip_update.stop_time_update.add()
        stop.stop_id = "Q01N"
        stop.stop_sequence = 17
        stop.arrival.time = 1_778_595_900

        parsed = mta_feeds.parse_bytes(feed.SerializeToString())

        assert parsed[0]["stop_sequence"] == 17


@unittest.skipIf(
    gtfs_realtime_pb2 is None or NYC_TZ is None,
    "gtfs-realtime-bindings or timezone data is not installed",
)
class MtaFeedServiceAlertParserTests(unittest.TestCase):
    def test_english_text_prefers_english_and_falls_back_to_first_nonempty(self):
        assert mta_alerts._english_text(None) == ""
        assert mta_alerts._english_text(SimpleNamespace(translation=[])) == ""
        assert mta_alerts._english_text(SimpleNamespace(translation=None)) == ""
        spanish_then_english = SimpleNamespace(
            translation=[
                SimpleNamespace(language="es", text="Retraso"),
                SimpleNamespace(language="en", text="Delay"),
            ]
        )
        assert mta_alerts._english_text(spanish_then_english) == "Delay"
        spanish_only = SimpleNamespace(
            translation=[SimpleNamespace(language="es", text="Retraso")]
        )
        assert mta_alerts._english_text(spanish_only) == "Retraso"
        empty_then_french = SimpleNamespace(
            translation=[
                SimpleNamespace(language="es", text=""),
                SimpleNamespace(language="fr", text="Retard"),
            ]
        )
        assert mta_alerts._english_text(empty_then_french) == "Retard"

    def test_alert_semantics_covers_each_existing_outcome(self):
        cases = (
            (
                "planned-work-id",
                "lmm:planned_work:33095",
                "Weekend service change",
                "",
                ("planned", "planned_service_change", "unknown", True),
            ),
            (
                "unplanned-alert-id",
                "lmm:alert:4401",
                "Crowd control",
                "",
                ("unplanned", "unknown", "unknown", True),
            ),
            (
                "unknown-source-id",
                "gtfs:entity:9",
                "Notice",
                "",
                ("unknown", "unknown", "unknown", True),
            ),
            (
                "planned-local-operation",
                "LMM:PLANNED_WORK:2",
                "Q trains run local",
                "Q runs local and trains operate local after a switch to local",
                ("planned", "express_to_local", True, False),
            ),
            (
                "unplanned-express-local",
                "LMM:ALERT:2",
                "F express trains running local",
                "",
                ("unplanned", "express_to_local", True, True),
            ),
            (
                "unplanned-local-without-express",
                "lmm:alert:21",
                "The Q operates local in Brooklyn",
                "",
                ("unplanned", "unknown", True, True),
            ),
            (
                "suspension-text",
                "lmm:alert:3",
                "Service is suspended",
                "There is no service. Trains are not running, do not run, and will not run.",
                ("unplanned", "suspension", False, True),
            ),
            (
                "severe-delay-text",
                "lmm:alert:10",
                "Severe delays on the 4",
                "",
                ("unplanned", "severe_delay", "unknown", True),
            ),
            (
                "ordinary-delay-text",
                "lmm:alert:11",
                "Delays on the 2",
                "",
                ("unplanned", "delay", "unknown", True),
            ),
            (
                "unknown-change-type",
                "lmm:alert:12",
                "Crowd control",
                "",
                ("unplanned", "unknown", "unknown", True),
            ),
            (
                "service-operating-true",
                "lmm:alert:13",
                "Service operates in both directions",
                "",
                ("unplanned", "unknown", True, True),
            ),
        )
        for label, source_id, header, description, expected in cases:
            with self.subTest(label=label):
                assert mta_alerts._alert_semantics(source_id, header, description) == expected

    def test_planned_q_express_to_local_preserves_typed_provenance(self):
        now = _localized_timestamp(2026, 5, 12, 10)
        feed = _new_feed()
        entity = feed.entity.add()
        entity.id = "lmm:planned_work:33095"
        alert = entity.alert
        period = alert.active_period.add()
        period.start = now - 600
        period.end = now + 3600
        header = alert.header_text.translation.add()
        header.language = "en"
        header.text = "Q trains run local"
        description = alert.description_text.translation.add()
        description.language = "en"
        description.text = (
            "In Manhattan, Q runs local in both directions between "
            "57 St-7 Av and Canal St"
        )
        for direction_id, stop_id in ((0, "Q01N"), (1, "Q01S")):
            selector = alert.informed_entity.add()
            selector.route_id = "Q"
            selector.stop_id = stop_id
            selector.direction_id = direction_id

        parsed = mta_alerts._parse_service_alerts(
            feed.SerializeToString(),
            include_same_day=False,
            now_timestamp=now,
        )

        assert len(parsed) == 1
        result = parsed[0]
        assert result["source"] == "mta_service_alerts"
        assert result["source_id"] == "lmm:planned_work:33095"
        assert result["alert_id"] == "lmm:planned_work:33095"
        assert result["route_ids"] == ["Q"]
        assert result["stop_ids"] == ["Q01N", "Q01S"]
        assert result["direction_ids"] == ["0", "1"]
        assert result["direction_scope"] == "both_directions"
        assert result["planned_status"] == "planned"
        assert result["change_type"] == "express_to_local"
        assert result["service_operating"] is True
        assert result["material_disruption"] is False
        assert result["effective_start"] == now - 600
        assert result["effective_end"] == now + 3600
        assert result["effective_window"] == {"start": now - 600, "end": now + 3600}
        assert result["affected_segments"] == [{"route_id": "Q", "stop_id": "Q01N", "direction_id": "0"}, {"route_id": "Q", "stop_id": "Q01S", "direction_id": "1"}]
        assert result["feed_observed_at"] == datetime.fromtimestamp(feed.header.timestamp, tz=UTC).isoformat()
        assert result["local_verified_at"] == datetime.fromtimestamp(now, tz=UTC).isoformat()

    def test_default_parser_keeps_currently_active_alerts_only(self):
        now = _localized_timestamp(2026, 5, 12, 10)
        feed = _new_feed()

        _add_alert(
            feed,
            entity_id="active",
            route_id="A",
            start=now - 600,
            end=now + 3600,
            header="Active A train delay",
        )
        _add_alert(
            feed,
            entity_id="future-today",
            route_id="B",
            start=now + 3600,
            end=now + 7200,
            header="Future B planned work",
        )
        _add_alert(
            feed,
            entity_id="expired",
            route_id="C",
            start=now - 7200,
            end=now - 3600,
            header="Expired C notice",
        )

        alerts = mta_alerts._parse_service_alerts(
            feed.SerializeToString(),
            include_same_day=False,
            now_timestamp=now,
        )

        assert [alert["alert_id"] for alert in alerts] == ["active"]

    def test_service_board_parser_includes_same_day_future_until_expired(self):
        now = _localized_timestamp(2026, 5, 12, 10)
        tomorrow = _localized_timestamp(2026, 5, 13, 9)
        feed = _new_feed()

        _add_alert(
            feed,
            entity_id="active",
            route_id="A",
            start=now - 600,
            end=now + 3600,
            header="Active A train delay",
        )
        _add_alert(
            feed,
            entity_id="future-today",
            route_id="B",
            start=now + 3600,
            end=now + 7200,
            header="Future B planned work",
        )
        _add_alert(
            feed,
            entity_id="future-tomorrow",
            route_id="D",
            start=tomorrow,
            end=tomorrow + 3600,
            header="Tomorrow D planned work",
        )
        _add_alert(
            feed,
            entity_id="expired",
            route_id="C",
            start=now - 7200,
            end=now - 3600,
            header="Expired C notice",
        )

        alerts = mta_alerts._parse_service_alerts(
            feed.SerializeToString(),
            include_same_day=True,
            now_timestamp=now,
        )

        assert [alert["alert_id"] for alert in alerts] == ["active", "future-today"]

    def test_partial_feed_without_header_is_tolerated_as_empty(self):
        partial_feed = gtfs_realtime_pb2.FeedMessage()

        alerts = mta_alerts._parse_service_alerts(
            partial_feed.SerializePartialToString(),
            include_same_day=False,
            now_timestamp=_localized_timestamp(2026, 5, 12, 10),
        )

        assert alerts == []


@unittest.skipIf(mta_bus is None, "GTFS realtime dependencies are unavailable")
class BusTimeParsingTests(unittest.TestCase):
    def test_stalled_bus_parser_keeps_only_actionable_no_progress_vehicles(self):
        payload = {
            "Siri": {
                "ServiceDelivery": {
                    "VehicleMonitoringDelivery": [{
                        "VehicleActivity": [
                            {
                                "RecordedAtTime": "2026-07-22T21:30:00Z",
                                "MonitoredVehicleJourney": {
                                    "LineRef": "MTA NYCT_B44",
                                    "ProgressRate": "noProgress",
                                    "ProgressStatus": [],
                                    "VehicleLocation": {"Latitude": 40.669, "Longitude": -73.951},
                                },
                            },
                            {
                                "MonitoredVehicleJourney": {
                                    "LineRef": "MTA NYCT_B46",
                                    "ProgressRate": "noProgress",
                                    "ProgressStatus": ["layover"],
                                    "VehicleLocation": {"Latitude": 40.67, "Longitude": -73.95},
                                },
                            },
                            {
                                "MonitoredVehicleJourney": {
                                    "ProgressRate": "noProgress",
                                    "VehicleLocation": {"Latitude": 40.67, "Longitude": -73.95},
                                },
                            },
                            {
                                "MonitoredVehicleJourney": {
                                    "LineRef": "MTA NYCT_B12",
                                    "ProgressRate": "noProgress",
                                },
                            },
                        ],
                    }],
                },
            },
        }

        stalled = mta_bus.parse_stalled_bus_positions(payload)

        assert stalled == [{"route_id": "B44", "location": {"Latitude": 40.669, "Longitude": -73.951}, "time_recorded": "2026-07-22T21:30:00Z"}]

    def test_stalled_train_detector_uses_route_and_staleness_not_direction_text(self):
        positions = [
            {"route_id": "Q", "stop_id": "D24N", "status": "STOPPED", "timestamp": 900, "direction": "northbound"},
            {"route_id": "Q", "stop_id": "D25N", "status": "IN_TRANSIT", "timestamp": 1_000, "direction": "southbound"},
            {"route_id": "R", "stop_id": "R20S", "status": "STOPPED", "timestamp": 800, "direction": "Q"},
        ]

        stalled = mta_subway.detect_stalled_trains(positions, {"Q"}, now_timestamp=1_300)

        assert stalled == [{"route_id": "Q", "stop_id": "D24N", "status": "STOPPED", "stalled_minutes": 7}]

    def test_stop_monitoring_accepts_dict_delivery_and_departure_time(self):
        payload = {
            "Siri": {
                "ServiceDelivery": {
                    "StopMonitoringDelivery": {
                        "MonitoredStopVisit": {
                            "MonitoredVehicleJourney": {
                                "LineRef": "MTA NYCT_B44",
                                "PublishedLineName": ["B44"],
                                "DirectionRef": "SOUTHBOUND",
                                "DestinationName": ["Sheepshead Bay"],
                                "FramedVehicleJourneyRef": {
                                    "DatedVehicleJourneyRef": "bus-trip-1",
                                },
                                "MonitoredCall": {
                                    "StopPointRef": "MTA_308214",
                                    "ExpectedDepartureTime": "2026-06-11T12:10:00-04:00",
                                },
                            },
                        },
                    },
                },
            },
        }
        stop = {
            "stop_id": "MTA_308214",
            "stop_name": "Nostrand Av/Eastern Pkwy",
            "distance_m": 240,
            "stop_lat": 40.669,
            "stop_lon": -73.951,
        }

        arrivals = mta_bus.parse_bus_stop_monitoring(payload, stop)

        assert len(arrivals) == 1
        assert arrivals[0]["route_id"] == "B44"
        assert arrivals[0]["stop_id"] == "308214"
        assert arrivals[0]["terminal_stop_name"] == "Sheepshead Bay"
        assert arrivals[0]["mode"] == "bus"

    def _payload(self):
        return {
            "Siri": {
                "ServiceDelivery": {
                    "StopMonitoringDelivery": {
                        "MonitoredStopVisit": {
                            "MonitoredVehicleJourney": {
                                "LineRef": "MTA NYCT_B44",
                                "PublishedLineName": ["B44"],
                                "DirectionRef": "1",
                                "DestinationName": ["Sheepshead Bay"],
                                "FramedVehicleJourneyRef": {
                                    "DatedVehicleJourneyRef": "bus-trip-1",
                                },
                                "MonitoredCall": {
                                    "StopPointRef": "MTA_308214",
                                    "ExpectedDepartureTime": "2026-06-11T12:10:00-04:00",
                                },
                            },
                        },
                    },
                },
            },
        }

    def test_stop_monitoring_passes_stop_compass_through(self):
        stop = {
            "stop_id": "MTA_308214",
            "stop_name": "Nostrand Av/Eastern Pkwy",
            "distance_m": 240,
            "stop_lat": 40.669,
            "stop_lon": -73.951,
            "stop_compass": "SW",
        }
        arrivals = mta_bus.parse_bus_stop_monitoring(self._payload(), stop)
        assert len(arrivals) == 1
        assert arrivals[0]["stop_compass"] == "SW"
        # SIRI DirectionRef must remain untouched on the arrival.
        assert arrivals[0]["direction"] == "1"

    def test_stop_monitoring_defaults_missing_compass_to_empty_string(self):
        stop = {
            "stop_id": "MTA_308214",
            "stop_name": "Nostrand Av/Eastern Pkwy",
            "distance_m": 240,
            "stop_lat": 40.669,
            "stop_lon": -73.951,
        }
        arrivals = mta_bus.parse_bus_stop_monitoring(self._payload(), stop)
        assert arrivals[0]["stop_compass"] == ""


if __name__ == "__main__":
    unittest.main()
