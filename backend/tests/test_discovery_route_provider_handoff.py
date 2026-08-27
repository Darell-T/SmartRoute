"""Focused tests for the discovery-to-route handoff (Phase 2A).

Covers routing by opaque destination_place_id, server-side canonical
identity preservation, the tool-start label, and the single-leg provider
handoff. Moved out of test_single_agent_route_tools.py so that phase does
not grow further.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.preparation_adapter import (
    PreparedLeg,
    prepare_single_leg,
)
from app.services.trips.preparation.dependencies import build_preparation_dependencies

from tests.discovery_route_handoff_test_support import (
    DiscoveryRouteHandoffTestMixin,
    _ctx,
)


class DiscoveryRouteProviderHandoffTests(
    DiscoveryRouteHandoffTestMixin, unittest.IsolatedAsyncioTestCase
):
    async def test_prepare_single_leg_passes_stored_identity_to_the_provider(self):
        stored_destination = ResolvedPlace(
            name="Di Fara Pizza",
            latitude=40.6298,
            longitude=-73.9616,
            source="discovery",
            address="1424 Av J",
            place_id="ChIJ-dest",
        )
        origin = ResolvedPlace("Your location", 40.75, -73.99, "user")
        route = [
            {
                "type": "WALK",
                "duration_seconds": 180,
                "departure_time_iso": "2026-08-06T12:00:00-04:00",
                "arrival_time_iso": "2026-08-06T12:03:00-04:00",
            },
            {
                "type": "SUBWAY",
                "route_id": "Q",
                "duration_seconds": 1200,
                "departure_stop": "Canal St",
                "arrival_stop": "Atlantic Av",
                "departure_time_iso": "2026-08-06T12:05:00-04:00",
                "arrival_time_iso": "2026-08-06T12:25:00-04:00",
            },
        ]
        provider_calls: list[dict] = []

        async def fake_route_with_recovery(**kwargs):
            provider_calls.append(dict(kwargs))
            return [route]

        deps = SimpleNamespace(
            resolve_named_place=AsyncMock(
                side_effect=AssertionError("resolve_named_place must not run")
            ),
            derive_arrive_by_departure=AsyncMock(
                side_effect=AssertionError("arrive-by must not run")
            ),
            route_with_recovery=fake_route_with_recovery,
            directions_service=SimpleNamespace(GoogleRoutesError=RuntimeError),
            collect_alerts=AsyncMock(return_value=[]),
            collect_stalled_trains=AsyncMock(return_value=[]),
            collect_stalled_buses=AsyncMock(return_value=[]),
            parse_service_alerts=lambda _raw: [],
            filter_alerts_for_routes=lambda _alerts, _route_ids: [],
            evidence_envelope=lambda name, payload, **_kwargs: {
                "name": name,
                "payload": payload,
            },
            current_payload=lambda envelope, empty: envelope.get("payload") or empty,
            scoring=SimpleNamespace(
                _score_routes=lambda routes, _alerts, **_kwargs: [
                    {
                        "index": 0,
                        "score": 1,
                        "total_minutes": 23,
                        "transfers": 0,
                    }
                    for _route in routes
                ]
            ),
            trip_incidents=SimpleNamespace(
                build_candidate_stop_context=lambda _gtfs, _routes: {},
                scan_route_incidents=AsyncMock(
                    return_value={
                        "scan_metadata": {
                            "status": "complete",
                            "sources": {"attempted": [], "completed": []},
                        },
                        "incidents": [],
                    }
                ),
                incident_scan_is_complete=lambda _metadata: True,
                incident_lookup_succeeded=lambda _metadata: True,
            ),
            crowd_hotspots=SimpleNamespace(find_hotspot_hits=lambda _gtfs, _routes: []),
            candidates=SimpleNamespace(
                _collect_route_and_bus_ids=lambda _routes: (set(), set())
            ),
            route_service_ids=lambda _route: set(),
            context_timeout_seconds=5.0,
            live_evidence_ttl_seconds=60,
            event_evidence_ttl_seconds=60,
        )
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.preparation_adapter.normalize_routes",
            new=lambda routes, _gtfs=None: routes,
        ):
            prepared = await prepare_single_leg(
                {
                    "origin": "user",
                    "destination": "Completely Different Text",
                    "destination_place_id": "pl_ignored_by_leg",
                },
                ctx,
                {},
                dependencies=deps,
                emit_comparing_progress=False,
                resolved_origin=origin,
                resolved_destination=stored_destination,
            )
        assert isinstance(prepared, PreparedLeg)
        assert len(provider_calls) == 1
        provider_input = provider_calls[0]
        assert provider_input["destination_query"] == "1424 Av J"
        assert provider_input["destination"].latitude == 40.6298
        assert provider_input["destination"].longitude == -73.9616
        assert provider_input["destination"].address == "1424 Av J"
        assert provider_input["destination"].place_id == "ChIJ-dest"
        serialized = json.dumps(provider_input, default=str)
        assert "Completely Different Text" not in serialized
        assert "pl_" not in serialized
        deps.resolve_named_place.assert_not_awaited()


class PrepareRouteOptionsLabelTests(unittest.TestCase):
    def test_neutral_dependencies_extract_transit_service_ids(self):
        dependencies = build_preparation_dependencies()

        assert dependencies.route_service_ids(
            [
                {"type": "SUBWAY", "route_id": "q"},
                {"type": "BUS", "route_id": "B35"},
                {"type": "WALK", "route_id": "ignored"},
                {"type": "SUBWAY", "route_id": ""},
            ]
        ) == {"Q", "B35"}

    def _label(self, tool_input: dict) -> str:
        from app.services.agent import tools as agent_tools

        return agent_tools._prepare_route_options_label(tool_input)

    def test_neutral_selected_place_label_when_destination_place_id_present(self):
        label = self._label(
            {
                "destination": "Completely Different Text",
                "destination_place_id": "pl_abc",
            }
        )
        assert "selected place" in label
        assert "Completely Different Text" not in label
        assert "pl_abc" not in label

    def test_label_never_echoes_an_opaque_destination_id(self):
        label = self._label({"destination": "pl_opaque"})
        assert "selected place" in label
        assert "pl_opaque" not in label

    def test_plain_destination_is_still_echoed(self):
        label = self._label({"destination": "Barclays Center"})
        assert "Barclays Center" in label
        assert "pl_" not in label

    def test_empty_input_uses_neutral_fallback(self):
        label = self._label({})
        assert "your destination" in label
