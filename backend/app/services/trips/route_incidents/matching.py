"""Deterministic local matching of cached 511NY incidents to route stops.

No function here fetches 511NY (or accepts URLs, credentials, or arbitrary
geography).  The only searchable geography is the supplied candidate context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import cos, isfinite, radians, sqrt
from typing import Any, Callable, Iterable, Mapping

from app.services.trips.route_incidents.context import CandidateStopContext, valid_coordinate_pair
from app.services.geography import distance_meters


MILES_TO_METERS = 1609.344
DEFAULT_SEARCH_RADIUS_MILES = 0.5
MAX_SEARCH_RADIUS_MILES = 0.5
MAX_NEARBY_STOP_MATCHES = 8
MAX_TOOL_INCIDENTS = 50

LOCAL_511NY_SEARCH_TOOL_SCHEMA = {
    "name": "search_cached_511ny_incidents",
    "description": "Search the current locally cached 511NY snapshot near the current route candidates. No upstream request is made.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_route_ids"],
        "properties": {
            "candidate_route_ids": {
                "type": "array", "minItems": 1, "maxItems": 12,
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
            },
            "radius_miles": {
                "type": "number", "minimum": 0.01, "maximum": MAX_SEARCH_RADIUS_MILES,
            },
        },
    },
}


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        candidate = dump()
        return candidate if isinstance(candidate, Mapping) else None
    legacy = getattr(value, "dict", None)
    if callable(legacy):
        candidate = legacy()
        return candidate if isinstance(candidate, Mapping) else None
    return None


def _coordinates(item: Mapping[str, Any], prefix: str = "") -> tuple[float, float] | None:
    return valid_coordinate_pair(
        item.get(f"{prefix}latitude"),
        item.get(f"{prefix}longitude", item.get(f"{prefix}lon")),
    )


def _geometry_components(value: object) -> list[list[tuple[float, float]]]:
    """Extract GeoJSON components without joining independent line strings."""
    if not isinstance(value, Mapping):
        return []
    geometry_type = str(value.get("type") or "").lower()
    if geometry_type == "geometrycollection":
        components: list[list[tuple[float, float]]] = []
        for geometry in value.get("geometries") or []:
            components.extend(_geometry_components(geometry))
        return components
    coordinates = value.get("coordinates")
    if geometry_type == "point":
        point = _geojson_point(coordinates)
        return [[point]] if point else []
    if geometry_type in {"linestring", "multipoint"}:
        return [_geojson_line(coordinates)] if _geojson_line(coordinates) else []
    if geometry_type in {"multilinestring", "polygon"} and isinstance(coordinates, (list, tuple)):
        return [line for item in coordinates if (line := _geojson_line(item))]
    if geometry_type == "multipolygon" and isinstance(coordinates, (list, tuple)):
        return [line for polygon in coordinates if isinstance(polygon, (list, tuple)) for ring in polygon if (line := _geojson_line(ring))]
    return []


def _geojson_point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return valid_coordinate_pair(value[1], value[0])


def _geojson_line(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [point for item in value if (point := _geojson_point(item))]


def _geometry_points(value: object) -> list[tuple[float, float]]:
    return [point for component in _geometry_components(value) for point in component]


def _decode_polyline(value: object) -> list[tuple[float, float]]:
    """Decode a Google-style encoded polyline; malformed input yields []."""
    if not isinstance(value, str) or not value or len(value) > 20000:
        return []
    coordinates: list[tuple[float, float]] = []
    index = latitude = longitude = 0
    try:
        while index < len(value):
            decoded: list[int] = []
            for _ in range(2):
                shift = result = 0
                while True:
                    byte = ord(value[index]) - 63
                    index += 1
                    result |= (byte & 0x1F) << shift
                    shift += 5
                    if byte < 0x20:
                        break
                decoded.append(~(result >> 1) if result & 1 else result >> 1)
            latitude += decoded[0]
            longitude += decoded[1]
            point = valid_coordinate_pair(latitude / 1e5, longitude / 1e5)
            if point is not None:
                coordinates.append(point)
    except (IndexError, ValueError):
        return []
    return coordinates


def incident_points(incident: object) -> list[tuple[float, float]]:
    """Return primary, secondary, geometry, and encoded-polyline coordinates."""
    item = _as_mapping(incident)
    if item is None:
        return []
    points = [point for point in (_coordinates(item), _coordinates(item, "secondary_")) if point]
    geometry = item.get("geometry")
    points.extend(_geometry_points(geometry))
    for field in ("encoded_polyline", "polyline"):
        points.extend(_decode_polyline(item.get(field)))
    if isinstance(geometry, Mapping):
        points.extend(_decode_polyline(geometry.get("encoded_polyline")))
    return list(dict.fromkeys(points))


def _point_to_segment_meters(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Local equirectangular point-to-segment distance, accurate at NYC scale."""
    reference_lat = radians((point[0] + a[0] + b[0]) / 3)
    scale_x = 111_320 * cos(reference_lat)
    scale_y = 110_574
    px, py = (point[1] - a[1]) * scale_x, (point[0] - a[0]) * scale_y
    bx, by = (b[1] - a[1]) * scale_x, (b[0] - a[0]) * scale_y
    length_sq = bx * bx + by * by
    if length_sq == 0:
        return sqrt(px * px + py * py)
    factor = max(0.0, min(1.0, (px * bx + py * by) / length_sq))
    dx, dy = px - factor * bx, py - factor * by
    return sqrt(dx * dx + dy * dy)


def _nearest_distance_meters(stop: CandidateStopContext, incident: object) -> tuple[float, str] | None:
    item = _as_mapping(incident)
    if item is None:
        return None
    point_candidates = [(point, "point") for point in incident_points(item)]
    if not point_candidates:
        return None
    distances = [(distance_meters(stop.latitude, stop.longitude, point[0], point[1]), kind) for point, kind in point_candidates]
    geometry_components = _geometry_components(item.get("geometry"))
    for component in geometry_components:
        distances.extend(
            (_point_to_segment_meters((stop.latitude, stop.longitude), a, b), "geometry")
            for a, b in zip(component, component[1:])
        )
    geometry = item.get("geometry")
    encoded_value = item.get("encoded_polyline") or item.get("polyline")
    if not encoded_value and isinstance(geometry, Mapping):
        encoded_value = geometry.get("encoded_polyline")
    encoded_points = _decode_polyline(encoded_value)
    if len(encoded_points) > 1:
        distances.extend(
            (_point_to_segment_meters((stop.latitude, stop.longitude), a, b), "polyline")
            for a, b in zip(encoded_points, encoded_points[1:])
        )
    return min(distances, key=lambda item: item[0])


def _roadway_incident(item: Mapping[str, Any]) -> bool:
    if item.get("roadway_name"):
        return True
    haystack = " ".join(str(item.get(key) or "") for key in ("roadway_name", "lanes_affected", "event_type", "event_subtype", "description"))
    return bool(item.get("is_full_closure")) or any(word in haystack.casefold() for word in ("road", "traffic", "lane", "closure", "collision"))


def _station_access_incident(item: Mapping[str, Any]) -> bool:
    haystack = " ".join(str(item.get(key) or "") for key in ("event_type", "event_subtype", "description", "comment"))
    terms = ("station access", "station entrance", "station exit", "station stair", "elevator access", "entrance closed", "exit closed")
    return any(term in haystack.casefold() for term in terms)


def _bounded_text(value: object, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _snapshot_metadata(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a small, JSON-safe snapshot status record for the model."""
    if snapshot is None:
        return {"status": "fresh"}
    metadata: dict[str, Any] = {
        "status": str(snapshot.get("status") or "fresh").lower(),
        "source_record_count": snapshot.get("source_record_count"),
        "nyc_record_count": snapshot.get("nyc_record_count"),
    }
    source_origin = snapshot.get("source_origin")
    if source_origin in {"live", "fixture"}:
        metadata["source_origin"] = source_origin
    for key in ("fetched_at", "last_successful_fetch_at"):
        value = snapshot.get(key)
        if value is None:
            continue
        metadata[key] = value.isoformat() if hasattr(value, "isoformat") else str(value)[:64]
    return {key: value for key, value in metadata.items() if value is not None}


@dataclass(frozen=True)
class IncidentStopMatch:
    stop_id: str | None
    stop_name: str | None
    distance_meters: float
    match_source: str
    candidate_route_ids: list[str]
    modes: list[str]


@dataclass(frozen=True)
class MatchedIncident:
    source_id: str
    source: str
    event_type: str | None
    description: str | None
    severity: str | None
    roadway_name: str | None
    nearest_stop: IncidentStopMatch
    nearby_stops: list[IncidentStopMatch]
    affected_candidate_route_ids: list[str]
    affected_modes: list[str]
    relevance_by_mode: dict[str, str]
    impact_scope: str = "nearby"

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["nearest_stop"]["distance_meters"] = round(result["nearest_stop"]["distance_meters"], 1)
        for stop in result["nearby_stops"]:
            stop["distance_meters"] = round(stop["distance_meters"], 1)
        return result


def _bounded_radius(requested_radius_miles: object, maximum_radius_miles: float) -> float:
    try:
        radius = float(requested_radius_miles)
    except (TypeError, ValueError):
        radius = DEFAULT_SEARCH_RADIUS_MILES
    if not isfinite(radius) or radius <= 0:
        radius = DEFAULT_SEARCH_RADIUS_MILES
    return min(radius, maximum_radius_miles)


def match_cached_incidents(
    incidents: Iterable[object],
    stops: Iterable[CandidateStopContext],
    *,
    candidate_route_ids: Iterable[str] | None = None,
    radius_miles: object = DEFAULT_SEARCH_RADIUS_MILES,
    maximum_radius_miles: float = MAX_SEARCH_RADIUS_MILES,
) -> list[MatchedIncident]:
    """Match a cached incident collection against candidate stops only.

    A requested radius can never exceed ``maximum_radius_miles``.  One result
    is emitted per incident, with its nearest stop and all matching candidates.
    """
    try:
        maximum = float(maximum_radius_miles)
    except (TypeError, ValueError):
        maximum = MAX_SEARCH_RADIUS_MILES
    if not isfinite(maximum) or maximum <= 0:
        maximum = MAX_SEARCH_RADIUS_MILES
    maximum = max(0.01, maximum)
    radius_meters = _bounded_radius(radius_miles, maximum) * MILES_TO_METERS
    requested_ids = {str(value) for value in candidate_route_ids or [] if str(value)}
    scoped_stops = [
        stop for stop in stops
        if not requested_ids or requested_ids.intersection(stop.candidate_route_ids)
    ]
    results: list[MatchedIncident] = []
    for raw_incident in incidents or []:
        item = _as_mapping(raw_incident)
        if item is None or not incident_points(item):
            continue
        matches: list[IncidentStopMatch] = []
        for stop in scoped_stops:
            nearest = _nearest_distance_meters(stop, item)
            if nearest is None or nearest[0] > radius_meters:
                continue
            associations = [
                association for association in stop.associations
                if not requested_ids or association.candidate_route_id in requested_ids
            ]
            matches.append(IncidentStopMatch(
                stop_id=stop.stop_id,
                stop_name=stop.stop_name,
                distance_meters=nearest[0],
                match_source=nearest[1],
                candidate_route_ids=sorted({item.candidate_route_id for item in associations}),
                modes=sorted({item.mode for item in associations if item.mode}),
            ))
        if not matches:
            continue
        matches.sort(key=lambda match: (match.distance_meters, match.stop_name or "", match.stop_id or ""))
        modes = sorted({mode for match in matches for mode in match.modes})
        roadway = _roadway_incident(item)
        station_access = _station_access_incident(item)
        if station_access:
            relevance = {mode: "station_access_only" for mode in modes}
            affected_modes = ["transfer", "walk"]
            impact_scope = "station_access"
        elif roadway:
            relevance = {
                mode: "potential_bus_corridor" if mode == "bus" else "nearby_unconfirmed"
                for mode in modes
            }
            affected_modes = ["bus", "walk"] if "bus" in modes else ["walk"]
            impact_scope = "roadway"
        else:
            relevance = {mode: "nearby" for mode in modes}
            affected_modes = modes
            impact_scope = "nearby"
        results.append(MatchedIncident(
            source_id=_bounded_text(item.get("source_id") or item.get("id"), 120) or "unknown",
            source=_bounded_text(item.get("source"), 32) or "511ny",
            event_type=_bounded_text(item.get("event_type"), 80),
            description=_bounded_text(item.get("description") or item.get("comment"), 500),
            severity=_bounded_text(item.get("severity_normalized") or item.get("severity_raw"), 32),
            roadway_name=_bounded_text(item.get("roadway_name"), 120),
            nearest_stop=matches[0],
            nearby_stops=matches[:MAX_NEARBY_STOP_MATCHES],
            affected_candidate_route_ids=sorted({candidate for match in matches for candidate in match.candidate_route_ids}),
            affected_modes=affected_modes,
            relevance_by_mode=relevance,
            impact_scope=impact_scope,
        ))
    return sorted(results, key=lambda result: (result.nearest_stop.distance_meters, result.source_id))


class Cached511NYSearchTool:
    """Validated adapter around a local snapshot getter; never calls upstream."""

    schema = LOCAL_511NY_SEARCH_TOOL_SCHEMA

    def __init__(self, snapshot_getter: Callable[[], object], stops: Iterable[CandidateStopContext]):
        self._snapshot_getter = snapshot_getter
        self._stops = list(stops)

    def execute(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) - {"candidate_route_ids", "radius_miles"}:
            return {"incidents": [], "status": "invalid_arguments"}
        ids = arguments.get("candidate_route_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 12 or any(not isinstance(value, str) or not value.strip() or len(value) > 80 for value in ids):
            return {"incidents": [], "status": "invalid_arguments"}
        radius = arguments.get("radius_miles", DEFAULT_SEARCH_RADIUS_MILES)
        try:
            radius = float(radius)
        except (TypeError, ValueError):
            return {"incidents": [], "status": "invalid_arguments"}
        if not isfinite(radius) or not 0 < radius <= MAX_SEARCH_RADIUS_MILES:
            return {"incidents": [], "status": "invalid_arguments"}
        snapshot = self._snapshot_getter()
        snapshot_mapping = _as_mapping(snapshot)
        snapshot_metadata = _snapshot_metadata(snapshot_mapping)
        snapshot_status = snapshot_metadata["status"]
        if snapshot_status not in {"fresh", "stale", "unavailable"}:
            return {"incidents": [], "status": "unavailable", "snapshot": {"status": "unavailable"}}
        if snapshot_status == "unavailable":
            return {"incidents": [], "status": "unavailable", "snapshot": snapshot_metadata}
        records = snapshot_mapping.get("incidents", []) if snapshot_mapping else snapshot
        if not isinstance(records, list):
            return {"incidents": [], "status": "unavailable", "snapshot": {"status": "unavailable"}}
        matches = match_cached_incidents(records, self._stops, candidate_route_ids=ids, radius_miles=radius)
        return {
            "incidents": [match.as_dict() for match in matches[:MAX_TOOL_INCIDENTS]],
            "status": "complete",
            "snapshot": snapshot_metadata,
            "truncated": len(matches) > MAX_TOOL_INCIDENTS,
        }
