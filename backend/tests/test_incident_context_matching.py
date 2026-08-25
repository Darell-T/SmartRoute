from datetime import datetime, timedelta, timezone
from math import pi
import unittest

from pydantic import BaseModel

from app.services.trips.route_incidents.context import extract_candidate_stop_context
from app.services.trips.route_incidents.matching import (
    Cached511NYSearchTool,
    MAX_SEARCH_RADIUS_MILES,
    MILES_TO_METERS,
    _geometry_components,
    incident_points,
    match_cached_incidents,
)
from app.services.trips.route_incidents.merge import merge_incident_evidence


class _IncidentModel(BaseModel):
    source_id: str
    latitude: float
    longitude: float
    source: str = "511ny"
    description: str | None = None


class _SnapshotModel(BaseModel):
    incidents: list[dict]
    status: str
    fetched_at: datetime | None = None
    last_successful_fetch_at: datetime | None = None
    source_record_count: int = 0
    nyc_record_count: int = 0
    source_origin: str | None = None


class IncidentGeometryTests(unittest.TestCase):
    def test_geojson_components_preserve_independent_shapes(self):
        self.assertEqual(_geometry_components(None), [])
        self.assertEqual(
            _geometry_components(
                {
                    "type": "GeometryCollection",
                    "geometries": [
                        {"type": "Point", "coordinates": [-73.9, 40.7]},
                        {
                            "type": "LineString",
                            "coordinates": [[-73.8, 40.8], [-73.7, 40.9]],
                        },
                        {"type": "Point", "coordinates": ["bad"]},
                    ],
                }
            ),
            [[(40.7, -73.9)], [(40.8, -73.8), (40.9, -73.7)]],
        )
        self.assertEqual(
            _geometry_components(
                {
                    "type": "MultiPoint",
                    "coordinates": [[-73.6, 40.6], [-73.5, 40.5]],
                }
            ),
            [[(40.6, -73.6), (40.5, -73.5)]],
        )
        self.assertEqual(
            _geometry_components(
                {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[-73.4, 40.4], [-73.3, 40.3]],
                        [["bad"]],
                    ],
                }
            ),
            [[(40.4, -73.4), (40.3, -73.3)]],
        )
        self.assertEqual(
            _geometry_components(
                {
                    "type": "Polygon",
                    "coordinates": [[[-73.2, 40.2], [-73.1, 40.1]]],
                }
            ),
            [[(40.2, -73.2), (40.1, -73.1)]],
        )
        self.assertEqual(
            _geometry_components(
                {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [[[-73.0, 40.0], [-72.9, 39.9]]],
                        "invalid polygon",
                    ],
                }
            ),
            [[(40.0, -73.0), (39.9, -72.9)]],
        )
        self.assertEqual(_geometry_components({"type": "unknown"}), [])


def _routes():
    return [
        [
            {
                "type": "SUBWAY",
                "route_id": "Q",
                "direction": "Coney Island",
                "departure_stop": "Church Av",
                "arrival_stop": "Prospect Park",
                "departure_coords": {"latitude": 40.6500, "longitude": -73.9630},
                "arrival_coords": {"latitude": 40.6610, "longitude": -73.9620},
                "intermediate_stop_locations": [
                    {"id": "D24", "name": "Church Av", "lat": 40.6500, "lng": -73.9630},
                    {"id": "D25", "name": "Parkside Av", "lat": 40.6550, "lng": -73.9625},
                ],
            }
        ],
        [
            {
                "type": "BUS",
                "route_id": "B68",
                "direction": "Coney Island",
                "departure_stop": "Church Av",
                "arrival_stop": "Prospect Park",
                "departure_coords": {"latitude": 40.6500, "longitude": -73.9630},
                "arrival_coords": {"latitude": 40.6610, "longitude": -73.9620},
            }
        ],
    ]


def _encode_polyline(points):
    encoded = []
    previous_latitude = previous_longitude = 0
    for latitude, longitude in points:
        for value, previous in ((round(latitude * 1e5), previous_latitude), (round(longitude * 1e5), previous_longitude)):
            delta = value - previous
            shifted = ~(delta << 1) if delta < 0 else delta << 1
            while shifted >= 0x20:
                encoded.append(chr((0x20 | (shifted & 0x1F)) + 63))
                shifted >>= 5
            encoded.append(chr(shifted + 63))
        previous_latitude, previous_longitude = round(latitude * 1e5), round(longitude * 1e5)
    return "".join(encoded)


class CandidateContextTests(unittest.TestCase):
    def test_extracts_deduplicates_and_reverse_maps_actual_step_shapes(self):
        stops = extract_candidate_stop_context(_routes())
        church = next(stop for stop in stops if stop.stop_name == "Church Av")

        self.assertEqual(church.stop_id, "D24")
        self.assertEqual(church.candidate_route_ids, ["candidate-0", "candidate-1"])
        self.assertEqual(church.modes, ["bus", "subway"])
        self.assertEqual(church.route_ids, ["B68", "Q"])
        self.assertEqual(church.associations[0].direction, "Coney Island")
        self.assertEqual(church.associations[0].segment_context, "Church Av -> Prospect Park")

    def test_invalid_stop_coordinates_are_rejected(self):
        routes = [[{
            "type": "BUS", "route_id": "B1", "departure_stop": "Bad",
            "arrival_stop": "Good",
            "departure_coords": {"latitude": float("nan"), "longitude": -73.9},
            "arrival_coords": {"latitude": 40.7, "longitude": -73.9},
        }]]
        stops = extract_candidate_stop_context(routes)
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].stop_name, "Good")

    def test_intermediate_stops_do_not_discard_or_duplicate_endpoints(self):
        route = _routes()[0][0]
        route["intermediate_stop_locations"] = [route["intermediate_stop_locations"][1]]
        stops = extract_candidate_stop_context([[route]])
        self.assertEqual([stop.stop_name for stop in stops], ["Church Av", "Parkside Av", "Prospect Park"])
        self.assertEqual(len(next(stop for stop in stops if stop.stop_name == "Church Av").associations), 1)


class IncidentMatchingTests(unittest.TestCase):
    def setUp(self):
        self.stops = extract_candidate_stop_context(_routes())

    def test_matches_inside_radius_and_all_candidate_associations(self):
        matches = match_cached_incidents(
            [{"source_id": "near", "latitude": 40.6505, "longitude": -73.9630, "roadway_name": "Ocean Ave"}],
            self.stops,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].nearest_stop.stop_name, "Church Av")
        self.assertEqual(matches[0].affected_candidate_route_ids, ["candidate-0", "candidate-1"])
        self.assertEqual(matches[0].relevance_by_mode["bus"], "potential_bus_corridor")
        self.assertEqual(matches[0].relevance_by_mode["subway"], "nearby_unconfirmed")

    def test_road_closure_affects_bus_without_claiming_subway_operation(self):
        incident = {
            "source_id": "road", "latitude": 40.6502, "longitude": -73.9630,
            "roadway_name": "Ocean Avenue", "is_full_closure": True,
        }
        match = match_cached_incidents([incident], self.stops)[0]
        self.assertEqual(match.impact_scope, "roadway")
        self.assertEqual(match.relevance_by_mode["bus"], "potential_bus_corridor")
        self.assertEqual(match.relevance_by_mode["subway"], "nearby_unconfirmed")
        self.assertIn("bus", match.affected_modes)
        self.assertNotIn("subway", match.affected_modes)

    def test_station_access_closure_is_scoped_to_walk_and_transfer(self):
        incident = {
            "source_id": "access", "latitude": 40.6502, "longitude": -73.9630,
            "description": "Station entrance closed for emergency repairs",
        }
        match = match_cached_incidents([incident], self.stops)[0]
        self.assertEqual(match.impact_scope, "station_access")
        self.assertEqual(match.affected_modes, ["transfer", "walk"])
        self.assertEqual(match.relevance_by_mode["subway"], "station_access_only")

    def test_excludes_outside_and_enforces_maximum_radius(self):
        incident = {"source_id": "far", "latitude": 40.660, "longitude": -73.940}
        self.assertEqual(match_cached_incidents([incident], self.stops), [])
        self.assertEqual(
            match_cached_incidents([incident], self.stops, radius_miles=10, maximum_radius_miles=MAX_SEARCH_RADIUS_MILES),
            [],
        )

    def test_radius_boundary_and_nonfinite_radii_are_safe(self):
        # One degree latitude is earth-radius * pi / 180 metres under the same
        # spherical distance function used by the production helper.
        metres_per_degree = 6_371_008.8 * pi / 180
        boundary_latitude = 40.6500 + (0.5 * MILES_TO_METERS) / metres_per_degree
        boundary = {"source_id": "boundary", "latitude": boundary_latitude, "longitude": -73.9630}
        outside = {"source_id": "outside", "latitude": boundary_latitude + (2 / metres_per_degree), "longitude": -73.9630}
        church_only = [next(stop for stop in self.stops if stop.stop_name == "Church Av")]
        self.assertEqual(len(match_cached_incidents([boundary], church_only)), 1)
        self.assertEqual(match_cached_incidents([outside], church_only), [])
        far = {"source_id": "far", "latitude": 40.660, "longitude": -73.940}
        self.assertEqual(match_cached_incidents([far], self.stops, radius_miles=float("nan")), [])
        self.assertEqual(match_cached_incidents([far], self.stops, radius_miles=float("inf")), [])

    def test_adjacent_stops_report_all_matches_and_the_nearest_stop(self):
        incident = {"source_id": "adjacent", "latitude": 40.6540, "longitude": -73.9626}
        matches = match_cached_incidents([incident], self.stops)
        self.assertEqual(matches[0].nearest_stop.stop_name, "Parkside Av")
        self.assertGreaterEqual(len(matches[0].nearby_stops), 2)

    def test_geometry_and_polyline_can_match_when_primary_coordinate_is_distant(self):
        geometry = {
            "source_id": "line",
            "latitude": 40.70,
            "longitude": -74.05,
            "geometry": {"type": "LineString", "coordinates": [[-73.970, 40.650], [-73.955, 40.650]]},
        }
        matches = match_cached_incidents([geometry], self.stops)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].nearest_stop.match_source, "geometry")

    def test_multiline_components_do_not_create_an_artificial_connecting_segment(self):
        incident = {
            "source_id": "disjoint-lines", "latitude": 40.70, "longitude": -74.05,
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [
                    [[-73.980, 40.645], [-73.980, 40.655]],
                    [[-73.940, 40.645], [-73.940, 40.655]],
                ],
            },
        }
        self.assertEqual(match_cached_incidents([incident], self.stops), [])

    def test_secondary_coordinate_and_model_adapter_are_supported(self):
        model = _IncidentModel(source_id="model", latitude=40.6502, longitude=-73.9631)
        secondary = {"source_id": "second", "latitude": None, "longitude": None, "secondary_latitude": 40.6502, "secondary_longitude": -73.9631}
        matches = match_cached_incidents([model, secondary], self.stops)
        self.assertEqual([match.source_id for match in matches], ["model", "second"])
        self.assertTrue(incident_points(model))

    def test_normalized_geometry_encoded_polyline_is_searchable(self):
        encoded = _encode_polyline([(40.6500, -73.970), (40.6500, -73.955)])
        incident = {
            "source_id": "encoded", "latitude": 40.70, "longitude": -74.05,
            "geometry": {"encoded_polyline": encoded},
        }
        matches = match_cached_incidents([incident], self.stops)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].nearest_stop.match_source, "polyline")

    def test_invalid_incident_coordinates_are_rejected(self):
        self.assertEqual(match_cached_incidents([{"source_id": "bad", "latitude": 0, "longitude": 0}], self.stops), [])

    def test_controlled_tool_rejects_unbounded_or_unsafe_arguments_and_never_needs_upstream(self):
        calls = []

        def snapshot():
            calls.append(True)
            return {"incidents": [{"source_id": "near", "latitude": 40.6502, "longitude": -73.9630}]}

        tool = Cached511NYSearchTool(snapshot, self.stops)
        self.assertEqual(tool.execute({"candidate_route_ids": ["candidate-0"], "url": "https://bad.example"})["status"], "invalid_arguments")
        self.assertEqual(tool.execute({"candidate_route_ids": ["candidate-0"], "radius_miles": 1})["status"], "invalid_arguments")
        self.assertEqual(tool.execute({"candidate_route_ids": ["candidate-0"], "radius_miles": float("nan")})["status"], "invalid_arguments")
        result = tool.execute({"candidate_route_ids": ["candidate-0"], "radius_miles": 0.5})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["incidents"][0]["affected_candidate_route_ids"], ["candidate-0"])

    def test_controlled_tool_propagates_snapshot_metadata_and_unavailable_state(self):
        now = datetime(2026, 7, 22, tzinfo=timezone.utc)
        stale = _SnapshotModel(
            incidents=[{"source_id": "near", "latitude": 40.6502, "longitude": -73.9630}],
            status="stale", fetched_at=now, last_successful_fetch_at=now - timedelta(minutes=20),
            source_record_count=4, nyc_record_count=2, source_origin="fixture",
        )
        result = Cached511NYSearchTool(lambda: stale, self.stops).execute({"candidate_route_ids": ["candidate-0"]})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["snapshot"]["status"], "stale")
        self.assertEqual(result["snapshot"]["nyc_record_count"], 2)
        self.assertEqual(result["snapshot"]["source_origin"], "fixture")
        unavailable = _SnapshotModel(incidents=[], status="unavailable")
        result = Cached511NYSearchTool(lambda: unavailable, self.stops).execute({"candidate_route_ids": ["candidate-0"]})
        self.assertEqual(result, {"incidents": [], "status": "unavailable", "snapshot": {"status": "unavailable", "source_record_count": 0, "nyc_record_count": 0}})


class IncidentMergeTests(unittest.TestCase):
    def test_merges_511ny_with_web_mta_and_vehicle_evidence(self):
        now = datetime(2026, 7, 22, tzinfo=timezone.utc)
        official = {
            "source": "511ny", "source_id": "event-1", "latitude": 40.6500, "longitude": -73.9630,
            "description": "Collision on Ocean Avenue", "reported_at": now.isoformat(),
        }
        related_evidence = [
            {"source": "web", "source_id": "web-1", "latitude": 40.6501, "longitude": -73.9631, "description": "Ocean Avenue collision reported", "observed_at": now.isoformat()},
            {"source": "mta", "source_id": "mta-1", "latitude": 40.6501, "longitude": -73.9631, "description": "Collision on Ocean Avenue causes bus detour", "updated_at": now.isoformat()},
            {"source": "vehicle", "source_id": "vehicle-1", "latitude": 40.6501, "longitude": -73.9631, "description": "Collision on Ocean Avenue observed", "observed_at": now.isoformat()},
        ]
        for evidence in related_evidence:
            with self.subTest(source=evidence["source"]):
                merged = merge_incident_evidence([official, evidence], now=now)
                self.assertEqual(len(merged), 1)
                self.assertEqual(merged[0]["sources"], ["511ny", evidence["source"]])

    def test_same_source_duplicate_id_merges_even_when_provider_fields_change(self):
        duplicate = [
            {"source": "511ny", "source_id": "event-1", "latitude": 40.65, "longitude": -73.96, "description": "Collision"},
            {"source": "511ny", "source_id": "event-1", "latitude": 40.70, "longitude": -74.00, "description": "Updated collision"},
        ]
        merged = merge_incident_evidence(duplicate)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["evidence"]), 1)

    def test_merges_related_evidence_and_prefers_official_coordinates_and_times(self):
        now = datetime(2026, 7, 22, tzinfo=timezone.utc)
        social = {
            "source": "x", "source_id": "post-1", "location": "Ocean Ave",
            "description": "Traffic collision at Ocean Avenue", "observed_at": (now - timedelta(minutes=10)).isoformat(),
        }
        official = {
            "source": "511ny", "source_id": "event-1", "latitude": 40.6502, "longitude": -73.9632,
            "description": "Collision on Ocean Avenue", "roadway_name": "Ocean Avenue", "reported_at": now.isoformat(),
        }
        merged = merge_incident_evidence([social, official], now=now)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["latitude"], 40.6502)
        self.assertEqual(merged[0]["reported_at"], now.isoformat())
        self.assertEqual(merged[0]["sources"], ["511ny", "x"])
        self.assertEqual(len(merged[0]["evidence"]), 2)

    def test_active_road_closure_is_not_treated_as_resolved(self):
        incident = {"source": "511ny", "source_id": "closure", "latitude": 40.65, "longitude": -73.96, "description": "Ocean Avenue closed for emergency repairs", "status": "Active"}
        self.assertEqual([item["source_id"] for item in merge_incident_evidence([incident])], ["closure"])

    def test_active_road_closed_status_text_is_not_terminal(self):
        incident = {"source": "511ny", "source_id": "road-closed", "latitude": 40.65, "longitude": -73.96, "status_text": "Road closed", "description": "Active detour in effect"}
        self.assertEqual([item["source_id"] for item in merge_incident_evidence([incident])], ["road-closed"])

    def test_keeps_separate_nearby_events_and_rejects_resolved_or_stale_social(self):
        now = datetime(2026, 7, 22, tzinfo=timezone.utc)
        first = {"source": "511ny", "source_id": "one", "latitude": 40.65, "longitude": -73.96, "description": "collision on Ocean Avenue"}
        different = {"source": "511ny", "source_id": "two", "latitude": 40.6501, "longitude": -73.9601, "description": "fire at Church Avenue"}
        resolved = {"source": "511ny", "source_id": "old", "latitude": 40.65, "longitude": -73.96, "status": "Resolved"}
        stale_post = {"source": "x", "source_id": "post", "latitude": 40.65, "longitude": -73.96, "description": "collision", "observed_at": (now - timedelta(hours=7)).isoformat()}
        merged = merge_incident_evidence([first, different, resolved, stale_post], now=now)
        self.assertEqual([item["source_id"] for item in merged], ["one", "two"])
