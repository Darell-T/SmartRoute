"""Curated event clusters associated with complete candidate transit paths."""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping


@dataclasses.dataclass(frozen=True)
class CrowdHotspot:
    key: str
    name: str
    stations: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class HotspotHit:
    route_index: int
    hotspot_key: str
    hotspot_name: str
    station_name: str
    latitude: float
    longitude: float
    expected_at: datetime | None
    route_id: str


HOTSPOTS = (
    CrowdHotspot(
        "midtown_34",
        "MSG, Penn Station, and Herald Square",
        ("34 St-Penn Station", "34 St-Herald Sq"),
    ),
    CrowdHotspot(
        "barclays",
        "Barclays Center and Atlantic Avenue",
        ("Atlantic Av-Barclays Ctr",),
    ),
    CrowdHotspot("yankee_stadium", "Yankee Stadium", ("161 St-Yankee Stadium",)),
    CrowdHotspot(
        "flushing_meadows",
        "Citi Field, USTA, and Flushing Meadows",
        ("Mets-Willets Point",),
    ),
    CrowdHotspot(
        "columbus_lincoln",
        "Columbus Circle, Lincoln Center, and Radio City",
        (
            "59 St-Columbus Circle",
            "57 St-7 Av",
            "57 St",
            "66 St-Lincoln Center",
            "47-50 Sts-Rockefeller Ctr",
        ),
    ),
    CrowdHotspot(
        "javits_hudson_yards",
        "Javits Center and Hudson Yards",
        ("34 St-Hudson Yards",),
    ),
    CrowdHotspot(
        "times_square",
        "Times Square and Broadway theater district",
        ("Times Sq-42 St", "42 St-Port Authority Bus Terminal"),
    ),
    CrowdHotspot(
        "civic_corridors",
        "Union Square, Washington Square, City Hall, and Foley Square",
        (
            "14 St-Union Sq",
            "W 4 St-Wash Sq",
            "City Hall",
            "Brooklyn Bridge-City Hall",
            "Chambers St",
        ),
    ),
)


def _station_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


_HOTSPOTS_BY_STATION = {
    _station_key(station): hotspot
    for hotspot in HOTSPOTS
    for station in hotspot.stations
}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _coords(value: Mapping[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        latitude = float(value.get("latitude", value.get("lat")))
        longitude = float(value.get("longitude", value.get("lng")))
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _step_stops(gtfs: Any, step: Mapping[str, Any]) -> list[dict[str, Any]]:
    stops: list[dict[str, Any]] = []

    def add(name: object, coordinates: object) -> None:
        point = _coords(coordinates if isinstance(coordinates, Mapping) else None)
        if point is None:
            return
        row = {"name": str(name or "").strip(), "lat": point[0], "lng": point[1]}
        if row["name"] and (
            not stops
            or _station_key(stops[-1]["name"]) != _station_key(row["name"])
        ):
            stops.append(row)

    add(step.get("departure_stop"), step.get("departure_coords"))
    index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    if step.get("type") == "SUBWAY" and index and step.get("route_id"):
        try:
            rows, _metadata = index.get_intermediate_stops_with_coords(
                step["route_id"],
                step.get("departure_stop"),
                step.get("arrival_stop"),
                step.get("departure_coords"),
                step.get("arrival_coords"),
            )
        except Exception:
            rows = []
        for row in rows or []:
            if isinstance(row, Mapping):
                add(row.get("name"), row)
    add(step.get("arrival_stop"), step.get("arrival_coords"))
    return stops


def _interpolated_time(
    departure: datetime | None,
    arrival: datetime | None,
    index: int,
    count: int,
) -> datetime | None:
    if departure is None:
        return arrival
    if arrival is None or count <= 1:
        return departure
    return departure + (arrival - departure) * (index / (count - 1))


def find_hotspot_hits(gtfs: Any, routes: Iterable[Iterable[Mapping[str, Any]]]) -> list[HotspotHit]:
    hits: list[HotspotHit] = []
    seen: set[tuple[int, str]] = set()
    for route_index, route in enumerate(routes or []):
        for step in route or []:
            if step.get("type") not in {"SUBWAY", "BUS"}:
                continue
            stops = _step_stops(gtfs, step)
            departure = _parse_time(step.get("departure_time_iso"))
            arrival = _parse_time(step.get("arrival_time_iso"))
            for stop_index, stop in enumerate(stops):
                hotspot = _HOTSPOTS_BY_STATION.get(_station_key(stop["name"]))
                if hotspot is None or (route_index, hotspot.key) in seen:
                    continue
                seen.add((route_index, hotspot.key))
                hits.append(
                    HotspotHit(
                        route_index=route_index,
                        hotspot_key=hotspot.key,
                        hotspot_name=hotspot.name,
                        station_name=stop["name"],
                        latitude=stop["lat"],
                        longitude=stop["lng"],
                        expected_at=_interpolated_time(
                            departure,
                            arrival,
                            stop_index,
                            len(stops),
                        ),
                        route_id=str(
                            step.get("route_id") or step.get("train_line") or ""
                        ).strip().upper(),
                    )
                )
    return hits
