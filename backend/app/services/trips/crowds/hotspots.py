"""Curated event clusters associated with complete candidate transit paths."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any


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
        parsed = datetime.fromisoformat(value)
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


def _parse_named_stop(name: object, coordinates: object) -> dict[str, Any] | None:
    point = _coords(coordinates if isinstance(coordinates, Mapping) else None)
    if point is None:
        return None
    row = {"name": str(name or "").strip(), "lat": point[0], "lng": point[1]}
    return row if row["name"] else None


def _select_pattern_stops(
    gtfs: Any, step: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    if step.get("type") != "SUBWAY" or not index or not step.get("route_id"):
        return []
    try:
        rows, _metadata = index.get_intermediate_stops_with_coords(
            step["route_id"],
            step.get("departure_stop"),
            step.get("arrival_stop"),
            step.get("departure_coords"),
            step.get("arrival_coords"),
        )
    except Exception:  # noqa: BLE001 pattern-index faults omit intermediates
        return []
    return [row for row in rows or [] if isinstance(row, Mapping)]


def _step_stops(gtfs: Any, step: Mapping[str, Any]) -> list[dict[str, Any]]:
    stops: list[dict[str, Any]] = []
    candidates = [
        (step.get("departure_stop"), step.get("departure_coords")),
        *[(row.get("name"), row) for row in _select_pattern_stops(gtfs, step)],
        (step.get("arrival_stop"), step.get("arrival_coords")),
    ]
    for name, coordinates in candidates:
        row = _parse_named_stop(name, coordinates)
        if row is not None and (
            not stops or _station_key(stops[-1]["name"]) != _station_key(row["name"])
        ):
            stops.append(row)
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


def _project_hotspot_hit(
    route_index: int,
    step: Mapping[str, Any],
    stop: dict[str, Any],
    stop_index: int,
    stop_count: int,
) -> HotspotHit | None:
    hotspot = _HOTSPOTS_BY_STATION.get(_station_key(stop["name"]))
    if hotspot is None:
        return None
    return HotspotHit(
        route_index=route_index,
        hotspot_key=hotspot.key,
        hotspot_name=hotspot.name,
        station_name=stop["name"],
        latitude=stop["lat"],
        longitude=stop["lng"],
        expected_at=_interpolated_time(
            _parse_time(step.get("departure_time_iso")),
            _parse_time(step.get("arrival_time_iso")),
            stop_index,
            stop_count,
        ),
        route_id=str(step.get("route_id") or step.get("train_line") or "")
        .strip()
        .upper(),
    )


def _select_step_hotspot_hits(
    route_index: int, step: Mapping[str, Any], gtfs: Any
) -> list[HotspotHit]:
    if step.get("type") not in {"SUBWAY", "BUS"}:
        return []
    stops = _step_stops(gtfs, step)
    hits: list[HotspotHit] = []
    for stop_index, stop in enumerate(stops):
        hit = _project_hotspot_hit(route_index, step, stop, stop_index, len(stops))
        if hit is not None:
            hits.append(hit)
    return hits


def find_hotspot_hits(gtfs: Any, routes: Iterable[Iterable[Mapping[str, Any]]]) -> list[HotspotHit]:
    hits: list[HotspotHit] = []
    seen: set[tuple[int, str]] = set()
    for route_index, route in enumerate(routes or []):
        for step in route or []:
            for hit in _select_step_hotspot_hits(route_index, step, gtfs):
                key = (route_index, hit.hotspot_key)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(hit)
    return hits
