"""Static-GTFS arrival fallback over a startup-loaded schedule artifact."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def _service_date(value: object) -> date | None:
    text = str(value or "").replace("-", "")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _seconds(value: object) -> int | None:
    try:
        hours, minutes, seconds = (int(part) for part in str(value).split(":"))
    except (TypeError, ValueError):
        return None
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _active_service(service: dict, service_date: date) -> bool:
    key = service_date.strftime("%Y%m%d")
    exception = (service.get("exceptions") or {}).get(key)
    if exception is not None:
        return int(exception) == 1
    start = _service_date(service.get("start_date"))
    end = _service_date(service.get("end_date"))
    weekdays = {int(value) for value in service.get("weekdays") or []}
    return bool(
        start
        and end
        and start <= service_date <= end
        and service_date.weekday() in weekdays
    )


def _direction_for(stop_id: str, headsign: object, direction_id: object) -> str:
    if stop_id.endswith("N"):
        return "uptown"
    if stop_id.endswith("S"):
        return "downtown"
    return str(headsign or direction_id or "route").strip().casefold()


class ScheduledArrivalIndex:
    """A bounded in-memory view built offline from full static GTFS tables."""

    def __init__(self, artifact: dict[str, Any]):
        self._artifact = artifact
        self.timezone = ZoneInfo(str(artifact.get("timezone") or "America/New_York"))
        self.valid_until = _parse_datetime(artifact.get("valid_until"))

    @classmethod
    def load(cls, path: str | Path) -> ScheduledArrivalIndex:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def lookup(
        self,
        *,
        route_id: str,
        stop_ids: Iterable[str],
        direction: str | None,
        now: datetime,
        limit: int,
    ) -> dict[str, Any]:
        current = now.astimezone(UTC)
        if self.valid_until is None or current > self.valid_until:
            return {
                "status": "stale",
                "predictions": [],
                "valid_until": self.valid_until,
            }

        local_now = current.astimezone(self.timezone)
        requested = str(direction or "").casefold()
        stop_set = {str(value) for value in stop_ids}
        services = self._artifact.get("services") or {}
        predictions: list[dict[str, Any]] = []
        # Previous-day service is required for GTFS times beyond 24:00. The
        # next service date covers trips just after midnight.
        for offset in (-1, 0, 1):
            operating_date = local_now.date() + timedelta(days=offset)
            midnight = datetime.combine(operating_date, time.min, self.timezone)
            for trip in self._artifact.get("trips") or []:
                if str(trip.get("route_id") or "").upper() != route_id:
                    continue
                service = services.get(str(trip.get("service_id") or "")) or {}
                if not _active_service(service, operating_date):
                    continue
                stop_times = [
                    row
                    for row in trip.get("stop_times") or []
                    if str(row.get("stop_id") or "") in stop_set
                ]
                for row in stop_times:
                    stop_id = str(row.get("stop_id") or "")
                    label = str(
                        trip.get("trip_headsign") or trip.get("direction_id") or "route"
                    )
                    normalized_direction = _direction_for(
                        stop_id, label, trip.get("direction_id")
                    )
                    if (
                        requested
                        and requested not in normalized_direction
                        and requested not in label.casefold()
                    ):
                        continue
                    arrival_offset = _seconds(row.get("arrival_time"))
                    if arrival_offset is None:
                        continue
                    frequencies = trip.get("frequencies") or []
                    if frequencies:
                        first_offset = _seconds(
                            (trip.get("stop_times") or [{}])[0].get("arrival_time")
                        )
                        if first_offset is None:
                            continue
                        stop_offset = arrival_offset - first_offset
                        for frequency in frequencies:
                            start = _seconds(frequency.get("start_time"))
                            end = _seconds(frequency.get("end_time"))
                            headway = int(frequency.get("headway_secs") or 0)
                            if start is None or end is None or headway <= 0:
                                continue
                            for departure in range(start, end, headway):
                                self._append_prediction(
                                    predictions,
                                    midnight,
                                    departure + stop_offset,
                                    local_now,
                                    normalized_direction,
                                    label,
                                    trip,
                                )
                    else:
                        self._append_prediction(
                            predictions,
                            midnight,
                            arrival_offset,
                            local_now,
                            normalized_direction,
                            label,
                            trip,
                        )
        ordered = sorted(predictions, key=lambda row: row["arrival_time"])
        bounded: list[dict[str, Any]] = []
        per_direction: dict[str, int] = {}
        for row in ordered:
            key = str(row.get("direction") or "route")
            if per_direction.get(key, 0) >= max(1, limit):
                continue
            per_direction[key] = per_direction.get(key, 0) + 1
            bounded.append(row)
        return {
            "status": "scheduled",
            "predictions": bounded,
            "valid_until": self.valid_until,
        }

    @staticmethod
    def _append_prediction(
        values: list[dict[str, Any]],
        midnight: datetime,
        offset_seconds: int,
        local_now: datetime,
        direction: str,
        label: str,
        trip: dict,
    ) -> None:
        expected = midnight + timedelta(seconds=offset_seconds)
        if expected < local_now - timedelta(seconds=30):
            return
        values.append(
            {
                "arrival_time": int(expected.timestamp()),
                "direction": direction,
                "direction_label": label,
                "trip_id": trip.get("trip_id"),
                "vehicle_id": None,
            }
        )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
