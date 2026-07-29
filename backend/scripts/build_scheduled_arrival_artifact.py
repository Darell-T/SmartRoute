"""Build the optional static-GTFS arrival artifact used during RT outages."""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _rows(archive: zipfile.ZipFile, name: str):
    try:
        handle = archive.open(name)
    except KeyError:
        return
    with handle:
        yield from csv.DictReader(line.decode("utf-8-sig") for line in handle)


def build(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as archive:
        services: dict[str, dict] = {}
        for row in _rows(archive, "calendar.txt") or ():
            services[row["service_id"]] = {
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "weekdays": [
                    index for index, field in enumerate(WEEKDAYS)
                    if row.get(field) == "1"
                ],
                "exceptions": {},
            }
        latest_date = ""
        for row in _rows(archive, "calendar_dates.txt") or ():
            service = services.setdefault(
                row["service_id"],
                {
                    "start_date": row["date"],
                    "end_date": row["date"],
                    "weekdays": [],
                    "exceptions": {},
                },
            )
            service["exceptions"][row["date"]] = int(row["exception_type"])
            latest_date = max(latest_date, row["date"])
        for service in services.values():
            latest_date = max(latest_date, str(service.get("end_date") or ""))

        trips: dict[str, dict] = {}
        for row in _rows(archive, "trips.txt") or ():
            if row.get("service_id") not in services:
                continue
            trips[row["trip_id"]] = {
                "route_id": row["route_id"],
                "trip_id": row["trip_id"],
                "service_id": row["service_id"],
                "trip_headsign": row.get("trip_headsign") or "",
                "direction_id": row.get("direction_id") or "",
                "stop_times": [],
            }
        for row in _rows(archive, "stop_times.txt") or ():
            trip = trips.get(row.get("trip_id") or "")
            if trip is None:
                continue
            arrival = row.get("arrival_time") or row.get("departure_time")
            if arrival:
                trip["stop_times"].append(
                    {
                        "stop_id": row["stop_id"],
                        "arrival_time": arrival,
                    }
                )
        frequencies = defaultdict(list)
        for row in _rows(archive, "frequencies.txt") or ():
            if row.get("trip_id") in trips:
                frequencies[row["trip_id"]].append(
                    {
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                        "headway_secs": int(row["headway_secs"]),
                    }
                )
        for trip_id, rows in frequencies.items():
            trips[trip_id]["frequencies"] = rows

    valid_until = None
    if latest_date:
        valid_until = datetime.strptime(latest_date, "%Y%m%d").replace(
            hour=23,
            minute=59,
            second=59,
            tzinfo=ZoneInfo("America/New_York"),
        ).astimezone(timezone.utc).isoformat()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "valid_until": valid_until,
        "timezone": "America/New_York",
        "services": services,
        "trips": list(trips.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "app"
        / "data"
        / "scheduled_arrivals.json",
    )
    args = parser.parse_args()
    artifact = build(args.zip)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(artifact, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"wrote {args.out} services={len(artifact['services'])} "
        f"trips={len(artifact['trips'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
