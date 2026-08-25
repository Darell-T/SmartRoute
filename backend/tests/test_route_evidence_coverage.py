"""Tests for the unflagged prepare_route_options / present_route path."""

from __future__ import annotations
import copy
import unittest
from unittest.mock import AsyncMock, patch
from app.services.agent import candidate_store
from app.services.agent.tools.route import (
    prepare_route_options,
)
from app.services.trips.preparation.constraints import (
    candidate_digest,
    route_status,
)
from app.services.trips.preparation import evidence as route_option_evidence

from tests.single_agent_route_test_support import (
    _ctx,
    _prepared_leg,
)


class RouteEvidenceCoverageTests(unittest.IsolatedAsyncioTestCase):
    """not_required event evidence is an explicit neutral coverage state."""

    def _leg(self, *, event_evidence_status="not_required", envelopes=None):
        leg = copy.copy(_prepared_leg())
        leg.event_evidence_status = event_evidence_status
        leg.evidence_envelopes = envelopes or {}
        return leg

    def test_coverage_for_prepared_maps_not_required_events_explicitly(self):
        self.assertEqual(
            route_option_evidence.coverage_for_prepared(self._leg())["events"],
            "not_required",
        )
        for status, expected in (
            ("available", "current"),
            ("no_relevant_events", "current"),
            ("complete", "current"),
            ("provider_unavailable", "unavailable"),
            ("failed", "unavailable"),
            ("partial", "partial"),
            ("unknown", "unscanned"),
        ):
            self.assertEqual(
                route_option_evidence.coverage_for_prepared(
                    self._leg(event_evidence_status=status)
                )["events"],
                expected,
                f"events coverage for raw status {status!r}",
            )

    def test_serialized_envelope_merge_preserves_worst_status_and_provenance(self):
        merged = route_option_evidence.merge_serialized_envelopes(
            [
                {
                    "alerts": {
                        "source": "mta",
                        "observedAt": "2026-08-24T12:02:00Z",
                        "validUntil": "2026-08-24T12:04:00Z",
                        "status": "current",
                        "payload": [{"id": "a"}],
                    }
                },
                {
                    "alerts": {
                        "observedAt": "2026-08-24T12:01:00Z",
                        "validUntil": "2026-08-24T12:03:00Z",
                        "status": "stale",
                        "payload": [{"id": "a"}, {"id": "b"}],
                    },
                    "vehicles": {"status": "current", "payload": []},
                },
            ]
        )

        self.assertEqual(
            merged["alerts"],
            {
                "source": "mta",
                "observedAt": "2026-08-24T12:01:00Z",
                "validUntil": "2026-08-24T12:03:00Z",
                "status": "stale",
                "payload": [{"id": "a"}, {"id": "b"}],
            },
        )
        self.assertEqual(
            merged["vehicles"],
            {
                "source": "unknown",
                "observedAt": "",
                "status": "current",
                "payload": [],
            },
        )

    def test_current_route_with_not_required_events_stays_good(self):
        self.assertEqual(
            route_status(
                candidates=[{"hard_constraints_satisfied": True}],
                coverage={
                    "mta": "current",
                    "vehicles": "current",
                    "incidents": "current",
                    "events": "not_required",
                },
                incident_impacts=[],
            ),
            "good",
        )

    def test_planned_operating_alert_stays_usable_but_material_alert_degrades(self):
        planned_local = {
            "source": "mta_service_alerts",
            "source_id": "lmm:planned_work:33095",
            "alert_id": "lmm:planned_work:33095",
            "route_ids": ["Q"],
            "planned_status": "planned",
            "change_type": "express_to_local",
            "service_operating": True,
            "material_disruption": False,
            "header": "Q express trains run local",
        }
        coverage = {"mta": "current", "events": "not_required"}
        self.assertEqual(
            route_status(
                candidates=[
                    {
                        "hard_constraints_satisfied": True,
                        "official_service_impacts": [planned_local],
                    }
                ],
                coverage=coverage,
                incident_impacts=[],
            ),
            "good",
        )
        self.assertEqual(
            route_status(
                candidates=[
                    {
                        "hard_constraints_satisfied": True,
                        "official_service_impacts": [
                            {
                                **planned_local,
                                "header": "Q service suspended",
                                "service_operating": False,
                                "material_disruption": True,
                            }
                        ],
                    }
                ],
                coverage=coverage,
                incident_impacts=[],
            ),
            "all_materially_degraded",
        )
        self.assertEqual(
            route_status(
                candidates=[
                    {
                        "hard_constraints_satisfied": True,
                        "official_service_impacts": [
                            {"header": "Unknown Q service notice", "route_ids": ["Q"]}
                        ],
                    }
                ],
                coverage=coverage,
                incident_impacts=[],
            ),
            "all_materially_degraded",
        )

    def test_candidate_digest_and_accepted_comparison_preserve_typed_alert(self):
        route = _prepared_leg().parsed_routes[0]
        planned_local = {
            "source": "mta_service_alerts",
            "source_id": "lmm:planned_work:33095",
            "alert_id": "lmm:planned_work:33095",
            "route_ids": ["Q"],
            "stop_ids": ["Q01N", "Q01S"],
            "direction_ids": ["0", "1"],
            "direction_scope": "both_directions",
            "planned_status": "planned",
            "change_type": "express_to_local",
            "service_operating": True,
            "material_disruption": False,
            "header": "Q express trains run local",
            "effective_start": 1_778_595_000,
            "effective_end": 1_778_599_000,
            "feed_observed_at": "2026-05-12T14:00:00+00:00",
            "local_verified_at": "2026-05-12T14:01:00+00:00",
        }
        score = {
            "index": 0,
            "total_minutes": 23,
            "transfers": 0,
            "alert_count": 0,
            "event_crowd_penalty": 0,
        }
        digest = candidate_digest(
            route=route,
            candidate_id="selected",
            score=score,
            alerts=[planned_local],
            incidents=[],
            event_impacts=[],
            prepared_arrival_by=None,
            hard_constraints={"satisfied": True},
        )
        self.assertEqual(
            digest["official_service_impacts"][0]["source_id"],
            "lmm:planned_work:33095",
        )
        self.assertEqual(
            digest["official_service_impacts"][0]["change_type"],
            "express_to_local",
        )
        self.assertFalse(digest["official_service_impacts"][0]["material_disruption"])
        self.assertEqual(digest["score_summary"]["reliability"], "high")

        record = {
            "candidates": [
                {"candidate_id": "selected", "digest": digest},
                {
                    "candidate_id": "alternative",
                    "digest": {
                        **digest,
                        "candidate_id": "alternative",
                        "official_service_impacts": [],
                    },
                },
            ]
        }
        comparison = candidate_store.accepted_route_comparison(record, "selected")
        self.assertIsNotNone(comparison)
        selected = comparison["options"][0]
        self.assertFalse(selected["service_conditions"]["official_service_impact"])
        self.assertTrue(selected["service_conditions"]["official_service_change"])
        self.assertEqual(
            selected["official_alerts"][0]["source_id"],
            "lmm:planned_work:33095",
        )
        self.assertEqual(
            selected["official_alerts"][0]["change_type"],
            "express_to_local",
        )

    def test_only_neutral_or_unusable_coverage_is_insufficient(self):
        self.assertEqual(
            route_status(
                candidates=[{"hard_constraints_satisfied": True}],
                coverage={"events": "not_required"},
                incident_impacts=[],
            ),
            "insufficient_coverage",
        )
        self.assertEqual(
            route_status(
                candidates=[{"hard_constraints_satisfied": True}],
                coverage={"events": "not_required", "mta": "unavailable"},
                incident_impacts=[],
            ),
            "insufficient_coverage",
        )

    def test_applicable_degradation_statuses_still_degrade(self):
        for degraded in ("partial", "stale", "unavailable", "unscanned"):
            self.assertEqual(
                route_status(
                    candidates=[{"hard_constraints_satisfied": True}],
                    coverage={"mta": "current", "events": degraded},
                    incident_impacts=[],
                ),
                "degraded_usable",
                f"events coverage {degraded!r} must degrade",
            )
            self.assertEqual(
                route_status(
                    candidates=[{"hard_constraints_satisfied": True}],
                    coverage={
                        "mta": degraded,
                        "incidents": "current",
                        "events": "not_required",
                    },
                    incident_impacts=[],
                ),
                "degraded_usable",
                f"mta coverage {degraded!r} must degrade despite neutral events",
            )

    def test_merge_coverage_keeps_not_required_neutral_across_legs(self):
        cases = (
            (("not_required", "not_required"), "not_required"),
            (("not_required", "available"), "current"),
            (("available", "not_required"), "current"),
            (("not_required", "no_relevant_events"), "current"),
            (("not_required", "provider_unavailable"), "unavailable"),
            (("provider_unavailable", "not_required"), "unavailable"),
        )
        for statuses, expected in cases:
            legs = [self._leg(event_evidence_status=status) for status in statuses]
            self.assertEqual(
                route_option_evidence.merge_coverage(legs)["events"],
                expected,
                f"events merge for {statuses!r}",
            )

    async def test_prepare_fixture_reports_not_required_events_truthfully(self):
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=_prepared_leg()),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx,
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["evidence_coverage"]["events"], "not_required")

    async def test_prepare_route_options_not_downgraded_by_not_required_events(self):
        from app.services.evidence import evidence_envelope

        leg = self._leg(
            envelopes={
                "alerts": evidence_envelope("mta_alerts", [], ttl_seconds=60),
                "subway_vehicles": evidence_envelope("mta_gtfs_rt", [], ttl_seconds=60),
            }
        )
        ctx = _ctx()
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=leg),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                },
                ctx,
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["evidence_coverage"]["events"], "not_required")
        self.assertEqual(result.data["route_status"], "good")
