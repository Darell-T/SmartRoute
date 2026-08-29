from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest
from app.services.mta.config import get_route_color
from app.services.mta.feeds import _gtfs_realtime_pb2
from app.services.mta.subway import (
    build_subway_vehicle_positions,
    parse_vehicle_positions,
)

NOW = 1_800_000_000
DEBUG_KEYS = {
    "scope",
    "requested_routes",
    "feeds_ok",
    "feed_failures",
    "entities",
    "trip_updates",
    "vehicle_entities",
    "vehicles_with_position",
    "vehicles_without_position",
    "zero_coordinates",
    "raw_positions",
    "final_markers",
    "stop_only_candidates",
    "feeds",
}


def _feed(entities: list[dict]) -> bytes:
    pb = _gtfs_realtime_pb2()
    message = pb.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    for spec in entities:
        entity = message.entity.add()
        entity.id = spec.get("id") or ""
        if spec.get("trip_update"):
            entity.trip_update.trip.trip_id = spec["trip_update"]
        if spec.get("no_vehicle"):
            continue
        vehicle = entity.vehicle
        vehicle.trip.trip_id = spec.get("trip_id", "")
        vehicle.trip.route_id = spec.get("route_id", "")
        vehicle.stop_id = spec.get("stop_id", "")
        if "timestamp" in spec:
            vehicle.timestamp = spec["timestamp"]
        if "sequence" in spec:
            vehicle.current_stop_sequence = spec["sequence"]
        if spec.get("has_position", True):
            vehicle.position.latitude = spec.get("lat", 40.75)
            vehicle.position.longitude = spec.get("lng", -73.99)
    return message.SerializeToString()


def _raw(suffix: str, entities: list[dict]) -> dict:
    return {"content": _feed(entities), "suffix": suffix}


def _build(
    raw_feeds: list[dict],
    *,
    requested_set: set[str] | None = None,
    route_ids=None,
    debug: bool = True,
    include_stop_only: bool = True,
    now: int = NOW,
):
    with patch("app.services.mta.subway.datetime") as mocked:
        mocked.now.return_value.timestamp.return_value = now
        return build_subway_vehicle_positions(
            raw_feeds,
            set() if requested_set is None else requested_set,
            route_ids,
            debug,
            include_stop_only,
        )


class SubwayVehicleBuildingTests(unittest.TestCase):
    def test_debug_true_returns_vehicles_and_payload_false_returns_vehicles_only(self):
        feeds = [_raw("nqrw", [{"id": "q1", "route_id": "Q", "trip_id": "t"}])]
        vehicles, debug = _build(feeds, requested_set={"Q"}, route_ids={"Q"})
        assert isinstance(vehicles, list)
        assert set(debug) == DEBUG_KEYS
        only = _build(feeds, requested_set={"Q"}, route_ids={"Q"}, debug=False)
        assert isinstance(only, list)
        assert only == vehicles

    def test_route_filter_is_exact_string_match_without_uppercasing(self):
        feeds = [_raw("nqrw", [
            {"id": "q1", "route_id": "Q"},
            {"id": "n1", "route_id": "N"},
            {"id": "q-lower", "route_id": "q"},
        ])]
        vehicles, _debug = _build(feeds, requested_set={"Q"}, route_ids={"Q"})
        assert [row["id"] for row in vehicles] == ["q1"]

    def test_zero_coordinate_rows_are_parsed_then_dropped_and_counted(self):
        raw = _feed([
            {"id": "zero", "route_id": "Q", "lat": 0, "lng": 0},
            {"id": "one-axis", "route_id": "Q", "lat": 0, "lng": -73.99},
        ])
        parsed = parse_vehicle_positions(raw)
        assert [row["id"] for row in parsed] == ["zero", "one-axis"]
        vehicles, debug = _build(
            [{"content": raw, "suffix": "nqrw"}],
            requested_set={"Q"},
            route_ids={"Q"},
        )
        assert [row["id"] for row in vehicles] == ["one-axis"]
        assert debug["zero_coordinates"] == 1
        assert debug["raw_positions"] == 2
        assert debug["final_markers"] == 1

    def test_missing_route_is_parsed_then_dropped_and_counted(self):
        raw = _feed([{"id": "no-route", "route_id": "", "lat": 40.7, "lng": -73.9}])
        parsed = parse_vehicle_positions(raw)
        assert parsed[0]["route_id"] == ""
        vehicles, debug = _build(
            [{"content": raw, "suffix": "nqrw"}],
            requested_set={"Q"},
            route_ids={"Q"},
        )
        assert vehicles == []
        assert debug["feeds"][0]["missing_route"] == 1

    def test_duplicate_ids_keep_the_first_vehicle_across_concatenated_feeds(self):
        feeds = [
            _raw("ace", [{"id": "same", "route_id": "A", "lat": 40.7, "lng": -74.0}]),
            _raw("nqrw", [{"id": "same", "route_id": "A", "lat": 40.8, "lng": -73.9}]),
        ]
        vehicles, _debug = _build(feeds, requested_set={"A"}, route_ids={"A"})
        assert len(vehicles) == 1
        assert vehicles[0]["lat"] == pytest.approx(40.7, abs=1e-4)

    def test_stale_uses_rounded_age_greater_than_300_and_zero_timestamp_never_ages(self):
        feeds = [_raw("nqrw", [
            {"id": "stale", "route_id": "Q", "timestamp": NOW - 301},
            {"id": "fresh", "route_id": "Q", "timestamp": NOW - 300},
            {"id": "zero-ts", "route_id": "Q", "timestamp": 0},
            {"id": "missing-ts", "route_id": "Q"},
        ])]
        vehicles, _debug = _build(feeds, requested_set={"Q"}, route_ids={"Q"})
        by_id = {row["id"]: row for row in vehicles}
        assert by_id["stale"]["stale"] is True
        assert by_id["stale"]["age_seconds"] == 301
        assert by_id["fresh"]["stale"] is False
        assert by_id["fresh"]["age_seconds"] == 300
        assert by_id["zero-ts"]["stale"] is False
        assert by_id["zero-ts"]["timestamp"] is None
        assert by_id["missing-ts"]["stale"] is False

    def test_stop_only_emit_requires_route_and_stop_and_uses_pending_coords_source(self):
        feeds = [_raw("nqrw", [
            {"id": "ok", "route_id": "Q", "stop_id": "Q01", "has_position": False},
            {"id": "no-stop", "route_id": "Q", "stop_id": "", "has_position": False},
            {"id": "no-route", "route_id": "", "stop_id": "Q01", "has_position": False},
        ])]
        vehicles, debug = _build(feeds, requested_set={"Q"}, route_ids={"Q"})
        assert len(vehicles) == 1
        assert vehicles[0]["id"] == "ok"
        assert vehicles[0]["lat"] is None
        assert vehicles[0]["position_source"] == "stop_id_pending_coords"
        assert debug["stop_only_candidates"] == 1
        hidden = _build(
            feeds, requested_set={"Q"}, route_ids={"Q"}, include_stop_only=False
        )[0]
        assert hidden == []

    def test_positioned_and_stop_only_identity_fallbacks_omit_timestamp_on_stop_only(self):
        positioned = _feed([{
            "route_id": "Q",
            "trip_id": "",
            "stop_id": "Q01",
            "timestamp": 99,
        }])
        stop_only = _feed([{
            "route_id": "Q",
            "trip_id": "",
            "stop_id": "Q01",
            "has_position": False,
            "timestamp": 99,
        }])
        assert parse_vehicle_positions(positioned)[0]["id"] == "Q-Q01-99"
        assert parse_vehicle_positions(stop_only, include_stop_only=True)[0]["id"] == "Q-Q01"

    def test_protobuf_zero_stop_sequence_is_lost_and_unknown_color_is_neutral_gray(self):
        feeds = [_raw("nqrw", [
            {"id": "seq0", "route_id": "Q", "sequence": 0},
            {"id": "unknown", "route_id": "ZZ", "lat": 40.7, "lng": -73.9},
        ])]
        vehicles, _debug = _build(feeds, requested_set=set(), route_ids={"Q"})
        by_id = {row["id"]: row for row in vehicles}
        assert by_id["seq0"]["current_stop_sequence"] is None
        assert by_id["seq0"]["color"] == get_route_color("Q")
        assert by_id["unknown"]["color"] == "#808183"

    def test_debug_scope_and_feed_failure_count_use_requested_set_not_raw_feed_count(self):
        feeds = [_raw("nqrw", [{"id": "q1", "route_id": "Q"}])]
        _vehicles, nearest = _build(feeds, requested_set={"Q", "A"}, route_ids={"Q", "A"})
        assert nearest["scope"] == "nearest_routes"
        assert nearest["feed_failures"] == 1
        assert nearest["feeds_ok"] == 1
        _vehicles, all_subway = _build(feeds, requested_set={"Q"}, route_ids=None)
        assert all_subway["scope"] == "all_subway"
        assert all_subway["requested_routes"] == ["Q"]
