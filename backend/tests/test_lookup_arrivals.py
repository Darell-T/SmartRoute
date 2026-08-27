from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.transit import lookup_arrivals
from app.services.mta.feeds import _gtfs_realtime_pb2

NOW = 1_800_000_000


class DatabaseUnavailableError(RuntimeError):
    pass


class FakeGtfs:
    def __init__(self):
        self.stops = [
            {
                "stop_id": "D28",
                "stop_name": "Newkirk Plaza",
                "stop_lat": 40.6351,
                "stop_lon": -73.9628,
                "route_ids": ["B", "Q"],
            },
            {
                "stop_id": "D26",
                "stop_name": "Prospect Park",
                "stop_lat": 40.6616,
                "stop_lon": -73.9623,
                "route_ids": ["B", "Q", "S"],
            },
        ]

    def get_subway_stops_with_routes(self, route_ids):
        return [
            stop
            for stop in self.stops
            if set(stop["route_ids"]).intersection({str(route).upper() for route in route_ids})
        ]

    def get_child_stop_ids(self, stop_id):
        return [f"{stop_id}N", f"{stop_id}S"]


class FakeScheduledGtfs(FakeGtfs):
    def get_scheduled_arrivals(self, **_kwargs):
        return {
            "status": "scheduled",
            "valid_until": "2027-01-16T05:00:00+00:00",
            "predictions": [
                {
                    "arrival_time": NOW + 480,
                    "direction": "downtown",
                    "direction_label": "Coney Island-bound",
                    "trip_id": "scheduled-q",
                }
            ],
        }


class BrokenDatabaseGtfs:
    def get_subway_stops_with_routes(self, _route_ids):
        raise DatabaseUnavailableError

    def get_child_stop_ids(self, _stop_id):
        raise DatabaseUnavailableError


def _ctx(*, session=None, origin=None):
    return ToolContext(
        gtfs=FakeGtfs(),
        session=session or {},
        turn_id="t1",
        now_et="2027-01-15T12:00:00-05:00",
        origin=origin,
    )


def _feed(
    predictions: list[tuple[str, int]],
    *,
    route_id: str = "Q",
    timestamp: int = NOW,
) -> bytes:
    pb = _gtfs_realtime_pb2()
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    for index, (stop_id, arrival_time) in enumerate(predictions):
        entity = feed.entity.add()
        entity.id = f"entity-{index}"
        update = entity.trip_update
        update.trip.trip_id = f"trip-{index}"
        update.trip.route_id = route_id
        stop = update.stop_time_update.add()
        stop.stop_id = stop_id
        stop.arrival.time = arrival_time
    return feed.SerializeToString()


class SubwayArrivalLookupTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, tool_input, payloads, *, ctx=None):
        metadata = [
            {"content": payload, "source": f"feed-{index}"}
            for index, payload in enumerate(payloads)
        ]
        with patch.object(
            lookup_arrivals.mta_realtime,
            "fetch_feeds_with_metadata",
            AsyncMock(return_value=metadata),
        ), patch.object(lookup_arrivals.time, "time", return_value=NOW):
            return await lookup_arrivals.execute(tool_input, ctx or _ctx())

    async def test_explicit_station_alias_overrides_user_proximity(self):
        result = await self._run(
            {
                "route_id": "Q",
                "stop_query": "Newkirk Avenue",
                "user_location": {"latitude": 40.6616, "longitude": -73.9623},
            },
            [_feed([("D28N", NOW + 180), ("D28S", NOW + 360)])],
        )
        assert result.ok
        assert result.data["stop"]["name"] == "Newkirk Plaza"
        assert {direction["id"] for direction in result.data["directions"]} == {"uptown", "downtown"}
        assert set(result.timings) == {"stop_resolution_ms", "feed_fetch_ms", "feed_parse_ms"}
        assert all(value >= 0 for value in result.timings.values())

    async def test_explicit_station_overrides_active_trip_boarding(self):
        session = {
            "active_trip": {
                "first_boarding": {
                    "route_id": "Q",
                    "stop_id": "D28",
                    "stop_name": "Newkirk Plaza",
                    "direction_id": 1,
                    "semantic_direction": "downtown",
                    "coordinates": {"latitude": 40.6351, "longitude": -73.9628},
                }
            }
        }
        result = await self._run(
            {"route_id": "Q", "stop_query": "Prospect Park"},
            [_feed([("D26S", NOW + 240), ("D28S", NOW + 420)])],
            ctx=_ctx(session=session),
        )

        assert result.data["stop"]["name"] == "Prospect Park"
        assert [group["id"] for group in result.data["directions"]] == ["downtown"]

    async def test_line_only_uses_nearest_station_served_by_that_line(self):
        result = await self._run(
            {
                "route_id": "Q",
                "user_location": {"latitude": 40.6615, "longitude": -73.9624},
            },
            [_feed([("D26N", NOW + 240)])],
        )
        assert result.data["stop"]["name"] == "Prospect Park"

    async def test_raw_coordinate_query_uses_nearest_applicable_stop(self):
        session = {
            "active_trip": {
                "first_boarding": {
                    "route_id": "Q",
                    "stop_id": "D28",
                    "stop_name": "Newkirk Plaza",
                    "coordinates": {"latitude": 40.6351, "longitude": -73.9628},
                }
            }
        }
        result = await self._run(
            {
                "route_id": "Q",
                "stop_query": "40.6615,-73.9624",
            },
            [_feed([("D26N", NOW + 240)])],
            ctx=_ctx(session=session),
        )

        assert result.ok
        assert result.data["stop"]["name"] == "Prospect Park"
        assert result.data["stop"]["id"] == "D26"

    async def test_current_location_source_ignores_coordinate_like_station_text(self):
        result = await self._run(
            {
                "route_id": "Q",
                "stop_source": "current_location",
                "stop_query": "closest Q train stop to 40.6615,-73.9624",
            },
            [_feed([("D26N", NOW + 240)])],
            ctx=_ctx(origin={"lat": 40.6615, "lng": -73.9624}),
        )

        assert result.ok
        assert result.data["stop"]["name"] == "Prospect Park"

    async def test_named_station_source_never_falls_back_to_current_location(self):
        result = await self._run(
            {
                "route_id": "Q",
                "stop_source": "named_station",
                "stop_query": "Not A Real Station",
            },
            [_feed([("D26N", NOW + 240)])],
            ctx=_ctx(origin={"lat": 40.6615, "lng": -73.9624}),
        )

        assert result.outcome == "needs_clarification"
        assert result.data["source_status"] == "stop_not_resolved"

    async def test_active_trip_boarding_takes_priority_over_location(self):
        session = {
            "active_trip": {
                "first_boarding": {
                    "route_id": "Q",
                    "stop_name": "Newkirk Plaza",
                    "direction": "Downtown",
                    "coordinates": {"latitude": 40.6351, "longitude": -73.9628},
                    "walking_minutes": 2,
                }
            }
        }
        result = await self._run(
            {"route_id": "Q"},
            [_feed([("D28N", NOW + 120), ("D28S", NOW + 420)])],
            ctx=_ctx(session=session, origin={"lat": 40.6616, "lng": -73.9623}),
        )
        assert result.data["stop"]["name"] == "Newkirk Plaza"
        assert [group["id"] for group in result.data["directions"]] == ["downtown"]
        assert result.data["catchability"]["catchable_arrival_minutes"] == 7

    async def test_terminal_label_cannot_override_active_trip_direction_for_catchability(self):
        session = {
            "active_trip": {
                "first_boarding": {
                    "route_id": "Q",
                    "stop_name": "Newkirk Plaza",
                    "direction_id": 1,
                    "semantic_direction": "downtown",
                    "direction_label": "Coney Island-Stillwell Av",
                    "coordinates": {"latitude": 40.6351, "longitude": -73.9628},
                    "walking_minutes": 2,
                }
            }
        }

        result = await self._run(
            {
                "route_id": "Q",
                "direction": "Coney Island-Stillwell Av",
            },
            [
                _feed(
                    [
                        ("D28N", NOW + 300),
                        ("D28S", NOW + 720),
                        ("D28S", NOW + 900),
                        ("D28S", NOW + 1020),
                    ]
                )
            ],
            ctx=_ctx(session=session),
        )

        assert [group["id"] for group in result.data["directions"]] == ["downtown"]
        assert result.data["catchability"]["arrival_minutes"] == [12, 15, 17]
        assert result.data["catchability"]["catchable_arrival_minutes"] == 12

    async def test_unresolved_subway_direction_withholds_catchability(self):
        result = await self._run(
            {
                "route_id": "Q",
                "stop_query": "Newkirk Plaza",
                "direction": "Coney Island-Stillwell Av",
                "walking_minutes": 2,
            },
            [_feed([("D28N", NOW + 300), ("D28S", NOW + 720)])],
        )

        assert {group["id"] for group in result.data["directions"]} == {"uptown", "downtown"}
        assert "catchability" not in result.data

    async def test_active_q_at_church_uses_persisted_stop_when_database_is_unavailable(self):
        session = {
            "active_trip": {
                "first_boarding": {
                    "route_id": "q",
                    "stop_id": "D28",
                    "stop_name": "Church Av",
                    "direction_id": 1,
                    "semantic_direction": "downtown",
                    "direction_label": "Coney Island-Stillwell Av",
                    "destination_stop_id": "D43",
                    "coordinates": {"latitude": 40.6505, "longitude": -73.9624},
                }
            }
        }
        context = _ctx(session=session)
        context.gtfs = BrokenDatabaseGtfs()

        result = await self._run(
            {"route_id": "q"},
            [_feed([("D28N", NOW + 120), ("D28S", NOW + 420)])],
            ctx=context,
        )

        assert result.ok
        assert result.data["route_id"] == "Q"
        assert result.data["stop"] == {"id": "D28", "name": "Church Av", "distance_meters": 0.0, "latitude": 40.6505, "longitude": -73.9624}
        assert [group["id"] for group in result.data["directions"]] == ["downtown"]

    async def test_predictions_are_deduplicated_and_sorted(self):
        result = await self._run(
            {"route_id": "Q", "stop_query": "Newkirk Plaza", "limit": 3},
            [
                _feed(
                    [
                        ("D28N", NOW + 600),
                        ("D28N", NOW + 180),
                        ("D28N", NOW + 180),
                    ]
                )
            ],
        )
        minutes = result.data["directions"][0]["arrivals"]
        assert [arrival["minutes"] for arrival in minutes] == [3, 10]

    async def test_due_prediction_is_skipped_in_favor_of_the_next_arrival(self):
        result = await self._run(
            {"route_id": "Q", "stop_query": "Newkirk Plaza", "limit": 3},
            [_feed([("D28N", NOW), ("D28N", NOW + 480), ("D28N", NOW + 840)])],
        )

        arrivals = result.data["directions"][0]["arrivals"]
        assert [arrival["minutes"] for arrival in arrivals] == [8, 14]

    async def test_stale_and_no_prediction_states_remain_distinct(self):
        stale = await self._run(
            {"route_id": "Q", "stop_query": "Newkirk Plaza"},
            [_feed([("D28N", NOW + 180)], timestamp=NOW - 300)],
        )
        empty = await self._run(
            {"route_id": "Q", "stop_query": "Newkirk Plaza"},
            [_feed([], timestamp=NOW)],
        )
        assert stale.data["source_status"] == "stale"
        assert empty.data["source_status"] == "no_predictions"

    async def test_provider_unavailable_is_not_no_predictions(self):
        result = await self._run(
            {"route_id": "Q", "stop_query": "Newkirk Plaza"},
            [],
        )
        assert result.data["source_status"] == "provider_unavailable"
        assert result.data["directions"] == []

    async def test_provider_unavailable_falls_back_to_distinct_scheduled_data(self):
        context = _ctx()
        context.gtfs = FakeScheduledGtfs()
        result = await self._run(
            {"route_id": "Q", "stop_query": "Newkirk Plaza"},
            [],
            ctx=context,
        )
        assert result.data["source_status"] == "scheduled"
        arrival = result.data["directions"][0]["arrivals"][0]
        assert not arrival["realtime"]
        assert result.data["evidence"]["source"] == "mta_static_gtfs"
        assert result.data["evidence"]["status"] == "current"

    async def test_unserved_route_does_not_choose_an_unrelated_station(self):
        result = await self._run(
            {
                "route_id": "7",
                "user_location": {"latitude": 40.6351, "longitude": -73.9628},
            },
            [],
        )
        assert result.data["source_status"] == "stop_not_resolved"


class CatchabilityTests(unittest.TestCase):
    def test_impossible_first_arrival_is_not_highlighted(self):
        result = lookup_arrivals.assess_catchability(
            [3, 9, 16],
            walking_minutes=6,
            boarding_buffer_minutes=2,
        )
        assert result["catchable_arrival_minutes"] == 9

    def test_due_arrival_is_not_part_of_catchability(self):
        result = lookup_arrivals.assess_catchability(
            [0, 8, 14],
            walking_minutes=2,
            boarding_buffer_minutes=2,
        )
        assert result["arrival_minutes"] == [8, 14]
        assert result["catchable_arrival_minutes"] == 8

    def test_no_predictions_has_no_catchable_arrival(self):
        result = lookup_arrivals.assess_catchability([], walking_minutes=3)
        assert result["catchable_arrival_minutes"] is None
        assert result["confidence"] == 0.0


class BusArrivalLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_bus_direction_filter_uses_the_matching_directional_stop(self):
        rows = [
            {
                "route_id": "B35",
                "parent_stop_id": "west",
                "station_name": "Church Av/E 18 St",
                "terminal_stop_name": "Sunset Park",
                "direction": "westbound",
                "arrival_time": NOW + 240,
                "distance_m": 40,
                "stop_lat": 40.649,
                "stop_lon": -73.963,
            },
            {
                "route_id": "B35",
                "parent_stop_id": "east",
                "station_name": "Church Av/E 18 St",
                "terminal_stop_name": "Brownsville",
                "direction": "eastbound",
                "arrival_time": NOW + 120,
                "distance_m": 20,
                "stop_lat": 40.649,
                "stop_lon": -73.962,
            },
        ]
        with patch.object(
            lookup_arrivals.mta_realtime,
            "fetch_nearby_bus_update",
            AsyncMock(
                return_value={
                    "arrivals": rows,
                    "debug": {"bus_arrivals_supported": True},
                    "status": "ready",
                }
            ),
        ), patch.object(lookup_arrivals.time, "time", return_value=NOW):
            result = await lookup_arrivals.execute(
                {
                    "mode": "bus",
                    "route_id": "B35",
                    "stop_query": "Church Av/E 18 St",
                    "direction": "Sunset Park",
                    "user_location": {"latitude": 40.649, "longitude": -73.963},
                },
                _ctx(),
            )
        assert result.data["stop"]["id"] == "west"
        assert result.data["directions"][0]["arrivals"][0]["minutes"] == 4


if __name__ == "__main__":
    unittest.main()
