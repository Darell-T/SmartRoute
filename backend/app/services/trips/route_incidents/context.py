"""Candidate-aware stop context for local incident searches.

This module intentionally works on the parsed Google Routes dictionaries used
by both trip entry points.  It does not enrich a route or contact GTFS/any
upstream service; callers may pass already-enriched intermediate stops.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Any


def valid_coordinate_pair(latitude: object, longitude: object) -> tuple[float, float] | None:
    """Return a finite, plausible coordinate pair, excluding the null island."""
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not (isfinite(lat) and isfinite(lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None
    return lat, lon


def _text(value: object) -> str | None:
    value = str(value or "").strip()
    return value or None


def _coords(value: Mapping[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(value, Mapping):
        return None
    return valid_coordinate_pair(
        value.get("latitude", value.get("lat")),
        value.get("longitude", value.get("lng", value.get("lon"))),
    )


@dataclass(frozen=True)
class CandidateStopAssociation:
    candidate_route_id: str
    mode: str | None = None
    route_id: str | None = None
    direction: str | None = None
    stop_order: int | None = None
    segment_context: str | None = None


@dataclass
class CandidateStopContext:
    """One physical stop, with a reverse map to every candidate using it."""

    stop_id: str | None
    stop_name: str | None
    latitude: float
    longitude: float
    associations: list[CandidateStopAssociation] = field(default_factory=list)

    @property
    def candidate_route_ids(self) -> list[str]:
        return sorted({item.candidate_route_id for item in self.associations})

    @property
    def modes(self) -> list[str]:
        return sorted({item.mode for item in self.associations if item.mode})

    @property
    def route_ids(self) -> list[str]:
        return sorted({item.route_id for item in self.associations if item.route_id})


def _physical_keys(stop_id: str | None, name: str | None, lat: float, lon: float) -> list[tuple[str, ...]]:
    """Stable aliases for a physical stop from partially enriched routes."""
    normalized_name = re.sub(r"[^a-z0-9]+", "", (name or "").casefold())
    # Five decimals is roughly a metre in latitude: enough to join provider
    # representations of one stop without joining adjacent stops.
    keys = [("point", normalized_name, f"{lat:.5f}", f"{lon:.5f}")]
    if stop_id:
        keys.insert(0, ("id", stop_id))
    return keys


def _stop_records(step: Mapping[str, Any]) -> Iterable[tuple[Mapping[str, Any], int]]:
    """Yield endpoints plus supplied intermediates in route order.

    Intermediate lists are opportunistic and can omit one or both endpoints,
    so endpoints must always be retained and deduplicated by the caller.
    """
    departure = step.get("departure_coords")
    if isinstance(departure, Mapping):
        yield {**departure, "name": step.get("departure_stop")}, 0
    intermediate = step.get("intermediate_stop_locations")
    if isinstance(intermediate, list) and intermediate:
        for index, item in enumerate(intermediate):
            if isinstance(item, Mapping):
                yield item, index + 1
    arrival = step.get("arrival_coords")
    if isinstance(arrival, Mapping):
        offset = len(intermediate) if isinstance(intermediate, list) else 0
        yield {**arrival, "name": step.get("arrival_stop")}, offset + 1


_TRANSIT_STEP_TYPES = frozenset({"SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL"})


@dataclass(frozen=True)
class _ParsedTransitStop:
    candidate_id: str
    mode: str
    route_id: str | None
    direction: str | None
    segment_context: str | None
    stop_id: str | None
    name: str | None
    latitude: float
    longitude: float
    stop_order: int
    step_key: tuple[int, int]


def _step_segment_context(step: Mapping[str, Any]) -> str | None:
    departure, arrival = _text(step.get("departure_stop")), _text(step.get("arrival_stop"))
    return " -> ".join(item for item in (departure, arrival) if item) or None


def _record_stop_identity(record: Mapping[str, Any]) -> tuple[str | None, str | None]:
    stop_id = _text(record.get("stop_id") or record.get("id"))
    name = _text(record.get("name") or record.get("stop_name"))
    return stop_id, name


def _parse_transit_step(
    step: object,
    candidate_id: str,
    step_index: int,
    route_index: int,
) -> Iterable[_ParsedTransitStop]:
    if not isinstance(step, Mapping):
        return
    mode = _text(step.get("type"))
    if mode not in _TRANSIT_STEP_TYPES:
        return
    route_id = _text(step.get("route_id") or step.get("train_line"))
    direction = _text(step.get("direction"))
    segment_context = _step_segment_context(step)
    for record, local_order in _stop_records(step):
        coordinates = _coords(record)
        if coordinates is None:
            continue
        lat, lon = coordinates
        stop_id, name = _record_stop_identity(record)
        yield _ParsedTransitStop(
            candidate_id=candidate_id,
            mode=mode.lower(),
            route_id=route_id,
            direction=direction,
            segment_context=segment_context,
            stop_id=stop_id,
            name=name,
            latitude=lat,
            longitude=lon,
            stop_order=(step_index * 10000) + local_order,
            step_key=(route_index, step_index),
        )


def _iter_parsed_stops(
    routes: Iterable[Iterable[Mapping[str, Any]]],
    candidate_ids: Iterable[str] | None,
) -> Iterable[_ParsedTransitStop]:
    route_list = list(routes or [])
    supplied_ids = list(candidate_ids or [])
    for route_index, route in enumerate(route_list):
        candidate_id = _text(supplied_ids[route_index] if route_index < len(supplied_ids) else None)
        candidate_id = candidate_id or f"candidate-{route_index}"
        for step_index, step in enumerate(route or []):
            yield from _parse_transit_step(step, candidate_id, step_index, route_index)


class _StopIdentityIndex:
    """Join provider aliases onto one physical stop context."""

    def __init__(self) -> None:
        self._aliases: dict[tuple[str, ...], CandidateStopContext] = {}
        self.contexts: list[CandidateStopContext] = []

    def resolve(
        self,
        stop_id: str | None,
        name: str | None,
        lat: float,
        lon: float,
    ) -> CandidateStopContext:
        keys = _physical_keys(stop_id, name, lat, lon)
        context = next((self._aliases[key] for key in keys if key in self._aliases), None)
        if context is None:
            context = CandidateStopContext(stop_id, name, lat, lon)
            self.contexts.append(context)
        elif context.stop_id is None and stop_id:
            context.stop_id = stop_id
        for key in keys:
            self._aliases[key] = context
        return context


def extract_candidate_stop_context(
    routes: Iterable[Iterable[Mapping[str, Any]]],
    *,
    candidate_ids: Iterable[str] | None = None,
) -> list[CandidateStopContext]:
    """Extract, validate, and deduplicate transit stops across candidates.

    The result retains an association for every candidate/leg that uses a stop,
    including mode, route, direction, ordered position, and the leg endpoints.
    Invalid coordinates are omitted rather than allowed into a local search.
    """
    index = _StopIdentityIndex()
    seen_in_step: set[tuple[tuple[int, int], int]] = set()
    for parsed in _iter_parsed_stops(routes, candidate_ids):
        context = index.resolve(parsed.stop_id, parsed.name, parsed.latitude, parsed.longitude)
        marker = (parsed.step_key, id(context))
        if marker in seen_in_step:
            continue
        seen_in_step.add(marker)
        association = CandidateStopAssociation(
            candidate_route_id=parsed.candidate_id,
            mode=parsed.mode,
            route_id=parsed.route_id,
            direction=parsed.direction,
            stop_order=parsed.stop_order,
            segment_context=parsed.segment_context,
        )
        if association not in context.associations:
            context.associations.append(association)
    return index.contexts
