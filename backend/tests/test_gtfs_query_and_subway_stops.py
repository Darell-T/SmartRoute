from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psycopg2
import pytest
from app.routers import subway as subway_mod
from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex
from app.services.mta.static_gtfs.store import GTFSStaticData


class CodedOperationalError(psycopg2.OperationalError):
    def __init__(self, pgcode: str):
        super().__init__("query failed")
        self._pgcode = pgcode

    @property
    def pgcode(self):
        return self._pgcode


class FakeCursor:
    def __init__(self, outcome):
        self.outcome = outcome
        self.rows = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, _params=None):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        self.rows = self.outcome

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, outcome):
        self.outcome = outcome

    def cursor(self, **_unused):
        return FakeCursor(self.outcome)


class FakePool:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.puts: list[tuple[object, bool]] = []
        self.get_count = 0

    def getconn(self):
        self.get_count += 1
        if not self.outcomes:
            raise RuntimeError("pool exhausted")
        return FakeConn(self.outcomes.pop(0))

    def putconn(self, conn, close=False):
        self.puts.append((conn, close))


class GtfsQueryRetryTests(unittest.TestCase):
    def _run(self, outcomes):
        pool = FakePool(outcomes)
        gtfs = GTFSStaticData()
        with patch("app.services.mta.static_gtfs.store._get_pool", return_value=pool):
            try:
                return pool, gtfs._query("SELECT 1"), None
            except (psycopg2.Error, ValueError) as exc:
                return pool, None, exc

    def test_stale_connection_retries_once_then_returns_without_closing_success(self):
        pool, rows, error = self._run([psycopg2.OperationalError("stale"), [{"ok": 1}]])
        assert error is None
        assert rows == [{"ok": 1}]
        assert pool.get_count == 2
        assert [closed for _conn, closed in pool.puts] == [True, False]

    def test_second_failure_raises_after_closing_both_connections(self):
        pool, rows, error = self._run([
            psycopg2.OperationalError("stale"),
            psycopg2.OperationalError("stale"),
        ])
        assert rows is None
        assert isinstance(error, psycopg2.OperationalError)
        assert pool.get_count == 2
        assert [closed for _conn, closed in pool.puts] == [True, True]

    def test_statement_timeout_does_not_retry(self):
        pool, rows, error = self._run(
            [CodedOperationalError("57014"), [{"should": "not run"}]]
        )
        assert rows is None
        assert isinstance(error, psycopg2.OperationalError)
        assert error.pgcode == "57014"
        assert pool.get_count == 1
        assert pool.puts == [(pool.puts[0][0], True)]

    def test_interface_error_retries_once_like_stale_operational_error(self):
        pool, rows, error = self._run([psycopg2.InterfaceError("closed"), [{"ok": 1}]])
        assert error is None
        assert rows == [{"ok": 1}]
        assert pool.get_count == 2
        assert [closed for _conn, closed in pool.puts] == [True, False]

    def test_other_exceptions_return_the_connection_without_close(self):
        pool, rows, error = self._run([ValueError("boom")])
        assert rows is None
        assert isinstance(error, ValueError)
        assert pool.get_count == 1
        assert [closed for _conn, closed in pool.puts] == [False]

    def test_pool_exhaustion_is_not_retried(self):
        pool = FakePool([])
        gtfs = GTFSStaticData()
        with (
            patch("app.services.mta.static_gtfs.store._get_pool", return_value=pool),
            pytest.raises(RuntimeError, match="pool exhausted"),
        ):
            gtfs._query("SELECT 1")
        assert pool.get_count == 1
        assert pool.puts == []


class SubwayStopsWithRoutesTests(unittest.TestCase):
    def test_db_path_keeps_route_ids_as_stored_and_skips_parents_without_routes(self):
        gtfs = GTFSStaticData()

        def scripted(sql, _params=None):
            if "location_type = '1'" in sql:
                return [
                    {
                        "stop_id": "R16",
                        "stop_name": "Times Sq-42 St",
                        "stop_lat": 40.75,
                        "stop_lon": -73.98,
                    },
                    {
                        "stop_id": "XX",
                        "stop_name": "No Routes",
                        "stop_lat": 40.0,
                        "stop_lon": -74.0,
                    },
                ]
            return [
                {"parent_id": "R16", "route_id": "Q"},
                {"parent_id": "R16", "route_id": "q"},
            ]

        gtfs._query = scripted
        rows = gtfs.get_subway_stops_with_routes({"Q"})
        assert rows == [{
            "stop_id": "R16",
            "stop_name": "Times Sq-42 St",
            "stop_lat": 40.75,
            "stop_lon": -73.98,
            "route_ids": ["Q"],
        }]
        lowered = gtfs.get_subway_stops_with_routes({"q"})
        assert lowered == [{
            "stop_id": "R16",
            "stop_name": "Times Sq-42 St",
            "stop_lat": 40.75,
            "stop_lon": -73.98,
            "route_ids": ["q"],
        }]

    def test_index_path_uppercases_the_route_whitelist(self):
        gtfs = GTFSStaticData()
        gtfs.set_pattern_index(StopPatternIndex({
            "stops": {
                "R16": {"name": "Times Sq-42 St", "lat": 40.75, "lon": -73.98},
            },
            "patterns": [{
                "route_id": "Q",
                "route_short_name": "Q",
                "direction_id": 0,
                "trip_count": 1,
                "signature": "q1",
                "stop_ids": ["R16"],
            }],
        }))
        gtfs._query = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("index path must not query")
        )
        rows = gtfs.get_subway_stops_with_routes({"q"})
        assert rows[0]["route_ids"] == ["Q"]


class SubwayStopsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        subway_mod._CACHE = {"data": None, "ts": 0.0}

    async def asyncTearDown(self):
        subway_mod._CACHE = {"data": None, "ts": 0.0}

    async def test_missing_gtfs_returns_503_without_color_field(self):
        response = await subway_mod.subway_stops(
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gtfs=None)))
        )
        assert response.status_code == 503
        assert json.loads(response.body) == {"error": "GTFS not ready"}

    async def test_geojson_omits_rows_without_coordinates_and_does_not_invent_color(self):
        class Gtfs:
            def __init__(self):
                self.calls = 0
                self.whitelist = None

            def get_subway_stops_with_routes(self, whitelist):
                self.calls += 1
                self.whitelist = whitelist
                return [
                    {
                        "stop_id": "R16",
                        "stop_name": "Times Sq-42 St",
                        "stop_lat": 40.75,
                        "stop_lon": -73.98,
                        "route_ids": ["N", "Q"],
                    },
                    {
                        "stop_id": "BAD",
                        "stop_name": "Missing",
                        "stop_lat": None,
                        "stop_lon": -73.98,
                        "route_ids": ["Q"],
                    },
                ]

        gtfs = Gtfs()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gtfs=gtfs)))
        with patch.object(subway_mod.time, "time", return_value=1_700_000_000.9):
            first = await subway_mod.subway_stops(request)
            second = await subway_mod.subway_stops(request)
        body = json.loads(first.body)
        assert first.status_code == 200
        assert body["type"] == "FeatureCollection"
        assert body["updated_at"] == 1_700_000_000
        assert len(body["features"]) == 1
        feature = body["features"][0]
        assert feature["geometry"]["coordinates"] == [-73.98, 40.75]
        assert feature["properties"] == {
            "stop_id": "R16",
            "name": "Times Sq-42 St",
            "route_ids": ["N", "Q"],
        }
        assert "color" not in feature["properties"]
        assert gtfs.calls == 1
        assert gtfs.whitelist is subway_mod.SUBWAY_ROUTE_IDS
        assert json.loads(second.body)["updated_at"] == 1_700_000_000
