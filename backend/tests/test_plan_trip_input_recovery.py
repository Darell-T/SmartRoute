"""Focused route_with_recovery gating tests.

The recovery path may only re-request by coordinates when the first Google
Routes failure explicitly identifies destination resolution in its provider
summary. Authentication, quota, timeout, network, and other provider errors
must propagate immediately so one failing provider request can never become two.
"""

import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route import route_input as plan_trip_input
from app.services.agent.tools.route.preparation_adapter import PreparedLeg, prepare_single_leg
from app.services.agent.tools._types import ToolContext
from app.services.directions import GoogleRoutesError
from app.services import directions
from app.services.trips import candidates as trip_candidates
from tests.test_stop_patterns import DISTINCT_TRANSFER_FIXTURE, FIXTURE
from app.services.mta.static_gtfs.stop_patterns import StopPatternIndex


class _GoogleRoutesError(RuntimeError):
    def __init__(self, code, message, *, provider_status=None, provider_summary=None):
        super().__init__(message)
        self.code = code
        self.provider_status = provider_status
        self.provider_summary = provider_summary


class _FakeDirections:
    GoogleRoutesError = _GoogleRoutesError

    def __init__(self, first, second=None):
        self.calls = []
        self._first = first
        self._second = second

    async def get_transit_route(self, origin, dest, dest_coords=None, **_kwargs):
        self.calls.append({"dest": dest, "dest_coords": dest_coords})
        result = self._first if len(self.calls) == 1 else self._second
        if isinstance(result, Exception):
            raise result
        return result

    def parse_response(self, response):
        return response


def _places():
    origin = ResolvedPlace(name="Origin", latitude=40.71, longitude=-73.99, source="user")
    destination = ResolvedPlace(
        name="Test Dest", latitude=40.70, longitude=-73.98, source="gps"
    )
    return origin, destination


async def _recover(directions):
    origin, destination = _places()
    return await plan_trip_input.route_with_recovery(
        directions_service=directions,
        origin=origin,
        destination=destination,
        destination_query="Test Dest",
        allowed_modes=["SUBWAY", "BUS"],
        routing_preference="FEWER_TRANSFERS",
        departure_time=None,
    )


class RouteRecoveryGatingTests(unittest.IsolatedAsyncioTestCase):
    async def test_arrive_by_derives_departure_from_provider_duration(self):
        origin, destination = _places()
        directions = _FakeDirections(first=[[{"route_total_seconds": 1_800}]])

        departure = await plan_trip_input.derive_arrive_by_departure(
            directions_service=directions,
            origin=origin,
            destination=destination,
            destination_query="Test Dest",
            arrival_by="2026-08-24T12:00:00-04:00",
            allowed_modes=["SUBWAY"],
            routing_preference="FEWER_TRANSFERS",
        )

        self.assertEqual(departure, "2026-08-24T11:30:00-04:00")

    async def test_arrive_by_rejects_route_without_duration(self):
        origin, destination = _places()
        directions = _FakeDirections(first=[[{"route_id": "Q"}]])

        with self.assertRaisesRegex(_GoogleRoutesError, "route duration"):
            await plan_trip_input.derive_arrive_by_departure(
                directions_service=directions,
                origin=origin,
                destination=destination,
                destination_query="Test Dest",
                arrival_by="2026-08-24T12:00:00-04:00",
                allowed_modes=["SUBWAY"],
                routing_preference="FEWER_TRANSFERS",
            )

    async def test_http_404_recovers_by_coordinates(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError(
                "http_404", "address not found", provider_status=404,
                provider_summary="NOT_FOUND address not found",
            ),
            second=[{"route_id": "A"}],
        )
        routes = await _recover(directions)
        self.assertEqual(routes, [{"route_id": "A"}])
        self.assertEqual(len(directions.calls), 2)
        self.assertIsNone(directions.calls[0]["dest_coords"])
        self.assertEqual(directions.calls[1]["dest_coords"], (40.70, -73.98))

    async def test_generic_http_404_raises_without_second_request(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError(
                "http_404",
                "not found",
                provider_status=404,
                provider_summary="NOT_FOUND requested resource does not exist",
            ),
            second=[{"route_id": "A"}],
        )
        with self.assertRaises(_GoogleRoutesError):
            await _recover(directions)
        self.assertEqual(len(directions.calls), 1)

    async def test_http_400_address_summary_recovers_by_coordinates(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError(
                "http_400", "bad request", provider_status=400,
                provider_summary="INVALID_ARGUMENT address could not be geocoded",
            ),
            second=[{"route_id": "Q"}],
        )
        routes = await _recover(directions)
        self.assertEqual(routes, [{"route_id": "Q"}])
        self.assertEqual(len(directions.calls), 2)

    async def test_http_400_destination_summary_recovers_by_coordinates(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError(
                "http_400", "bad request", provider_status=400,
                provider_summary="INVALID_ARGUMENT destination not found",
            ),
            second=[],
        )
        routes = await _recover(directions)
        self.assertEqual(routes, [])
        self.assertEqual(len(directions.calls), 2)

    async def test_address_specific_request_failure_recovers_by_coordinates(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError("request_failed", "address route failed"),
            second=[{"route_id": "Q"}],
        )
        routes = await _recover(directions)
        self.assertEqual(routes, [{"route_id": "Q"}])
        self.assertEqual(len(directions.calls), 2)

    async def test_http_400_non_address_summary_raises_without_second_request(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError(
                "http_400", "bad request", provider_status=400,
                provider_summary="INVALID_ARGUMENT field masks are invalid",
            ),
            second=[{"route_id": "A"}],
        )
        with self.assertRaises(_GoogleRoutesError) as raised:
            await _recover(directions)
        self.assertEqual(raised.exception.code, "http_400")
        self.assertEqual(len(directions.calls), 1)

    async def test_http_403_raises_without_second_request(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError(
                "http_403", "permission denied", provider_status=403,
                provider_summary="PERMISSION_DENIED API key not authorized",
            ),
            second=[{"route_id": "A"}],
        )
        with self.assertRaises(_GoogleRoutesError) as raised:
            await _recover(directions)
        self.assertEqual(raised.exception.code, "http_403")
        self.assertEqual(len(directions.calls), 1)

    async def test_quota_raises_without_second_request(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError(
                "http_429", "quota exceeded", provider_status=429,
                provider_summary="RESOURCE_EXHAUSTED quota",
            ),
            second=[{"route_id": "A"}],
        )
        with self.assertRaises(_GoogleRoutesError):
            await _recover(directions)
        self.assertEqual(len(directions.calls), 1)

    async def test_timeout_raises_without_second_request(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError("timeout", "Google Routes API timed out"),
            second=[{"route_id": "A"}],
        )
        with self.assertRaises(_GoogleRoutesError) as raised:
            await _recover(directions)
        self.assertEqual(raised.exception.code, "timeout")
        self.assertEqual(len(directions.calls), 1)

    async def test_network_error_raises_without_second_request(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError("request_failed", "Google Routes API request failed"),
            second=[{"route_id": "A"}],
        )
        with self.assertRaises(_GoogleRoutesError) as raised:
            await _recover(directions)
        self.assertEqual(raised.exception.code, "request_failed")
        self.assertEqual(len(directions.calls), 1)

    async def test_invalid_json_raises_without_second_request(self):
        directions = _FakeDirections(
            first=_GoogleRoutesError("invalid_json", "provider returned invalid data"),
            second=[{"route_id": "A"}],
        )
        with self.assertRaises(_GoogleRoutesError) as raised:
            await _recover(directions)
        self.assertEqual(raised.exception.code, "invalid_json")
        self.assertEqual(len(directions.calls), 1)

    async def test_successful_first_response_returns_without_second_request(self):
        directions = _FakeDirections(first=[{"route_id": "1"}])
        routes = await _recover(directions)
        self.assertEqual(routes, [{"route_id": "1"}])
        self.assertEqual(len(directions.calls), 1)

    async def test_empty_first_response_still_recovers_by_coordinates(self):
        directions = _FakeDirections(first=[], second=[{"route_id": "C"}])
        routes = await _recover(directions)
        self.assertEqual(routes, [{"route_id": "C"}])
        self.assertEqual(len(directions.calls), 2)


class StructuralRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _primary(self):
        return [[
            {
                "type": "SUBWAY",
                "route_id": "Q",
                "departure_stop": "Alpha Av",
                "arrival_stop": "Epsilon Ctr",
                "departure_coords": {"latitude": 40.0, "longitude": -73.0},
                "arrival_coords": {"latitude": 40.4, "longitude": -73.4},
            }
        ]]

    async def test_recovery_uses_two_segments_and_provider_arrival(self):
        first = [[{
            "type": "SUBWAY", "route_id": "Q", "departure_stop": "Origin",
            "arrival_stop": "Beta St", "departure_coords": {"latitude": 40.71, "longitude": -73.99},
            "arrival_coords": {"latitude": 40.1, "longitude": -73.1},
            "arrival_time_iso": "2026-08-23T12:10:00-04:00", "route_total_seconds": 600,
        }]]
        second = [[{
            "type": "WALK", "departure_stop": "Beta St", "arrival_stop": "Beta St",
            "departure_coords": {"latitude": 40.1, "longitude": -73.1},
            "arrival_coords": {"latitude": 40.1005, "longitude": -73.1005},
            "duration_seconds": 60,
        }, {
            "type": "SUBWAY", "route_id": "R", "departure_stop": "Beta St",
            "arrival_stop": "Delta Pkwy", "departure_coords": {"latitude": 40.1005, "longitude": -73.1005},
            "arrival_coords": {"latitude": 40.3, "longitude": -73.3},
            "arrival_time_iso": "2026-08-23T12:25:00-04:00", "route_total_seconds": 900,
        }]]
        origin = ResolvedPlace(name="Origin", latitude=40.71, longitude=-73.99, source="user")
        destination = ResolvedPlace(name="Delta Pkwy", latitude=40.3, longitude=-73.3, source="gtfs")
        telemetry = {}
        with patch.object(directions, "get_transit_route", new=AsyncMock(side_effect=[{}, {}])) as fetch, \
                patch.object(directions, "parse_response", side_effect=[first, second]):
            recovered = await plan_trip_input.recover_structural_route(
                directions_service=directions,
                pattern_index=StopPatternIndex(DISTINCT_TRANSFER_FIXTURE),
                primary_routes=self._primary(), origin=origin, destination=destination,
                destination_query="Test Dest", departure_time=None,
                allowed_modes=["SUBWAY", "BUS"], routing_preference="FEWER_TRANSFERS",
                excluded_route_ids=set(), excluded_modes=set(), telemetry=telemetry,
            )

        self.assertEqual(fetch.await_count, 2)
        self.assertEqual(fetch.await_args_list[0].args[2], (40.1, -73.1))
        self.assertEqual(
            fetch.await_args_list[1].args[0],
            (40.1, -73.1),
        )
        self.assertEqual(fetch.await_args_list[1].kwargs["departure_time"], "2026-08-23T12:10:00-04:00")
        self.assertEqual(recovered[0][0]["route_total_seconds"], 1500)
        self.assertEqual(telemetry["recovery_succeeded"], True)
        self.assertEqual(telemetry["recovered_service_chain"], ["Q", "R"])

    async def test_provider_failure_preserves_primary_and_does_not_retry(self):
        origin = ResolvedPlace(name="Origin", latitude=40.71, longitude=-73.99, source="user")
        destination = ResolvedPlace(name="Delta Pkwy", latitude=40.3, longitude=-73.3, source="gtfs")
        with patch.object(
            directions, "get_transit_route",
            new=AsyncMock(side_effect=GoogleRoutesError("timeout", "provider down")),
        ) as fetch:
            recovered = await plan_trip_input.recover_structural_route(
                directions_service=directions,
                pattern_index=StopPatternIndex(FIXTURE),
                primary_routes=self._primary(), origin=origin, destination=destination,
                destination_query="Test Dest", departure_time=None,
                allowed_modes=["SUBWAY", "BUS"], routing_preference="FEWER_TRANSFERS",
                excluded_route_ids=set(), excluded_modes=set(), telemetry={},
            )
        self.assertEqual(recovered, [])
        self.assertEqual(fetch.await_count, 1)

    async def test_ordinary_optional_provider_failure_preserves_primary(self):
        class _ReloadedDirections:
            async def get_transfer_route_pair(self, **_kwargs):
                raise RuntimeError("provider wrapper was reloaded")

        origin = ResolvedPlace(name="Origin", latitude=40.71, longitude=-73.99, source="user")
        destination = ResolvedPlace(name="Delta Pkwy", latitude=40.3, longitude=-73.3, source="gtfs")
        telemetry = {}
        recovered = await plan_trip_input.recover_structural_route(
            directions_service=_ReloadedDirections(),
            pattern_index=StopPatternIndex(DISTINCT_TRANSFER_FIXTURE),
            primary_routes=self._primary(), origin=origin, destination=destination,
            destination_query="Test Dest", departure_time=None,
            allowed_modes=["SUBWAY", "BUS"], routing_preference="FEWER_TRANSFERS",
            excluded_route_ids=set(), excluded_modes=set(), telemetry=telemetry,
        )

        self.assertEqual(recovered, [])
        self.assertTrue(telemetry["recovery_attempted"])
        self.assertFalse(telemetry["recovery_succeeded"])

    async def test_boundary_service_mismatch_rejects_combined_family(self):
        origin = ResolvedPlace(name="Origin", latitude=40.71, longitude=-73.99, source="user")
        destination = ResolvedPlace(name="Delta Pkwy", latitude=40.3, longitude=-73.3, source="gtfs")
        invalid_first = [[
            {"type": "SUBWAY", "route_id": "Q", "arrival_stop": "Beta St",
             "arrival_coords": {"latitude": 40.1, "longitude": -73.1}, "route_total_seconds": 600},
            {"type": "SUBWAY", "route_id": "A", "arrival_stop": "Beta St",
             "arrival_coords": {"latitude": 40.1, "longitude": -73.1}, "route_total_seconds": 600},
        ]]
        with patch.object(directions, "get_transit_route", new=AsyncMock(return_value={})), \
                patch.object(directions, "parse_response", return_value=invalid_first):
            recovered = await plan_trip_input.recover_structural_route(
                directions_service=directions, pattern_index=StopPatternIndex(FIXTURE),
                primary_routes=self._primary(), origin=origin, destination=destination,
                destination_query="Test Dest", departure_time=None,
                allowed_modes=["SUBWAY"], routing_preference="FEWER_TRANSFERS",
                excluded_route_ids=set(), excluded_modes=set(), telemetry={},
            )
        self.assertEqual(recovered, [])

    async def test_cap_one_and_arrive_by_skip_structural_recovery(self):
        class _Dependencies:
            directions_service = SimpleNamespace()

        class _Context:
            gtfs = SimpleNamespace(_pattern_index=StopPatternIndex(FIXTURE))

        primary = self._primary() + [[{"type": "SUBWAY", "route_id": "A"}]]
        for tool_input in (
            {"max_candidates": 1},
            {"max_candidates": 2, "arrival_by": "target"},
            {"max_candidates": 2, "excluded_route_ids": ["R"]},
        ):
            with self.subTest(tool_input=tool_input):
                routes = await plan_trip_input.prepare_structural_candidates(
                    self._primary() if tool_input.get("excluded_route_ids") else primary,
                    tool_input=tool_input,
                    ctx=_Context(), dependencies=_Dependencies(),
                    origin=ResolvedPlace("Origin", 40.71, -73.99, "user"),
                    destination=ResolvedPlace("Delta Pkwy", 40.3, -73.3, "gtfs"),
                    destination_query="Delta Pkwy",
                    departure_time=None, allowed_modes=["SUBWAY"],
                    routing_preference="FEWER_TRANSFERS", telemetry={}, timings={},
                )
                expected = 1 if tool_input.get("excluded_route_ids") else tool_input["max_candidates"]
                self.assertEqual(len(routes), expected)

    async def test_second_segment_failure_keeps_primary_routes_unchanged(self):
        origin = ResolvedPlace(name="Origin", latitude=40.71, longitude=-73.99, source="user")
        destination = ResolvedPlace(name="Delta Pkwy", latitude=40.3, longitude=-73.3, source="gtfs")
        first = [[{
            "type": "SUBWAY", "route_id": "Q", "departure_stop": "Origin",
            "arrival_stop": "Beta St", "arrival_coords": {"latitude": 40.1, "longitude": -73.1},
            "arrival_time_iso": "2026-08-23T12:10:00-04:00", "route_total_seconds": 600,
        }]]
        with patch.object(directions, "get_transit_route", new=AsyncMock(
            side_effect=[{}, GoogleRoutesError("timeout", "second segment failed")]
        )) as fetch, patch.object(directions, "parse_response", return_value=first):
            recovered = await plan_trip_input.recover_structural_route(
                directions_service=directions,
                pattern_index=StopPatternIndex(DISTINCT_TRANSFER_FIXTURE),
                primary_routes=self._primary(), origin=origin, destination=destination,
                destination_query="Test Dest", departure_time=None,
                allowed_modes=["SUBWAY", "BUS"], routing_preference="FEWER_TRANSFERS",
                excluded_route_ids=set(), excluded_modes=set(), telemetry={},
            )
        self.assertEqual(recovered, [])
        self.assertEqual(fetch.await_count, 2)


class PrepareSingleLegRecoveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovered_family_enters_evidence_scoring_and_hard_constraints(self):
        origin = ResolvedPlace(name="Origin", latitude=40.71, longitude=-73.99, source="user")
        destination = ResolvedPlace(name="Delta Pkwy", latitude=40.3, longitude=-73.3, source="gtfs")
        primary = [[{
            "type": "SUBWAY", "route_id": "Q", "departure_stop": "Alpha Av",
            "arrival_stop": "Epsilon Ctr", "departure_coords": {"latitude": 40.0, "longitude": -73.0},
            "arrival_coords": {"latitude": 40.4, "longitude": -73.4},
            "route_total_seconds": 600,
        }]]
        first = [[{
            "type": "SUBWAY", "route_id": "Q", "departure_stop": "Origin",
            "arrival_stop": "Beta St", "arrival_coords": {"latitude": 40.1, "longitude": -73.1},
            "arrival_time_iso": "2026-08-23T12:10:00-04:00", "route_total_seconds": 600,
        }]]
        second = [[{
            "type": "SUBWAY", "route_id": "R", "departure_stop": "Beta St",
            "arrival_stop": "Delta Pkwy", "departure_coords": {"latitude": 40.1005, "longitude": -73.1005},
            "arrival_time_iso": "2026-08-23T12:25:00-04:00", "route_total_seconds": 900,
        }]]
        observed = {"normalized": [], "mta": [], "incidents": [], "scoring": [], "evidence": {}}

        def normalize(routes, _gtfs=None):
            observed["normalized"].append(routes)
            return routes

        def score(routes, _alerts, **_kwargs):
            observed["scoring"].append(routes)
            return [
                {"index": index, "score": 1, "total_minutes": route[0]["route_total_seconds"] // 60}
                for index, route in enumerate(routes)
            ]

        def evidence(name, payload, **_kwargs):
            observed["evidence"][name] = payload
            return {"payload": payload}

        deps = SimpleNamespace(
            resolve_named_place=AsyncMock(),
            derive_arrive_by_departure=AsyncMock(),
            route_with_recovery=AsyncMock(return_value=primary),
            directions_service=directions,
            collect_alerts=AsyncMock(return_value=[{"header": "R status", "route_ids": ["R"]}]),
            collect_stalled_trains=AsyncMock(side_effect=lambda route_ids: observed["mta"].append(route_ids) or []),
            collect_stalled_buses=AsyncMock(return_value=[]),
            parse_service_alerts=lambda raw: raw,
            filter_alerts_for_routes=lambda alerts, route_ids: alerts if not observed["mta"].append(route_ids) else alerts,
            evidence_envelope=evidence,
            current_payload=lambda envelope, empty: envelope.get("payload", empty),
            scoring=SimpleNamespace(_score_routes=score),
            trip_incidents=SimpleNamespace(
                build_candidate_stop_context=lambda _gtfs, routes: observed["incidents"].append(routes) or {},
                scan_route_incidents=AsyncMock(return_value={"scan_metadata": {"status": "complete"}, "incidents": []}),
                incident_lookup_succeeded=lambda _metadata: False,
            ),
            crowd_hotspots=SimpleNamespace(find_hotspot_hits=lambda _gtfs, _routes: []),
            crowd_evidence=SimpleNamespace(collect=AsyncMock()),
            candidates=trip_candidates,
            route_service_ids=lambda route: {
                str(step.get("route_id") or "").upper()
                for step in route if step.get("type") == "SUBWAY"
            },
            context_timeout_seconds=5.0,
            live_evidence_ttl_seconds=60,
            event_evidence_ttl_seconds=60,
        )
        ctx = ToolContext(
            gtfs=SimpleNamespace(_pattern_index=StopPatternIndex(DISTINCT_TRANSFER_FIXTURE)),
            session={}, telemetry={}, session_id="recovery", turn_id="t1",
        )
        with patch.object(directions, "get_transit_route", new=AsyncMock(side_effect=[{}, {}])), \
                patch.object(directions, "parse_response", side_effect=[first, second]), \
                patch("app.services.agent.tools.route.preparation_adapter.normalize_routes", side_effect=normalize):
            prepared = await prepare_single_leg(
                {"origin": "user", "destination": "Delta Pkwy", "max_candidates": 2, "required_route_ids": ["R"]},
                ctx, {}, dependencies=deps, emit_comparing_progress=False,
                resolved_origin=origin, resolved_destination=destination,
            )

        self.assertIsInstance(prepared, PreparedLeg)
        self.assertEqual(len(prepared.parsed_routes), 1)
        self.assertEqual([step["route_id"] for step in prepared.parsed_routes[0]], ["Q", "R"])
        self.assertEqual(prepared.parsed_routes[0][0]["route_total_seconds"], 1500)
        self.assertEqual(
            [step["route_id"] for step in observed["scoring"][0][0]], ["Q", "R"]
        )
        self.assertEqual(
            [step["route_id"] for step in observed["incidents"][0][0]], ["Q", "R"]
        )
        self.assertIn("R", observed["mta"][0])
        self.assertTrue(ctx.telemetry["route_candidate_diagnostics"]["recovery_succeeded"])
        self.assertEqual(
            ctx.telemetry["route_candidate_diagnostics"]["final_structurally_unique_candidate_count"],
            2,
        )
        with patch("app.services.agent.tools.route.preparation_adapter.normalize_routes", side_effect=normalize):
            excluded = await prepare_single_leg(
                {
                    "origin": "user", "destination": "Delta Pkwy", "max_candidates": 2,
                    "excluded_route_ids": ["R"],
                },
                ctx, {}, dependencies=deps, emit_comparing_progress=False,
                resolved_origin=origin, resolved_destination=destination,
            )
        self.assertIsInstance(excluded, PreparedLeg)
        self.assertEqual([step["route_id"] for step in excluded.parsed_routes[0]], ["Q"])


if __name__ == "__main__":
    unittest.main()
