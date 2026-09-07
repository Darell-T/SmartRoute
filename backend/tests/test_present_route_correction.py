"""Focused route-presentation framing and canonical-fact guards."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.services.agent import candidate_store, trip_state
from app.services.agent.tools.route import present_route
from app.services.trips import scoring

from tests.present_route_framing_test_support import (
    PresentRouteFramingTestMixin,
)
from tests.single_agent_route_test_support import _ctx, _prepared_leg


class PresentRouteCorrectionTests(PresentRouteFramingTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_present_does_not_refresh_canonical_snapshot(self):
        ctx, candidate_id, _set_id = await self._prepared_context()
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(side_effect=AssertionError("snapshot was refreshed")),
        ) as enrich:
            result = await present_route.execute(
                {
                    "candidate_id": candidate_id,
                    "goal_key": "route",
                    "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )

        assert result.ok, result.error
        enrich.assert_not_awaited()

    async def test_dominated_selection_is_rejected_before_card_or_reservation(self):
        selected, _alternative, record = self._dominated_selection_facts()
        owned = {
            "candidate_set_id": "cs-dominated",
            "candidate_id": "cd-selected",
            "goal_key": "route",
            "record": record,
            "entry": selected,
        }
        facts = {**owned}
        ctx = _ctx("sess-dominated")
        with (
            patch.object(present_route, "_owned_candidate", return_value=owned),
            patch.object(
                present_route,
                "canonical_facts",
                return_value=facts,
            ),
            patch.object(present_route, "_reserve_and_commit", Mock()) as reserve,
        ):
            result = await present_route.execute(
                {
                    "candidate_id": "cd-selected",
                    "goal_key": "route",
                    "lead_in": "The route options were close, so I chose this one.",
                    "follow_up": "",
                    "reason_code": "less_walking",
                },
                ctx,
            )

        assert not result.ok
        assert result.internal_diagnostic
        assert result.events == []
        assert "existing Candidate Set" in result.error
        assert "Do not prepare routes again" in result.error
        reserve.assert_not_called()
        assert trip_state.get_trip_state(ctx.session)["selected_candidate_id"] is None

    def test_dominated_selection_uses_existing_correction_then_fallback(self):
        selected, alternative, record = self._dominated_selection_facts()
        owned = {
            "candidate_set_id": "cs-dominated",
            "candidate_id": "cd-selected",
            "goal_key": "route",
            "record": record,
            "entry": selected,
        }
        facts = {**owned}
        fallback_facts = {**facts, "entry": alternative}
        ctx = _ctx("sess-dominated-fallback")
        with patch.object(
            present_route,
            "canonical_facts",
            side_effect=[facts, facts, fallback_facts],
        ):
            first = present_route.canonical_facts_with_fallback(owned, ctx)
            second = present_route.canonical_facts_with_fallback(owned, ctx)

        assert not first.ok
        assert first.internal_diagnostic
        assert "existing Candidate Set" in first.error
        assert isinstance(second, dict)
        assert second["entry"] is alternative
        assert second["selection_source"] == "deterministic_fallback"
        assert second["selection_reason"] == "deterministic_fallback"
        assert ctx.telemetry["route_decision_corrections"]["route:cs-dominated"] == 2

    @staticmethod
    def _dominated_selection_facts():
        selected = {
            "candidate_id": "cd-selected",
            "index": 0,
            "digest": {
                "hard_constraints_satisfied": True,
                "duration_minutes": 75,
                "walking_minutes": 6,
                "transfers": 1,
                "official_service_impacts": [],
                "confirmed_incident_impacts": [],
                "unconfirmed_material_claims": [
                    {"type": "vehicle_signal"},
                ],
                "soft_preferences": {
                    "routing_preference": "LESS_WALKING",
                    "routing_preference_source": "current_turn",
                },
            },
        }
        alternative = {
            "candidate_id": "cd-alternative",
            "index": 1,
            "digest": {
                "hard_constraints_satisfied": True,
                "duration_minutes": 25,
                "walking_minutes": 4,
                "transfers": 1,
                "official_service_impacts": [],
                "confirmed_incident_impacts": [],
                "unconfirmed_material_claims": [],
                "soft_preferences": {
                    "routing_preference": "LESS_WALKING",
                    "routing_preference_source": "current_turn",
                },
            },
        }
        record = {
            "candidate_set_id": "cs-dominated",
            "candidates": [selected, alternative],
            "scored": [
                {"index": 0, "score": 75, "total_minutes": 75, "transfers": 1},
                {"index": 1, "score": 25, "total_minutes": 25, "transfers": 1},
            ],
        }
        return selected, alternative, record

    async def test_invalid_framing_requests_correction_before_card(self):
        for field, value in (
            ("lead_in", "Use candidate cd_route_internal."),
            ("follow_up", "x" * 241),
        ):
            with self.subTest(field=field):
                ctx, candidate_id, set_id = await self._prepared_context()
                result = await present_route.execute(
                    {
                        "candidate_id": candidate_id,
                        "goal_key": "route",
                        "lead_in": value if field == "lead_in" else "",
                        "follow_up": value if field == "follow_up" else "",
                        "reason_code": "meets_hard_constraints",
                    },
                    ctx,
                )
                assert not result.ok
                assert result.internal_diagnostic
                assert result.events == []
                record = candidate_store.load_candidate_set(
                    set_id, session_id="sess-route-framing"
                )
                assert not record["presented"]
                assert trip_state.get_trip_state(ctx.session)["selected_candidate_id"] is None

    async def test_canonical_route_facts_request_correction_before_card_emission(self):
        for field, value in (
            ("lead_in", "This route takes 30 minutes."),
            ("follow_up", "It has one transfer."),
            ("lead_in", "Take the Q train."),
            ("follow_up", "Ride B35."),
        ):
            with self.subTest(field=field, value=value):
                ctx, candidate_id, set_id = await self._prepared_context()
                result = await present_route.execute(
                    {
                        "candidate_id": candidate_id,
                        "goal_key": "route",
                        "lead_in": value if field == "lead_in" else "",
                        "follow_up": value if field == "follow_up" else "",
                        "reason_code": "meets_hard_constraints",
                    },
                    ctx,
                )
                assert not result.ok
                assert result.internal_diagnostic
                assert result.events == []
                assert not candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)["presented"]

    async def test_route_framing_requires_a_supported_reason_reference(self):
        ctx, candidate_id, _set_id = await self._prepared_context()
        missing_reason = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "I found a route that fits.",
                "follow_up": "",
            },
            ctx,
        )
        assert not missing_reason.ok
        assert missing_reason.internal_diagnostic
        assert missing_reason.events == []

        ctx, candidate_id, _set_id = await self._prepared_context()
        unsupported_reason = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "I found a route that fits.",
                "follow_up": "",
                "reason_code": "fewer_transfers",
            },
            ctx,
        )
        assert not unsupported_reason.ok
        assert unsupported_reason.internal_diagnostic
        assert unsupported_reason.events == []
        assert "reason_code values supported by the selected candidate:" in unsupported_reason.error
        assert "meets_hard_constraints" in unsupported_reason.error

    async def test_generic_framing_is_rejected_even_with_supported_reason(self):
        for lead_in in (
            "This option fits the trip you requested.",
            "This is the best simple option.",
            "This is the best option.",
        ):
            with self.subTest(lead_in=lead_in):
                ctx, candidate_id, _set_id = await self._prepared_context()
                result = await present_route.execute(
                    {
                        "candidate_id": candidate_id,
                        "goal_key": "route",
                        "lead_in": lead_in,
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                    ctx,
                )

                assert not result.ok
                assert result.internal_diagnostic
                assert "concrete supported route factor" in result.error
                assert result.events == []

    async def test_concrete_best_wording_is_allowed_when_grounded(self):
        ctx, candidate_id, _set_id = await self._prepared_context()
        result = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": (
                    "It was best on the available route shape; nothing stood out "
                    "as a clear edge."
                ),
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )
        assert result.ok, result.error

    async def test_explicit_fastest_claim_cannot_use_hard_constraint_reason(self):
        ctx, candidate_id, _set_id = await self._prepared_context()
        result = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "Fastest among the checked routes.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )

        assert not result.ok
        assert result.internal_diagnostic
        assert "different route factor" in result.error
        assert result.events == []

    async def test_supported_correction_emits_explanation_and_card(self):
        ctx, candidate_id, _set_id = await self._prepared_context()
        rejected = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "I found a route that fits your request.",
                "follow_up": "Want help with anything else before you go?",
                "reason_code": "fewer_transfers",
            },
            ctx,
        )
        assert not rejected.ok

        result = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "Want help with anything else before you go?",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )

        assert result.ok, result.error
        assert [event.type for event in result.events] == ["token", "route_card"]
        assert result.data["reason_code"] == "meets_hard_constraints"
        assert result.data["follow_up"] == ""

    async def test_neutral_follow_up_accompanies_a_grounded_explanation(self):
        ctx, candidate_id, _set_id = await self._prepared_context()
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            result = await present_route.execute(
                {
                    "candidate_id": candidate_id,
                    "goal_key": "route",
                    "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "Want help with anything else before you go?",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )

        assert result.ok, result.error
        assert [event.type for event in result.events] == ["token", "route_card"]
        assert result.data["follow_up"] == ""

    async def test_missing_explanation_gets_one_correction_before_grounded_fallback(self):
        ctx, candidate_id, set_id = await self._prepared_context()
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            first = await present_route.execute(
                {
                    "candidate_id": candidate_id,
                    "goal_key": "route",
                    "lead_in": "",
                    "follow_up": "",
                },
                ctx,
            )
            assert not candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)["presented"]
            second = await present_route.execute(
                {
                    "candidate_id": candidate_id,
                    "goal_key": "route",
                    "lead_in": "",
                    "follow_up": "",
                },
                ctx,
            )

        assert not first.ok
        assert first.internal_diagnostic
        assert first.events == []
        assert second.ok, second.error
        assert [event.type for event in second.events] == ["token", "route_card"]
        assert second.data["reason_code"] in {"coverage_gap", "meets_hard_constraints"}
        assert second.data["lead_in"].startswith("Here's the route I found.")
        card = next(event for event in second.events if event.type == "route_card")
        assert card.selection_decision["selection_source"] == "deterministic_fallback"
        assert card.selection_decision["reason_code"] == second.data["reason_code"]

    async def test_invalid_model_choice_falls_back_to_other_candidate_atomically(self):
        prepared = _prepared_leg()
        fast_route = copy.deepcopy(prepared.parsed_routes[0])
        model_route = copy.deepcopy(prepared.parsed_routes[0])
        fast_route[0]["route_total_seconds"] = 900
        model_route[0]["route_total_seconds"] = 1800
        prepared.parsed_routes = [fast_route, model_route]
        prepared.scored = [
            {
                "index": index,
                **scoring._route_score(
                    route,
                    [],
                    route_index=index,
                    routing_preference="FEWER_TRANSFERS",
                ),
            }
            for index, route in enumerate(prepared.parsed_routes)
        ]
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            side_effect=["cd_fast", "cd_model"],
        ):
            ctx, _candidate_id, set_id = await self._prepared_context(prepared)
            with patch(
                "app.services.trips.enrichment._enrich_route",
                new=AsyncMock(return_value=None),
            ):
                first = await present_route.execute(
                    {
                        "candidate_id": "cd_model",
                        "goal_key": "route",
                        "lead_in": "I chose this option for fewer transfers.",
                        "follow_up": "",
                        "reason_code": "fewer_transfers",
                    },
                    ctx,
                )
                second = await present_route.execute(
                    {
                        "candidate_id": "cd_model",
                        "goal_key": "route",
                        "lead_in": "I chose this option for fewer transfers.",
                        "follow_up": "",
                        "reason_code": "fewer_transfers",
                    },
                    ctx,
                )

        assert not first.ok
        assert second.ok, second.error
        card = next(event for event in second.events if event.type == "route_card")
        decision = card.selection_decision
        for private_key in (
            "selected_candidate_id",
            "selected_candidate_index",
            "base_score",
            "final_score",
            "penalties",
            "evidence_ids",
        ):
            assert private_key not in decision
        assert card.itinerary["selection_decision"] == decision
        assert decision["selection_source"] == "deterministic_fallback"
        assert decision["selection_reason"] == "deterministic_fallback"
        assert decision["reason_code"] == "fastest"
        assert card.itinerary["total_duration_seconds"] == 900
        state = trip_state.get_trip_state(ctx.session)
        assert state["selected_candidate_id"] == "cd_fast"
        record = candidate_store.load_candidate_set(
            set_id, session_id=ctx.session_id
        )
        assert record["presented"]
        assert record["selected_candidate_id"] == "cd_fast"

    async def test_hard_constraint_violation_gets_one_retry_before_fallback(self):
        prepared = _prepared_leg()
        valid_route = copy.deepcopy(prepared.parsed_routes[0])
        invalid_route = copy.deepcopy(prepared.parsed_routes[0])
        valid_route[0]["arrival_time_iso"] = "2026-08-06T12:01:00-04:00"
        invalid_route[0]["arrival_time_iso"] = "2026-08-06T12:03:00-04:00"
        prepared.parsed_routes = [valid_route, invalid_route]
        prepared.scored = [
            {
                "index": index,
                **scoring._route_score(
                    route,
                    [],
                    route_index=index,
                    routing_preference="FEWER_TRANSFERS",
                ),
            }
            for index, route in enumerate(prepared.parsed_routes)
        ]
        with patch(
            "app.services.agent.candidate_store.new_candidate_id",
            side_effect=["cd_valid", "cd_hard_invalid"],
        ):
            ctx, _candidate_id, set_id = await self._prepared_context(
                prepared,
                prepare_input={"walking_tolerance_minutes": 2},
            )
            first = await present_route.execute(
                {
                    "candidate_id": "cd_hard_invalid",
                    "goal_key": "route",
                    "lead_in": "This option fits the trip you requested.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )
            record_after_first = candidate_store.load_candidate_set(
                set_id, session_id=ctx.session_id
            )
            selected_after_first = trip_state.get_trip_state(ctx.session)[
                "selected_candidate_id"
            ]
            corrections_after_first = ctx.telemetry[
                "route_decision_corrections"
            ][f"route:{set_id}"]
            second = await present_route.execute(
                {
                    "candidate_id": "cd_hard_invalid",
                    "goal_key": "route",
                    "lead_in": "This option fits the trip you requested.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )

        assert not first.ok
        assert first.internal_diagnostic
        assert first.events == []
        assert not record_after_first["presented"]
        assert record_after_first["selected_candidate_id"] is None
        assert selected_after_first is None
        assert corrections_after_first == 1

        assert second.ok, second.error
        card = next(event for event in second.events if event.type == "route_card")
        assert "selected_candidate_id" not in card.selection_decision
        assert "selected_candidate_index" not in card.selection_decision
        assert card.itinerary["selection_decision"] == card.selection_decision
        assert card.selection_decision["selection_source"] == "deterministic_fallback"
        assert card.itinerary["total_street_walking_seconds"] == 60
        assert trip_state.get_trip_state(ctx.session)["selected_candidate_id"] == "cd_valid"
        record = candidate_store.load_candidate_set(
            set_id, session_id=ctx.session_id
        )
        assert record["selected_candidate_id"] == "cd_valid"
