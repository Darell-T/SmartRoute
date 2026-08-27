"""Focused tests for the still-registered internal transit snapshot tool."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent.tools.transit import transit_snapshot

from tests._fake_http_tools import make_tool_ctx


class TransitSnapshotToolTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self, origin=None, gtfs="fake-gtfs"):
        return make_tool_ctx(origin, gtfs=gtfs)

    async def test_near_user_without_gps_asks_for_location(self):
        result = await transit_snapshot.execute(
            {"near": "user"}, self._ctx(origin=None)
        )
        assert not result.ok
        assert "location" in result.error.lower()

    async def test_near_resolves_and_builds_snapshot(self):
        snapshot = {
            "nearest_stop": {"stop_name": "Church Av", "distance_m": 50},
            "arrivals": [
                {"route_id": "Q", "station_name": "Church Av", "arrival_time": 123}
            ],
            "alerts": [{"header": "Delays on Q", "route_ids": ["Q"]}],
            "signals": {"network_status": "healthy"},
        }
        with patch.object(
            transit_snapshot,
            "_build_live_snapshot",
            new=AsyncMock(return_value=snapshot),
        ) as build_snapshot:
            result = await transit_snapshot.execute(
                {"near": "user"}, self._ctx(origin={"lat": 40.7, "lng": -73.9})
            )
            build_snapshot.assert_awaited_once_with("fake-gtfs", 40.7, -73.9)
        assert result.ok
        assert result.data["network_status"] == "healthy"
        assert len(result.data["arrivals"]) == 1

    async def test_near_without_gtfs_ready_is_reported(self):
        result = await transit_snapshot.execute(
            {"near": "user"}, self._ctx(origin={"lat": 40.7, "lng": -73.9}, gtfs=None)
        )
        assert not result.ok
        assert "not ready" in result.error

    async def test_lines_filter_alerts_without_location(self):
        alerts = [
            {"header": "Q delayed", "route_ids": ["Q"]},
            {"header": "B suspended", "route_ids": ["B"]},
        ]
        with (
            patch.object(
                transit_snapshot.mta_realtime,
                "fetch_service_alerts",
                new=AsyncMock(return_value=b"x"),
            ),
            patch.object(
                transit_snapshot.mta_realtime,
                "parse_service_alerts",
                return_value=alerts,
            ),
            patch.object(
                transit_snapshot.mta_realtime,
                "filter_alerts_for_routes",
                side_effect=lambda parsed, route_ids: [
                    a for a in parsed if set(a["route_ids"]) & route_ids
                ],
            ),
        ):
            result = await transit_snapshot.execute(
                {"lines": ["Q"]}, self._ctx(origin=None)
            )
        assert result.ok
        assert len(result.data["alerts"]) == 1
        assert "Q" in result.data["alerts"][0]["header"]
        assert result.data["requested_routes"] == ["Q"]
        assert result.data["affected_routes"] == ["Q"]
        assert result.data["freshness"] == "unknown"
        assert "observed_at" not in result.data

    async def test_line_status_requests_fresh_alerts_with_stale_metadata_fallback(self):
        alert_result = {
            "content": b"alerts-feed",
            "freshness": "stale",
            "observed_at": "2026-08-22T12:00:00+00:00",
        }
        with (
            patch.object(
                transit_snapshot.mta_realtime,
                "fetch_service_alerts",
                new=AsyncMock(return_value=alert_result),
            ) as fetch_alerts,
            patch.object(
                transit_snapshot.mta_realtime,
                "parse_service_alerts",
                return_value=[],
            ),
        ):
            result = await transit_snapshot.execute({"lines": ["Q"]}, self._ctx())

        fetch_alerts.assert_awaited_once_with(force_refresh=True, with_metadata=True)
        assert result.ok
        assert result.data["freshness"] == "stale"
        assert result.data["observed_at"] == "2026-08-22T12:00:00+00:00"

    async def test_line_status_preserves_live_alert_metadata(self):
        alert_result = {
            "content": b"alerts-feed",
            "freshness": "live",
            "observed_at": "2026-08-22T12:01:00+00:00",
        }
        with (
            patch.object(
                transit_snapshot.mta_realtime,
                "fetch_service_alerts",
                new=AsyncMock(return_value=alert_result),
            ),
            patch.object(
                transit_snapshot.mta_realtime,
                "parse_service_alerts",
                return_value=[],
            ),
        ):
            result = await transit_snapshot.execute({"lines": ["Q"]}, self._ctx())

        assert result.ok
        assert result.data["freshness"] == "live"
        assert result.data["observed_at"] == "2026-08-22T12:01:00+00:00"

    async def test_no_active_alerts_does_not_mark_requested_line_as_affected(self):
        with (
            patch.object(
                transit_snapshot.mta_realtime,
                "fetch_service_alerts",
                new=AsyncMock(return_value=b"alerts-feed"),
            ),
            patch.object(
                transit_snapshot.mta_realtime, "parse_service_alerts", return_value=[]
            ),
        ):
            result = await transit_snapshot.execute(
                {"lines": ["Q"]}, self._ctx(origin=None)
            )

        assert result.ok
        assert result.data["status"] == "no_active_alerts"
        assert result.data["requested_routes"] == ["Q"]
        assert result.data["affected_routes"] == []

    async def test_alert_headline_is_capped_via_safe_text(self):
        long_header = "X" * 500
        with (
            patch.object(
                transit_snapshot.mta_realtime,
                "fetch_service_alerts",
                new=AsyncMock(return_value=b"x"),
            ),
            patch.object(
                transit_snapshot.mta_realtime,
                "parse_service_alerts",
                return_value=[{"header": long_header, "route_ids": []}],
            ),
        ):
            result = await transit_snapshot.execute({}, self._ctx(origin=None))
        assert len(result.data["alerts"][0]["header"]) <= 200


if __name__ == "__main__":
    unittest.main()
