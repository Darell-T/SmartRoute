"""State-valid reuse of a session-owned temporary route preview."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import candidate_store, public_surface, trip_state
from app.services.agent.tool_input_policy import goal_error
from app.services.agent.tools.route import prepare_route_options, present_route
from app.services.agent.tools._types import ToolContext
from app.services.agent.turn.contract import GoalKind, GoalState, OutcomeGoal, TurnContract
from app.services.agent.turn.evidence import TurnEvidence
from tests.test_single_agent_route_tools import _ctx, _prepared_leg
from tests.conversation.conversation_matrix_harness import clear_caches


class ActiveTemporaryRoutePresenterTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_caches()

    def _evidence(self) -> TurnEvidence:
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("route", GoalKind.ROUTE),))
        )
        return evidence

    def _preview_session(
        self,
        *,
        session_id: str = "sess-preview",
        route_status: str = "good",
        selected: str | None = "cd_preview",
        scenario_mode: str = "what_if",
        hard_constraints_satisfied: bool = True,
    ) -> tuple[dict, str, str | None]:
        session = {"trip_state": trip_state.empty_trip_state()}
        set_id = candidate_store.store_candidate_set(
            session_id=session_id,
            payload={
                "scenario_mode": scenario_mode,
                "route_status": route_status,
                "candidates": [
                    {
                        "candidate_id": "cd_preview",
                        "index": 0,
                        "digest": {
                            "hard_constraints_satisfied": hard_constraints_satisfied,
                        },
                    }
                ],
            },
        )
        trip_state.bind_temporary_candidate_set(session, set_id)
        if selected is not None:
            trip_state.bind_temporary_selected_candidate(session, selected)
        return session, set_id, selected

    def test_valid_preview_adds_present_route_and_policy_accepts_exact_id(self):
        session, set_id, candidate_id = self._preview_session()
        evidence = self._evidence()

        offered = public_surface.state_valid_tool_names(
            evidence,
            session=session,
            session_id="sess-preview",
        )

        self.assertEqual(
            offered,
            {
                "discover_places",
                "prepare_route_options",
                "present_route",
                "complete_turn",
            },
        )
        ctx = ToolContext(
            session=session,
            session_id="sess-preview",
            turn_evidence=evidence,
        )
        self.assertIsNone(
            goal_error(
                "present_route",
                {
                    "goal_key": "route",
                    "candidate_id": candidate_id,
                    "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )
        )
        self.assertEqual(
            public_surface.active_temporary_route_preview(
                session,
                session_id="sess-preview",
            ),
            (set_id, candidate_id),
        )

    def test_adverse_or_incomplete_evidence_does_not_hide_a_viable_preview(self):
        for route_status in ("insufficient_coverage", "all_materially_degraded"):
            with self.subTest(route_status=route_status):
                session, set_id, candidate_id = self._preview_session(
                    route_status=route_status,
                )
                evidence = self._evidence()

                self.assertEqual(
                    public_surface.active_temporary_route_preview(
                        session,
                        session_id="sess-preview",
                    ),
                    (set_id, candidate_id),
                )
                self.assertIn(
                    "present_route",
                    public_surface.state_valid_tool_names(
                        evidence,
                        session=session,
                        session_id="sess-preview",
                    ),
                )

    def test_invalid_preview_never_enters_surface_or_policy(self):
        cases = (
            ("no_selected", {"selected": None}, "sess-preview"),
            ("wrong_session", {}, "other-session"),
            ("nonpresentable_candidate", {"hard_constraints_satisfied": False}, "sess-preview"),
            ("not_what_if", {"scenario_mode": "active"}, "sess-preview"),
        )
        for name, overrides, lookup_session_id in cases:
            with self.subTest(name=name):
                session, _set_id, candidate_id = self._preview_session(**overrides)
                evidence = self._evidence()
                offered = public_surface.state_valid_tool_names(
                    evidence,
                    session=session,
                    session_id=lookup_session_id,
                )
                self.assertNotIn("present_route", offered)
                ctx = ToolContext(
                    session=session,
                    session_id=lookup_session_id,
                    turn_evidence=evidence,
                )
                candidate_input = candidate_id or "cd_preview"
                self.assertIsNotNone(
                    goal_error(
                        "present_route",
                        {
                            "goal_key": "route",
                            "candidate_id": candidate_input,
                            "lead_in": "The route options were close, so I chose this one for your trip.",
                            "follow_up": "",
                            "reason_code": "meets_hard_constraints",
                        },
                        ctx,
                    )
                )

    def test_expired_preview_is_not_reused(self):
        session, set_id, candidate_id = self._preview_session()
        record = candidate_store.load_candidate_set(
            set_id,
            session_id="sess-preview",
        )
        self.assertIsNotNone(record)
        with patch(
            "app.services.agent.candidate_store.time.time",
            return_value=float(record["expires_at"]) + 1,
        ):
            self.assertIsNone(
                public_surface.active_temporary_route_preview(
                    session,
                    session_id="sess-preview",
                )
            )
            evidence = self._evidence()
            self.assertNotIn(
                "present_route",
                public_surface.state_valid_tool_names(
                    evidence,
                    session=session,
                    session_id="sess-preview",
                ),
            )

class ActiveTemporaryRoutePresenterAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_caches()

    async def test_pending_turn_accepts_existing_preview_without_reprepare(self):
        session_id = "sess-execute-preview"
        ctx = _ctx(session_id)
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=_prepared_leg()),
        ):
            prepared = await prepare_route_options.execute(
                {
                    "destination": "Barclays Center",
                    "destination_source": "current_turn",
                    "what_if": True,
                },
                ctx,
            )
        self.assertTrue(prepared.ok)
        candidate = prepared.data["candidates"][0]
        trip_state.bind_temporary_selected_candidate(
            ctx.session,
            candidate["candidate_id"],
        )
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("route", GoalKind.ROUTE),))
        )
        ctx.turn_evidence = evidence
        with patch(
            "app.services.trips.enrichment._enrich_route",
            new=AsyncMock(return_value=None),
        ):
            presented = await present_route.execute(
                {
                    "candidate_id": candidate["candidate_id"],
                    "goal_key": "route",
                    "commit_scenario": True,
                            "lead_in": "The route options were close, so I chose this one for your trip.",
                    "follow_up": "",
                    "reason_code": "meets_hard_constraints",
                },
                ctx,
            )
        self.assertTrue(presented.ok, presented.error)
        state = trip_state.get_trip_state(ctx.session)
        self.assertEqual(state["active_candidate_set_id"], prepared.data["candidate_set_id"])
        self.assertEqual(state["selected_candidate_id"], candidate["candidate_id"])
        self.assertIsNone(state["temporary_candidate_set_id"])
        self.assertEqual(evidence.handle_for("route"), prepared.data["candidate_set_id"])
        self.assertEqual(evidence.state_for("route"), GoalState.SATISFIED)


if __name__ == "__main__":
    unittest.main()
