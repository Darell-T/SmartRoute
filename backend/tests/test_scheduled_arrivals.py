from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.mta.static_gtfs.scheduled_arrivals import ScheduledArrivalIndex

NYC = ZoneInfo("America/New_York")
NOW = datetime(2027, 1, 15, 23, 55, tzinfo=NYC)


def _artifact() -> dict:
    return {
        "timezone": "America/New_York",
        "valid_until": (NOW + timedelta(days=30)).astimezone(UTC).isoformat(),
        "services": {
            "weekday": {
                "start_date": "20270101",
                "end_date": "20270228",
                "weekdays": [0, 1, 2, 3, 4],
                "exceptions": {},
            },
            "exception_only": {
                "start_date": "20270101",
                "end_date": "20270228",
                "weekdays": [],
                "exceptions": {"20270115": 1},
            },
        },
        "trips": [
            {
                "route_id": "Q",
                "trip_id": "overnight",
                "service_id": "weekday",
                "trip_headsign": "Coney Island",
                "direction_id": 1,
                "stop_times": [
                    {"stop_id": "D28S", "arrival_time": "24:10:00"},
                    {"stop_id": "D29S", "arrival_time": "24:12:00"},
                ],
            },
            {
                "route_id": "Q",
                "trip_id": "frequency",
                "service_id": "exception_only",
                "trip_headsign": "96 St",
                "direction_id": 0,
                "stop_times": [
                    {"stop_id": "D28N", "arrival_time": "24:00:00"},
                    {"stop_id": "D26N", "arrival_time": "24:05:00"},
                ],
                "frequencies": [
                    {
                        "start_time": "24:00:00",
                        "end_time": "24:31:00",
                        "headway_secs": 600,
                    }
                ],
            },
        ],
    }


class ScheduledArrivalIndexTests(unittest.TestCase):
    def test_handles_overnight_service_day_and_station_direction(self):
        result = ScheduledArrivalIndex(_artifact()).lookup(
            route_id="Q",
            stop_ids={"D28N", "D28S"},
            direction="downtown",
            now=NOW.astimezone(UTC),
            limit=3,
        )
        assert result["status"] == "scheduled"
        assert len(result["predictions"]) == 1
        expected = datetime.fromtimestamp(
            result["predictions"][0]["arrival_time"], NYC
        )
        assert (expected.day, expected.hour, expected.minute) == (16, 0, 10)

    def test_calendar_exception_and_frequency_expansion_are_applied(self):
        result = ScheduledArrivalIndex(_artifact()).lookup(
            route_id="Q",
            stop_ids={"D28N"},
            direction="uptown",
            now=NOW.astimezone(UTC),
            limit=3,
        )
        times = [
            datetime.fromtimestamp(row["arrival_time"], NYC).minute
            for row in result["predictions"]
        ]
        assert times == [0, 10, 20]

    def test_removed_service_exception_suppresses_trip(self):
        artifact = _artifact()
        artifact["services"]["weekday"]["exceptions"]["20270115"] = 2
        result = ScheduledArrivalIndex(artifact).lookup(
            route_id="Q",
            stop_ids={"D28S"},
            direction="downtown",
            now=NOW.astimezone(UTC),
            limit=3,
        )
        assert result["predictions"] == []

    def test_stale_artifact_is_not_served(self):
        artifact = _artifact()
        artifact["valid_until"] = (
            NOW - timedelta(seconds=1)
        ).astimezone(UTC).isoformat()
        result = ScheduledArrivalIndex(artifact).lookup(
            route_id="Q",
            stop_ids={"D28S"},
            direction=None,
            now=NOW.astimezone(UTC),
            limit=3,
        )
        assert result["status"] == "stale"
        assert result["predictions"] == []


if __name__ == "__main__":
    unittest.main()
