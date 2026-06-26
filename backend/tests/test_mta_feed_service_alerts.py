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
    from app.services.mta import alerts as mta_alerts, bus as mta_bus
else:
    mta_alerts = None
    mta_bus = None


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


@unittest.skipIf(
    gtfs_realtime_pb2 is None or NYC_TZ is None,
    "gtfs-realtime-bindings or timezone data is not installed",
)
class MtaFeedServiceAlertParserTests(unittest.TestCase):
    def test_default_parser_keeps_currently_active_alerts_only(self):
        now = _localized_timestamp(2026, 5, 12, 10)
        feed = gtfs_realtime_pb2.FeedMessage()

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
        feed = gtfs_realtime_pb2.FeedMessage()

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


@unittest.skipIf(mta_bus is None, "GTFS realtime dependencies are unavailable")
class BusTimeParsingTests(unittest.TestCase):
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
