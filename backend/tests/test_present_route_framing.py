"""Focused route-presentation framing and canonical-fact guards."""
from __future__ import annotations
import unittest
from unittest.mock import AsyncMock, patch
from app.services.agent import candidate_store, trip_state
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route import present_route
from tests.single_agent_route_test_support import _prepared_leg
from tests.agent_route_decision_test_support import (
    _prepared_leg as _multi_prepared_leg,
    _route,
)

from tests.present_route_framing_test_support import (
    PresentRouteFramingTestMixin,
)


class PresentRouteFramingTests(PresentRouteFramingTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_generic_complication_free_framing_is_rejected(self):
        ctx, candidate_id, _set_id = await self._prepared_context()
        result = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "I found the route without any complications.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.events, [])
        self.assertIn("ungrounded success wording", result.error)

    async def test_framing_wraps_card_without_replacing_canonical_facts(self):
        ctx, candidate_id, set_id = await self._prepared_context()
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            result = await present_route.execute(
                {
                    "candidate_id": candidate_id,
                    "goal_key": "route",
                    "lead_in": (
                        "The route options were close, so I chose this one for your trip."
                    ),
                    "follow_up": "Want to see the walking details?",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(
            [event.type for event in result.events],
            ["token", "route_card"],
        )
        self.assertEqual(
            result.events[0].text,
            "The route options were close, so I chose this one for your trip.\n\n",
        )
        self.assertEqual(
            result.data["lead_in"],
            "The route options were close, so I chose this one for your trip.",
        )
        self.assertNotRegex(result.data["lead_in"], r"\d+\s+min")
        self.assertNotIn("transfer", result.data["lead_in"].casefold())
        self.assertEqual(result.data["follow_up"], "")
        self.assertEqual(result.data["reason_code"], "meets_hard_constraints")
        card = next(event for event in result.events if event.type == "route_card")
        self.assertEqual(card.summary["lines"], ["Q"])
        self.assertGreater(card.summary["eta_minutes"], 0)
        self.assertEqual(card.summary["transfers"], 0)
        self.assertEqual(
            result.data["candidates"][0]["eta_minutes"],
            card.summary["eta_minutes"],
        )
        # The card event keeps the server-owned selection record for the UI,
        # but the model-facing tool result must not expose score/rank data.
        self.assertNotIn("selection_decision", result.data)
        self.assertNotIn("selected_route_index", result.data)
        self.assertNotIn("quick_escalation_reason", result.data)
        for private_key in (
            "card_id",
            "event_crowd_penalty",
            "selection_score",
            "selection_rank",
            "score_breakdown",
            "score_summary",
            "structured_recommendation_reasons",
            "reason",
        ):
            self.assertNotIn(private_key, result.data["candidates"][0])
        self.assertEqual(
            card.selection_decision["selection_reason"],
            "outer_agent_selection",
        )
        self.assertEqual(
            candidate_store.load_candidate_set(
                set_id, session_id="sess-route-framing"
            )["presented"],
            True,
        )

    async def test_natural_framing_does_not_change_selection_or_canonical_payload(self):
        volatile_fields = {
            "candidate_set_id",
            "created_at",
            "expires_at",
            "generated_at",
            "itinerary_id",
            "presentation_reserved_at",
            "snapshot_id",
            "snapshot_observed_at",
            "observed_at",
            "data_freshness",
            "evidence_snapshot",
            "_evidence_snapshot",
            "timings",
            "plan_origin",
        }

        def stable(value):
            if isinstance(value, dict):
                return {
                    key: stable(item)
                    for key, item in value.items()
                    if key not in volatile_fields
                }
            if isinstance(value, list):
                return [stable(item) for item in value]
            return value

        destination = ResolvedPlace(
            "Barclays Center",
            40.6826,
            -73.9754,
            "fixture",
        )
        prepared = _multi_prepared_leg(
            destination="Barclays Center",
            destination_place=destination,
            routes=[
                _route(route_ids=("Q",), walking_seconds=60, total_seconds=1200),
                _route(route_ids=("B",), walking_seconds=240, total_seconds=1800),
            ],
        )

        async def present_with_framing(
            lead_in: str,
            *,
            correction: bool = False,
        ):
            with patch(
                "app.services.agent.candidate_store.new_candidate_id",
                side_effect=["cd_winner", "cd_alternative"],
            ):
                ctx, _candidate_id, set_id = await self._prepared_context(prepared)
            record_before = candidate_store.load_candidate_set(
                set_id, session_id=ctx.session_id
            )
            with patch(
                "app.services.trips.enrichment._enrich_route",
                new=AsyncMock(return_value=None),
            ):
                if correction:
                    rejected = await present_route.execute(
                        {
                            "candidate_id": "cd_winner",
                            "goal_key": "route",
                            "lead_in": "This route takes 30 minutes.",
                            "follow_up": "",
                            "reason_code": "meets_hard_constraints",
                        },
                        ctx,
                    )
                    self.assertFalse(rejected.ok)
                    self.assertEqual(rejected.events, [])
                result = await present_route.execute(
                    {
                        "candidate_id": "cd_winner",
                        "goal_key": "route",
                        "lead_in": lead_in,
                        "follow_up": "",
                        "reason_code": "meets_hard_constraints",
                    },
                    ctx,
                )
            self.assertTrue(result.ok, result.error)
            card = next(event for event in result.events if event.type == "route_card")
            record = candidate_store.load_candidate_set(
                set_id, session_id=ctx.session_id
            )
            state = trip_state.get_trip_state(ctx.session)
            return result, card, record_before, record, state

        first = await present_with_framing(
            "The route options were close, so I chose this one for your trip."
        )
        corrected = await present_with_framing(
            "Nothing stood out between these options, so I chose this route for you.",
            correction=True,
        )

        first_result, first_card, first_before, first_record, first_state = first
        (
            corrected_result,
            corrected_card,
            corrected_before,
            corrected_record,
            corrected_state,
        ) = corrected
        for result, card, before, record, state in (first, corrected):
            self.assertEqual(state["selected_candidate_id"], "cd_winner")
            self.assertEqual(record["selected_candidate_id"], "cd_winner")
            self.assertEqual(
                [entry["candidate_id"] for entry in before["candidates"]],
                ["cd_winner", "cd_alternative"],
            )
            self.assertEqual(
                [entry["candidate_id"] for entry in record["candidates"]],
                ["cd_winner", "cd_alternative"],
            )
            snapshot = card.itinerary["evidence_snapshot"]
            selected_entry = next(
                entry
                for entry in before["candidates"]
                if entry["candidate_id"] == "cd_winner"
            )
            expected_snapshot = selected_entry["digest"]["evidence_snapshot"]
            self.assertEqual(snapshot, expected_snapshot)
            self.assertEqual(result.data["reason_code"], "meets_hard_constraints")

        self.assertEqual(stable(first_before), stable(corrected_before))
        self.assertEqual(
            stable(first_result.data["candidates"]),
            stable(corrected_result.data["candidates"]),
        )
        self.assertEqual(
            stable(first_result.data["evidence"]),
            stable(corrected_result.data["evidence"]),
        )
        self.assertEqual(stable(first_card.itinerary), stable(corrected_card.itinerary))
        self.assertEqual(first_card.destination, corrected_card.destination)
        self.assertEqual(
            stable(
                {
                    key: value
                    for key, value in first_card.summary.items()
                    if key != "reason"
                }
            ),
            stable(
                {
                    key: value
                    for key, value in corrected_card.summary.items()
                    if key != "reason"
                }
            ),
        )
        self.assertEqual(
            first_result.data["selection_source"],
            corrected_result.data["selection_source"],
        )
        self.assertEqual(
            first_card.selection_decision["selection_reason"],
            corrected_card.selection_decision["selection_reason"],
        )

    async def test_live_evidence_stays_on_the_card_without_being_narrated(self):
        prepared = _prepared_leg()
        prepared.relevant_alerts = [{"header": "Q trains are delayed"}]
        prepared.incidents = [{"description": "Track obstruction near Church Av"}]
        ctx, candidate_id, _set_id = await self._prepared_context(prepared)
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
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

        self.assertTrue(result.ok, result.error)
        self.assertEqual(
            result.data["candidates"][0]["alert_headlines"],
            ["Q trains are delayed"],
        )
        self.assertNotIn("Q trains are delayed", result.data["lead_in"])
        self.assertNotIn("Track obstruction", result.data["lead_in"])

    async def test_partial_crowd_coverage_is_explained_without_claiming_safety(self):
        prepared = _prepared_leg()
        prepared.event_evidence_status = "partial"
        prepared.collect_crowd_evidence = True
        ctx, candidate_id, _set_id = await self._prepared_context(
            prepared,
            prepare_input={"avoid_crowds": True},
        )
        result = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "The route options were close, and I could not verify crowd conditions for the relevant window.",
                "follow_up": "",
                "reason_code": "coverage_gap",
            },
            ctx,
        )

        self.assertTrue(result.ok, result.error)
        self.assertIn(
            "crowd conditions for the relevant window could not be verified",
            result.data["lead_in"],
        )
        self.assertNotIn("avoid_crowds", result.data["lead_in"])
        self.assertNotIn("lower event crowd exposure", result.data["lead_in"])

    async def test_incomplete_incident_all_clear_is_rejected_before_projection(self):
        prepared = _prepared_leg()
        prepared.incident_scan_metadata["status"] = "partial"
        ctx, candidate_id, set_id = await self._prepared_context(prepared)
        unsafe = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": "Fastest among the checked routes; no active incidents.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )

        self.assertFalse(unsafe.ok)
        self.assertEqual(unsafe.events, [])
        self.assertIn("incident coverage is incomplete", unsafe.error.casefold())
        stored = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        self.assertFalse(stored["presented"])
        self.assertIsNone(stored["selected_candidate_id"])

        safe = await present_route.execute(
            {
                "candidate_id": candidate_id,
                "goal_key": "route",
                "lead_in": (
                    "The available options were close, so I chose this one for you; "
                    "incident coverage is incomplete."
                ),
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )

        self.assertTrue(safe.ok, safe.error)
        self.assertEqual([event.type for event in safe.events], ["token", "route_card"])
        self.assertNotIn("no active incidents", safe.events[0].text.casefold())
        self.assertIn("incident coverage is incomplete", safe.events[0].text.casefold())
        card = safe.events[1]
        self.assertEqual(card.summary["reason"], safe.data["lead_in"])
        self.assertEqual(
            candidate_store.load_candidate_set(
                set_id, session_id=ctx.session_id
            )["selected_candidate_id"],
            candidate_id,
        )

    async def test_unknown_candidate_is_rejected_before_deterministic_fallback(self):
        ctx, _candidate_id, set_id = await self._prepared_context()
        result = await present_route.execute(
            {
                "candidate_id": "cd_stale_model_choice",
                "goal_key": "route",
                "lead_in": "The route options were close, so I chose this one for your trip.",
                "follow_up": "",
                "reason_code": "meets_hard_constraints",
            },
            ctx,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "candidate id is unknown for this set")
        self.assertEqual(result.events, [])
        self.assertEqual(
            trip_state.get_trip_state(ctx.session)["selected_candidate_id"],
            None,
        )
        stored = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        self.assertFalse(stored["presented"])
        self.assertIsNone(stored["selected_candidate_id"])

    async def test_malformed_destination_selection_shape_fails_before_presentation(self):
        for malformed in (
            {
                "destination_selection_mode": "single",
                "destination_place_ids": ["pl_first", "pl_second"],
            },
            {"destination_selection_mode": "unexpected"},
        ):
            with self.subTest(malformed=malformed):
                ctx, candidate_id, set_id = await self._prepared_context()
                stored_get_candidate = candidate_store.get_candidate

                def malformed_candidate(*args, **kwargs):
                    record, entry, error = stored_get_candidate(*args, **kwargs)
                    self.assertIsNotNone(record)
                    record.update(malformed)
                    return record, entry, error

                with patch(
                    "app.services.agent.candidate_store.get_candidate",
                    side_effect=malformed_candidate,
                ):
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

                self.assertFalse(result.ok)
                self.assertTrue(result.internal_diagnostic)
                self.assertEqual(
                    result.error,
                    "candidate set has an invalid destination selection shape",
                )
                self.assertEqual(result.events, [])
                record = candidate_store.load_candidate_set(
                    set_id,
                    session_id=ctx.session_id,
                )
                self.assertFalse(record["presented"])
                self.assertIsNone(record["selected_candidate_id"])
