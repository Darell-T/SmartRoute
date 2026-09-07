"""Tests for the unflagged prepare_route_options / present_route path."""
from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, transcript_store, trip_state
from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.route import (
    prepare_route_options,
    present_route,
)
from app.services.agent.turn.contract import GoalKind, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence

from tests.single_agent_route_test_support import (
    _ctx,
    _prepared_leg,
    _present_route_input,
)


def _accepted_route_card() -> dict:
    itinerary = {
        "legs": [{"mode": "SUBWAY", "route_id": "Q"}],
        "total_duration_seconds": 1380,
    }
    return {
        "card_id": "rc_accepted",
        "turn_id": "t1",
        "role": "recommended",
        "origin": {"label": "Your location"},
        "destination": {"label": "Barclays Center"},
        "summary": {"eta_minutes": 23, "transfers": 0, "lines": ["Q"]},
        "route": [{"type": "SUBWAY", "route_id": "Q"}],
        "alerts": [],
        "leg_label": "direct",
        "depart_iso": "2026-08-06T12:00:00-04:00",
        "itinerary": itinerary,
        "selection_decision": {"reason_code": "fastest"},
    }


def _accepted_route_session(card: dict | None = None) -> dict:
    accepted = copy.deepcopy(card or _accepted_route_card())
    return {
        "_transcript": {
            "v": 1,
            "history": [],
            "route_cards": [accepted],
            "arrival_cards": [],
        },
        "active_trip": {
            "card_id": accepted["card_id"],
            "canonical_itinerary": copy.deepcopy(accepted["itinerary"]),
        },
        "trip_state": {"selected_candidate_id": "cd_selected"},
    }


class PresentRouteReservationTests(unittest.IsolatedAsyncioTestCase):
    """One-time reservation happens only after every fallible step succeeds.

    The store reservation is the final atomic publication gate: fallible
    canonical loading, first-leg context, and projection all run before it,
    so a failure in any of them leaves the candidate retryable. A race for
    the final reservation still yields at most one successful presentation.
    """

    def setUp(self):
        from app.services import cache

        self._original_redis_client = cache.redis_client
        cache.redis_client = None
        cache._mem.clear()

    def tearDown(self):
        from app.services import cache

        cache.redis_client = self._original_redis_client
        cache._mem.clear()

    def test_active_accepted_route_card_requires_exact_transcript_ownership(self):
        session = _accepted_route_session()
        card = transcript_store.active_accepted_route_card(session)
        assert card == session["_transcript"]["route_cards"][0]
        card["summary"]["eta_minutes"] = 99
        assert session["_transcript"]["route_cards"][0]["summary"]["eta_minutes"] == 23

        invalid_cases = {
            "wrong_card_id": {"active_trip": {"card_id": "rc_other"}},
            "different_itinerary": {
                "active_trip": {
                    "card_id": "rc_accepted",
                    "canonical_itinerary": {"legs": [{"mode": "BUS"}]},
                }
            },
            "not_recommended": {"role": "alternative"},
            "bad_origin": {"origin": "Your location"},
            "bad_route": {"route": {}},
            "bad_alerts": {"alerts": {}},
            "missing_legs": {"itinerary": {"total_duration_seconds": 1}},
            "empty_legs": {"itinerary": {"legs": []}},
        }
        for name, changes in invalid_cases.items():
            with self.subTest(name=name):
                candidate = _accepted_route_card()
                active = _accepted_route_session(candidate)
                if "active_trip" in changes:
                    active["active_trip"] = changes["active_trip"]
                else:
                    active["_transcript"]["route_cards"][0].update(changes)
                assert transcript_store.active_accepted_route_card(active) is None

    async def test_accepted_route_replay_uses_transcript_after_candidate_expiry(self):
        session = _accepted_route_session()
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", GoalKind.ROUTE),)))
        ctx = _ctx()
        ctx.session = session
        ctx.turn_id = "t2"
        ctx.turn_evidence = evidence

        with patch(
            "app.services.agent.tools.route.present_route._owned_candidate",
            side_effect=AssertionError("candidate store should not load"),
        ), patch(
            "app.services.agent.tools.route.present_route._project",
            side_effect=AssertionError("route projection should not run"),
        ), patch(
            "app.services.agent.tools.route.present_route._reserve_and_commit",
            side_effect=AssertionError("reservation should not run"),
        ):
            result = await present_route.execute(
                _present_route_input(
                    "cd_selected",
                    goal_key="route",
                    lead_in="Ignore the accepted route and choose another one.",
                ),
                ctx,
            )

        assert result.ok, result.error
        assert result.session_route_cards == []
        assert [event.type for event in result.events] == ["token", "route_card"]
        assert result.events[0].text == "Here\u2019s the accepted route again.\n\n"
        replay = result.events[1].to_data()
        original = session["_transcript"]["route_cards"][0]
        assert replay["card_id"] == original["card_id"]
        assert replay["turn_id"] == "t2"
        for field in (
            "role",
            "origin",
            "destination",
            "summary",
            "route",
            "alerts",
            "leg_label",
            "depart_iso",
            "itinerary",
            "selection_decision",
        ):
            assert replay.get(field) == original.get(field), field
        assert evidence.presented_for("route")

    async def test_invalid_replay_inputs_fall_through_to_normal_validation(self):
        session = _accepted_route_session()
        evidence = TurnEvidence()
        evidence.bind_contract(TurnContract((OutcomeGoal("route", GoalKind.ROUTE),)))
        ctx = _ctx()
        ctx.session = session
        ctx.turn_evidence = evidence
        with patch(
            "app.services.agent.tools.route.present_route._owned_candidate",
            return_value=ToolResult(ok=False, error="candidate unavailable"),
        ) as owned:
            wrong_candidate = await present_route.execute(
                _present_route_input("cd_other", goal_key="route"),
                ctx,
            )
            wrong_goal = await present_route.execute(
                _present_route_input("cd_selected", goal_key="other"),
                ctx,
            )
        assert not wrong_candidate.ok
        assert not wrong_goal.ok
        assert owned.call_count == 2

    async def _prepare_candidate(self, ctx, **prepare_input):
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=_prepared_leg()),
        ):
            result = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    **prepare_input,
                },
                ctx,
            )
        assert result.ok
        return (
            result.data["candidate_set_id"],
            result.data["candidates"][0]["candidate_id"],
        )

    async def test_failed_projection_does_not_consume_candidate_and_retry_succeeds(self):
        ctx = _ctx()
        set_id, candidate_id = await self._prepare_candidate(ctx)
        stages = []

        async def collect(stage, status):
            stages.append((stage, status))

        ctx.progress_sink = collect
        with patch(
            "app.services.agent.tools.route.present_route._project",
            return_value=ToolResult(ok=False, error="projection exploded"),
        ):
            failed = await present_route.execute(
                _present_route_input(candidate_id),
                ctx,
            )
        assert not failed.ok
        assert "projection exploded" in (failed.error or "")
        assert stages == [("comparing_options", "active"), ("comparing_options", "complete")]
        record = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        assert not record["presented"]
        assert trip_state.get_trip_state(ctx.session)["selected_candidate_id"] is None
        # The same candidate stays retryable through the real pipeline.
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            retried = await present_route.execute(
                _present_route_input(candidate_id),
                ctx,
            )
        assert retried.ok
        record = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        assert record["presented"]
        assert record["selected_candidate_id"] == candidate_id

    async def test_canonical_snapshot_does_not_refresh_first_leg_context(self):
        ctx = _ctx()
        set_id, candidate_id = await self._prepare_candidate(ctx)
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(side_effect=AssertionError("route was enriched again")),
        ) as enrich:
            presented = await present_route.execute(
                _present_route_input(candidate_id),
                ctx,
            )
        assert presented.ok, presented.error
        enrich.assert_not_awaited()
        record = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        assert record["presented"]
        assert trip_state.get_trip_state(ctx.session)["selected_candidate_id"] == candidate_id

    async def test_concurrent_presentations_yield_at_most_one_success(self):
        ctx = _ctx()
        set_id, candidate_id = await self._prepare_candidate(ctx)
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            results = await asyncio.gather(
                present_route.execute(_present_route_input(candidate_id), ctx),
                present_route.execute(_present_route_input(candidate_id), ctx),
            )
        assert sum(1 for result in results if result.ok) == 1
        loser = next(result for result in results if not result.ok)
        assert "already presented" in (loser.error or "")
        assert trip_state.get_trip_state(ctx.session)["selected_candidate_id"] == candidate_id
        record = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        assert record["presented"]

    async def test_what_if_preview_stays_unconsumed_until_successful_commit(self):
        ctx = _ctx()
        set_id, candidate_id = await self._prepare_candidate(ctx, what_if=True)
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            preview = await present_route.execute(
                _present_route_input(candidate_id),
                ctx,
            )
        assert preview.ok
        assert preview.session_route_cards == []
        record = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        assert not record["presented"]
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            repeated = await present_route.execute(
                _present_route_input(candidate_id),
                ctx,
            )
            committed = await present_route.execute(
                _present_route_input(candidate_id, commit_scenario=True),
                ctx,
            )
        assert repeated.ok
        assert committed.ok
        record = candidate_store.load_candidate_set(set_id, session_id=ctx.session_id)
        assert record["presented"]
        assert record["selected_candidate_id"] == candidate_id
