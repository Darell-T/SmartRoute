"""Focused tests for server-owned transit transfer semantics."""

from __future__ import annotations

import unittest

from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex
from app.services.trips import itinerary, scoring
from app.services.trips.transfer_semantics import (
    normalize_route,
    route_accessibility,
    route_transfer_facts,
    route_walking_totals,
)


def _transit(
    route_id: str,
    *,
    arrival_stop: str = "Arrival",
    departure_stop: str = "Departure",
    arrival_stop_id: str | None = None,
    departure_stop_id: str | None = None,
    arrival_coords: dict | None = None,
    departure_coords: dict | None = None,
    **extra,
) -> dict:
    return {
        "type": "SUBWAY",
        "route_id": route_id,
        "arrival_stop": arrival_stop,
        "departure_stop": departure_stop,
        "arrival_stop_id": arrival_stop_id,
        "departure_stop_id": departure_stop_id,
        "arrival_coords": arrival_coords,
        "departure_coords": departure_coords,
        **extra,
    }


def _walk(seconds: int, **extra) -> dict:
    return {"type": "WALK", "duration_seconds": seconds, **extra}


class _StopLookup:
    def __init__(self, details: dict[str, dict]) -> None:
        self.details = details

    def get_stop_locations(self, stop_ids: list[str]) -> dict[str, dict]:
        return {stop_id: self.details.get(stop_id, {}) for stop_id in stop_ids}


class TransferSemanticsTests(unittest.TestCase):
    def test_exact_stop_parent_complex_and_street_relationships(self):
        cases = (
            (
                "same_platform",
                _transit("A", arrival_stop_id="A01N"),
                _transit("B", departure_stop_id="A01N"),
            ),
            (
                "same_station",
                _transit("A", arrival_stop_id="A01"),
                _transit("B", departure_stop_id="A02"),
            ),
            (
                "station_complex",
                _transit("A", arrival_stop_id="A01"),
                _transit("B", departure_stop_id="B01"),
            ),
            (
                "street_transfer",
                _transit(
                    "A",
                    arrival_stop="34 St",
                    arrival_coords={"latitude": 40.75, "longitude": -73.99},
                ),
                _transit(
                    "B",
                    departure_stop="34 St",
                    departure_coords={"latitude": 40.752, "longitude": -73.99},
                ),
            ),
            (
                "street_transfer",
                _transit("A", arrival_stop="Union Sq"),
                _transit("B", departure_stop="Union Square"),
            ),
        )
        gtfs = _StopLookup(
            {
                "A01": {"parent_station": "P1", "station_complex_id": "C1"},
                "A02": {"parent_station": "P1", "station_complex_id": "C1"},
                "B01": {"parent_station": "P2", "station_complex_id": "C1"},
            }
        )
        for expected, previous, following in cases:
            with self.subTest(expected=expected):
                route = [previous, _walk(45), following]
                normalize_route(route, gtfs)
                self.assertEqual(route_transfer_facts(route)[0]["kind"], expected)

    def test_multiple_walk_fragments_are_one_semantic_transfer(self):
        route = [
            _transit("A", arrival_stop_id="A01N", arrival_time_iso="2026-08-08T10:00:00-04:00"),
            _walk(30),
            _walk(45),
            _transit("B", departure_stop_id="A01N", departure_time_iso="2026-08-08T10:02:00-04:00"),
        ]
        normalize_route(route)
        facts = route_transfer_facts(route)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["kind"], "same_platform")
        self.assertEqual(facts[0]["fragment_count"], 2)
        self.assertEqual(facts[0]["total_seconds"], 75)
        self.assertEqual(route_walking_totals(route), (0, 75))
        self.assertTrue(route[2]["semantic_transfer_fragment"])

    def test_nearby_unlinked_stops_remain_a_street_transfer(self):
        route = [
            _transit(
                "A",
                arrival_stop="Lexington Ave",
                arrival_coords={"latitude": 40.7500, "longitude": -73.9900},
            ),
            _walk(45),
            _transit(
                "B",
                departure_stop="Lexington Avenue",
                departure_coords={"latitude": 40.7504, "longitude": -73.9900},
            ),
        ]
        normalize_route(route)
        self.assertEqual(route_transfer_facts(route)[0]["kind"], "street_transfer")

    def test_transfer_fact_retains_authoritative_station_labels(self):
        route = [
            _transit("A", arrival_stop_id="A01", arrival_stop="14 St"),
            _walk(30),
            _transit("F", departure_stop_id="A01", departure_stop="14 St"),
        ]
        normalize_route(route)
        fact = route_transfer_facts(route)[0]
        self.assertEqual(fact["from_station_label"], "14 St")
        self.assertEqual(fact["to_station_label"], "14 St")

    def test_origin_destination_walks_remain_ordinary_street_walking(self):
        route = [_walk(60), _transit("Q"), _walk(120)]
        normalize_route(route)
        self.assertEqual(route_transfer_facts(route), [])
        self.assertEqual(route_walking_totals(route), (180, 0))

    def test_accessibility_is_propagated_but_same_station_is_not_proof(self):
        accessible = [
            _transit("A", arrival_stop_id="A01", arrival_accessible=True),
            _walk(20),
            _transit("B", departure_stop_id="A01", departure_accessible=True),
        ]
        normalize_route(accessible)
        self.assertEqual(route_accessibility(accessible), "accessible")

        inaccessible = [
            _transit("A", arrival_stop_id="A01", arrival_accessible=False),
            _walk(20),
            _transit("B", departure_stop_id="A01", departure_accessible=True),
        ]
        normalize_route(inaccessible)
        self.assertEqual(route_accessibility(inaccessible), "inaccessible")

        unknown = [_transit("A", arrival_stop_id="A01"), _walk(20), _transit("B", departure_stop_id="A01")]
        normalize_route(unknown)
        self.assertEqual(route_accessibility(unknown), "unknown")

    def test_canonical_and_scoring_surfaces_semantic_totals(self):
        route = [
            _transit(
                "A",
                arrival_stop_id="A01N",
                departure_time_iso="2026-08-08T10:00:00-04:00",
                arrival_time_iso="2026-08-08T10:10:00-04:00",
            ),
            _walk(90),
            _transit(
                "B",
                departure_stop_id="A01N",
                departure_time_iso="2026-08-08T10:12:00-04:00",
                arrival_time_iso="2026-08-08T10:22:00-04:00",
            ),
        ]
        normalize_route(route)
        canonical = itinerary.build_canonical_itinerary(
            route,
            origin={"label": "A"},
            destination={"label": "B"},
        )
        self.assertEqual(canonical["total_walk_seconds"], 0)
        self.assertEqual(canonical["total_street_walking_seconds"], 0)
        self.assertEqual(canonical["total_in_station_transfer_seconds"], 90)
        self.assertEqual(canonical["legs"][1]["transfer_kind"], "same_platform")
        self.assertEqual(canonical["legs"][1]["transfer_seconds"], 90)

        scored = scoring._route_score(route, [], routing_preference="LESS_WALKING")
        self.assertEqual(scored["in_station_transfer_seconds"], 90)
        self.assertEqual(scored["walking_penalty"], 0)

        street_route = [_transit("A"), _walk(90), _transit("B")]
        street_route[1]["start_point"] = {"latitude": 40.70, "longitude": -74.0}
        street_route[1]["end_point"] = {"latitude": 40.71, "longitude": -74.0}
        normalize_route(street_route)
        street_score = scoring._route_score(
            street_route,
            [],
            routing_preference="LESS_WALKING",
        )
        self.assertGreater(street_score["street_walking_seconds"], 0)
        self.assertGreater(street_score["walking_penalty"], 0)

    def test_index_identity_classifies_platform_parent_and_component(self):
        # Pattern index carries canonical parent identity + GTFS transfer
        # components (as the regenerated stop_patterns.json does).
        artifact = {
            "stops": {
                "R14": {"name": "14 St-Union Sq", "station_complex_id": "gtfs_transfer:635"},
                "R16": {"name": "Times Sq-42 St", "station_complex_id": "gtfs_transfer:127"},
                "A27": {"name": "42 St-Port Authority Bus Terminal", "station_complex_id": "gtfs_transfer:127"},
                "128": {"name": "34 St-Penn Station"},
                "A28": {"name": "34 St-Penn Station"},
            },
            "patterns": [],
        }
        index = StopPatternIndex(artifact)
        gtfs = type(
            "IndexOnlyGtfs",
            (),
            {
                "_pattern_index": index,
                "get_stop_locations": lambda stop_ids: (_ for _ in ()).throw(
                    AssertionError("request-time DB lookup must not run")
                ),
            },
        )()

        cases = (
            # Exact authoritative platform id equality.
            ("same_platform", "R14N", "R14N"),
            # Different platforms of the same canonical parent.
            ("same_station", "R14N", "R14S"),
            # Equal canonical PARENT ids are same_station, never same_platform.
            ("same_station", "R14", "R14"),
            # Different parents in one explicit GTFS transfer component.
            ("station_complex", "R16", "A27"),
            # Same-name but unlinked parents stay a street transfer.
            ("street_transfer", "128", "A28"),
            # Unknown ids degrade to unknown identity, not a same-platform claim.
            ("street_transfer", "UNKNOWN", "UNKNOWN"),
        )
        for expected, from_id, to_id in cases:
            with self.subTest(expected=expected, from_id=from_id, to_id=to_id):
                route = [
                    _transit("A", arrival_stop_id=from_id, arrival_stop="from"),
                    _walk(45),
                    _transit("B", departure_stop_id=to_id, departure_stop="to"),
                ]
                normalize_route(route, gtfs)
                self.assertEqual(route_transfer_facts(route)[0]["kind"], expected)

    def test_pattern_index_present_never_falls_back_to_db_lookup(self):
        artifact = {
            "stops": {
                "R14": {"name": "14 St-Union Sq", "station_complex_id": "gtfs_transfer:635"},
            },
            "patterns": [],
        }
        index = StopPatternIndex(artifact)
        gtfs = type(
            "IndexOnlyGtfs",
            (),
            {
                "_pattern_index": index,
                "get_stop_locations": lambda stop_ids: (_ for _ in ()).throw(
                    AssertionError("request-time DB lookup must not run")
                ),
            },
        )()
        route = [
            _transit("A", arrival_stop_id="R14N", arrival_stop="14 St"),
            _walk(45),
            _transit("B", departure_stop_id="R14N", departure_stop="14 St"),
        ]
        normalize_route(route, gtfs)  # must not raise
        fact = route_transfer_facts(route)[0]
        self.assertEqual(fact["kind"], "same_platform")
        self.assertEqual(fact["from_parent_station"], "R14")
        self.assertEqual(fact["from_station_label"], "14 St")

    def test_complex_identity_does_not_invent_accessibility(self):
        artifact = {
            "stops": {
                "R16": {"name": "Times Sq-42 St", "station_complex_id": "gtfs_transfer:127"},
                "A27": {"name": "42 St-Port Authority Bus Terminal", "station_complex_id": "gtfs_transfer:127"},
            },
            "patterns": [],
        }
        gtfs = type(
            "IndexOnlyGtfs",
            (),
            {
                "_pattern_index": StopPatternIndex(artifact),
                "get_stop_locations": lambda stop_ids: (_ for _ in ()).throw(
                    AssertionError("request-time DB lookup must not run")
                ),
            },
        )()
        route = [
            _transit("A", arrival_stop_id="R16"),
            _walk(45),
            _transit("B", departure_stop_id="A27"),
        ]
        normalize_route(route, gtfs)
        fact = route_transfer_facts(route)[0]
        self.assertEqual(fact["kind"], "station_complex")
        # No accessibility evidence in the artifact: remains unknown.
        self.assertEqual(fact["accessibility"], "unknown")
        self.assertEqual(route_accessibility(route), "unknown")

    def test_real_artifact_resolver_parents_keep_component_identity(self):
        # Production path: provider steps carry no stop ids, so the
        # route-pattern resolver adds canonical PARENT ids plus parent
        # markers. Those resolver parents must still receive their GTFS
        # transfer-component identity from the in-memory index (N arrival R16
        # -> A departure A27, both in component gtfs_transfer:127) with no
        # request-time DB lookup.
        index = StopPatternIndex.load()
        gtfs = type(
            "IndexOnlyGtfs",
            (),
            {
                "_pattern_index": index,
                "get_stop_locations": lambda stop_ids: (_ for _ in ()).throw(
                    AssertionError("request-time DB lookup must not run")
                ),
            },
        )()
        route = [
            {
                "type": "SUBWAY",
                "route_id": "N",
                "departure_stop": "34 St-Herald Sq",
                "arrival_stop": "Times Sq-42 St",
            },
            _walk(300),
            {
                "type": "SUBWAY",
                "route_id": "A",
                "departure_stop": "42 St-Port Authority Bus Terminal",
                "arrival_stop": "59 St-Columbus Circle",
            },
        ]
        normalize_route(route, gtfs)  # must not call get_stop_locations
        self.assertEqual(route[0]["arrival_stop_id"], "R16")
        self.assertEqual(route[2]["departure_stop_id"], "A27")
        self.assertIs(route[0]["arrival_stop_is_parent"], True)
        self.assertIs(route[2]["departure_stop_is_parent"], True)
        fact = route_transfer_facts(route)[0]
        self.assertEqual(fact["kind"], "station_complex")
        self.assertEqual(fact["street_walking_seconds"], 0)
        self.assertEqual(fact["in_station_transfer_seconds"], 300)
        self.assertEqual(route_walking_totals(route), (0, 300))


if __name__ == "__main__":
    unittest.main()
