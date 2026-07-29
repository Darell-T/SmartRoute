from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from google.transit import gtfs_realtime_pb2
except ModuleNotFoundError:
    gtfs_realtime_pb2 = None

try:
    NYC_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    NYC_TZ = None

if gtfs_realtime_pb2 is not None and NYC_TZ is not None:
    from app.services.mta import alerts as mta_alerts, bus as mta_bus, subway as mta_subway
else:
    mta_alerts = None
    mta_bus = None
    mta_subway = None


def _localized_timestamp(year: int, month: int, day: int, hour: int) -> int:
    if NYC_TZ is None:
        raise RuntimeError("America/New_York timezone data is unavailable")
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


@unittest.skipIf(
    gtfs_realtime_pb2 is None or NYC_TZ is None,
    "gtfs-realtime-bindings or timezone data is not installed",
)
class MtaFeedServiceAlertParserTests(unittest.TestCase):
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

        self.assertEqual([alert["alert_id"] for alert in alerts], ["active"])

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

        self.assertEqual(
            [alert["alert_id"] for alert in alerts],
            ["active", "future-today"],
        )

    def test_partial_feed_without_header_is_tolerated_as_empty(self):
        """Keep the current parser's defensive handling explicit.

        `ParseFromString` accepts provider bytes that omit the proto2-required
        header. Production parsing intentionally returns no alerts from such an
        empty partial feed rather than failing the whole route request. Valid
        test fixtures must still use `_new_feed` so setup errors are not
        mistaken for parser behavior.
        """
        partial_feed = gtfs_realtime_pb2.FeedMessage()

        alerts = mta_alerts._parse_service_alerts(
            partial_feed.SerializePartialToString(),
            include_same_day=False,
            now_timestamp=_localized_timestamp(2026, 5, 12, 10),
        )

        self.assertEqual(alerts, [])


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

        self.assertEqual(stalled, [{
            "route_id": "B44",
            "location": {"Latitude": 40.669, "Longitude": -73.951},
            "time_recorded": "2026-07-22T21:30:00Z",
        }])

    def test_stalled_train_detector_uses_route_and_staleness_not_direction_text(self):
        positions = [
            {"route_id": "Q", "stop_id": "D24N", "status": "STOPPED", "timestamp": 900, "direction": "northbound"},
            {"route_id": "Q", "stop_id": "D25N", "status": "IN_TRANSIT", "timestamp": 1_000, "direction": "southbound"},
            {"route_id": "R", "stop_id": "R20S", "status": "STOPPED", "timestamp": 800, "direction": "Q"},
        ]

        stalled = mta_subway.detect_stalled_trains(positions, {"Q"}, now_timestamp=1_300)

        self.assertEqual(stalled, [{
            "route_id": "Q", "stop_id": "D24N", "status": "STOPPED", "stalled_minutes": 7,
        }])

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

        self.assertEqual(len(arrivals), 1)
        self.assertEqual(arrivals[0]["route_id"], "B44")
        self.assertEqual(arrivals[0]["stop_id"], "308214")
        self.assertEqual(arrivals[0]["terminal_stop_name"], "Sheepshead Bay")
        self.assertEqual(arrivals[0]["mode"], "bus")

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
        self.assertEqual(len(arrivals), 1)
        self.assertEqual(arrivals[0]["stop_compass"], "SW")
        # SIRI DirectionRef must remain untouched on the arrival.
        self.assertEqual(arrivals[0]["direction"], "1")

    def test_stop_monitoring_defaults_missing_compass_to_empty_string(self):
        stop = {
            "stop_id": "MTA_308214",
            "stop_name": "Nostrand Av/Eastern Pkwy",
            "distance_m": 240,
            "stop_lat": 40.669,
            "stop_lon": -73.951,
        }
        arrivals = mta_bus.parse_bus_stop_monitoring(self._payload(), stop)
        self.assertEqual(arrivals[0]["stop_compass"], "")


if __name__ == "__main__":
    unittest.main()
