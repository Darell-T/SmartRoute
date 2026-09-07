"""Candidate-aware stop context for local incident searches.

This module intentionally works on the parsed Google Routes dictionaries used
by both trip entry points.  It does not enrich a route or contact GTFS/any
upstream service; callers may pass already-enriched intermediate stops.
"""

from __future__ import annotations

import hashlib
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


def stop_reference(
    stop_id: str | None,
    stop_name: str | None,
    latitude: float,
    longitude: float,
) -> str:
    """Return a deterministic opaque reference for one physical stop.

    Physical stop IDs are the preferred identity.  Some route providers omit
    them, so the fallback includes the normalized name and five-decimal
    coordinate pair used elsewhere to distinguish a physical platform.
    """
    physical_id = _text(stop_id)
    if physical_id:
        material = f"id:{physical_id.casefold()}"
    else:
        name = re.sub(r"[^a-z0-9]+", "", (_text(stop_name) or "").casefold())
        material = f"point:{name}:{latitude:.5f}:{longitude:.5f}"
    return "sr_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


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

    @property
    def directions(self) -> list[str]:
        return sorted({item.direction for item in self.associations if item.direction})

    @property
    def stop_reference(self) -> str:
        return stop_reference(self.stop_id, self.stop_name, self.latitude, self.longitude)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stop_id": self.stop_id,
            "stop_ref": self.stop_reference,
            "stop_name": self.stop_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "modes": self.modes,
            "route_ids": self.route_ids,
            "candidate_route_ids": self.candidate_route_ids,
            "directions": self.directions,
            "associations": [
                {
                    "candidate_route_id": association.candidate_route_id,
                    "mode": association.mode,
                    "route_id": association.route_id,
                    "direction": association.direction,
                    "stop_order": association.stop_order,
                    "segment_context": association.segment_context,
                }
                for association in self.associations
            ],
        }


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
    route_list = list(routes or [])
    supplied_ids = list(candidate_ids or [])
    aliases: dict[tuple[str, ...], CandidateStopContext] = {}
    contexts: list[CandidateStopContext] = []
    for route_index, route in enumerate(route_list):
        candidate_id = _text(supplied_ids[route_index] if route_index < len(supplied_ids) else None)
        candidate_id = candidate_id or f"candidate-{route_index}"
        for step_index, step in enumerate(route or []):
            if not isinstance(step, Mapping):
                continue
            mode = _text(step.get("type"))
            if mode not in {"SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL"}:
                continue
            route_id = _text(step.get("route_id") or step.get("train_line"))
            direction = _text(step.get("direction"))
            departure, arrival = _text(step.get("departure_stop")), _text(step.get("arrival_stop"))
            segment_context = " -> ".join(item for item in (departure, arrival) if item) or None
            seen_in_step: set[int] = set()
            for record, local_order in _stop_records(step):
                coordinates = _coords(record)
                if coordinates is None:
                    continue
                lat, lon = coordinates
                stop_id = _text(record.get("stop_id") or record.get("id"))
                name = _text(record.get("name") or record.get("stop_name"))
                context = _resolve_stop_context(
                    stop_id, name, lat, lon, aliases, contexts
                )
                if id(context) in seen_in_step:
                    continue
                seen_in_step.add(id(context))
                association = CandidateStopAssociation(
                    candidate_route_id=candidate_id,
                    mode=mode.lower(),
                    route_id=route_id,
                    direction=direction,
                    stop_order=(step_index * 10000) + local_order,
                    segment_context=segment_context,
                )
                if association not in context.associations:
                    context.associations.append(association)
    return contexts


def _resolve_stop_context(
    stop_id: str | None,
    name: str | None,
    lat: float,
    lon: float,
    aliases: dict[tuple[str, ...], CandidateStopContext],
    contexts: list[CandidateStopContext],
) -> CandidateStopContext:
    keys = _physical_keys(stop_id, name, lat, lon)
    context = next((aliases[key] for key in keys if key in aliases), None)
    if context is None:
        context = CandidateStopContext(stop_id, name, lat, lon)
        contexts.append(context)
    elif context.stop_id is None and stop_id:
        context.stop_id = stop_id
    for key in keys:
        aliases[key] = context
    return context
