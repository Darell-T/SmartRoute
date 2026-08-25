"""Regression tests for capability-aware completion and presenter framing.

These tests intentionally exercise the real route/transit presenters.  The
provider seam is limited to a deterministic prepared-leg fixture; the
presenters, candidate store, evidence handle, and rider-visible events remain
the production implementations.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import public_surface
from app.services.agent import session as session_module
from app.services.agent.tools.transit import evidence as transit_evidence
from app.services.agent.tools import (
    complete_turn,
)
from app.services.agent.tools.transit import present_transit
from app.services.agent.tools.route import (
    prepare_route_options,
    present_route,
)
from app.services.agent.tools._types import ToolContext
from app.services.agent.turn.completion import evaluate_completion
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence
from tests.conversation.conversation_matrix_harness import clear_caches
from tests.test_single_agent_route_tools import _ctx, _prepared_leg


class UnsupportedProactiveOfferTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_caches()

    async def _prepared_route(
        self,
        *,
        include_service_status: bool = False,
        service_status_state: GoalState = GoalState.PENDING,
    ):
        ctx = _ctx("sess-capability-completion")
        evidence = TurnEvidence()
        goals = [OutcomeGoal("route", GoalKind.ROUTE)]
        if include_service_status:
            goals.append(OutcomeGoal("service", GoalKind.SERVICE_STATUS))
        evidence.bind_contract(TurnContract(tuple(goals)))
        ctx.turn_evidence = evidence
        with patch(
            "app.services.agent.tools.route.prepare_route_options.prepare_single_leg",
            new=AsyncMock(return_value=_prepared_leg()),
        ):
            prepared = await prepare_route_options.execute(
                {
                    "destination": "Konoha",
                    "destination_source": "current_turn",
                    "goal_key": "route",
                },
                ctx,
            )
        self.assertTrue(prepared.ok, prepared.error)
        candidate_id = prepared.data["candidates"][0]["candidate_id"]
        evidence.record_goal_handle("route", prepared.data["candidate_set_id"])
        evidence.record_goal("route", GoalState.EVIDENCE_READY, attempted=True)
        if include_service_status and service_status_state != GoalState.PENDING:
            evidence.record_goal(
                "service",
                service_status_state,
                attempted=True,
                presented=service_status_state == GoalState.SATISFIED,
            )
        return ctx, candidate_id

    @staticmethod
    def _visible_text(result) -> str:
        return "".join(
            event.text
            for event in result.events
            if getattr(event, "type", "") == "token"
        )

    async def _present_route_with_follow_up(
        self,
        follow_up: str,
        **kwargs,
    ):
        ctx, candidate_id = await self._prepared_route(**kwargs)
        payload = {
            "candidate_id": candidate_id,
            "goal_key": "route",
            "lead_in": "The route options were close, so I chose this one for your trip.",
            "follow_up": follow_up,
            "reason_code": "meets_hard_constraints",
        }
        result = await present_route.execute(
            payload,
            ctx,
        )
        return ctx, result

    async def test_successful_route_ends_without_automatic_follow_up_spam(self):
        ctx, result = await self._present_route_with_follow_up("")

        self.assertTrue(result.ok, result.error)
        self.assertNotIn("want me to", self._visible_text(result).casefold())
        self.assertTrue(
            evaluate_completion(ctx.turn_evidence.turn_contract, ctx.turn_evidence)
            .may_terminate
        )
        self.assertEqual(session_module.get_pending_continuations(ctx.session), ())

    async def test_model_cannot_offer_unsupported_busyness_or_future_monitoring(self):
        for offer in (
            "Want me to check how busy it might be when you arrive?",
            "Want me to keep an eye on conditions closer to your arrival?",
        ):
            with self.subTest(offer=offer):
                ctx, candidate_id = await self._prepared_route()
                result = await present_route.execute(
                    {
                        "candidate_id": candidate_id,
                        "goal_key": "route",
                        "lead_in": "The route options were close, so I chose this one for your trip.",
                        "follow_up": offer,
                        "reason_code": "meets_hard_constraints",
                    },
                    ctx,
                )

                self.assertTrue(result.ok, result.error)
                visible = self._visible_text(result)
                self.assertNotIn(offer.casefold(), visible.casefold())
                self.assertEqual(result.data.get("follow_up"), "")
                self.assertEqual(session_module.get_pending_continuations(ctx.session), ())

    async def test_pending_requested_goal_does_not_authorize_proactive_offer(self):
        offer = "Want me to check current service status?"
        # A pending requested outcome remains executable, but it is not an
        # optional-offer permission.  Presenter prose cannot manufacture that
        # separate backend-owned authorization.
        ctx, result = await self._present_route_with_follow_up(
            offer,
            include_service_status=True,
        )

        self.assertIn(
            "check_transit",
            public_surface.state_valid_tool_names(
                ctx.turn_evidence,
                session=ctx.session,
                session_id=ctx.session_id,
            ),
        )
        self.assertTrue(result.ok, result.error)
        self.assertNotIn(offer.casefold(), self._visible_text(result).casefold())
        self.assertEqual(result.data.get("follow_up"), "")
        self.assertEqual(session_module.get_pending_continuations(ctx.session), ())

    async def test_raw_model_offer_cannot_create_an_unsupported_pending_continuation(self):
        offer = "Want me to keep monitoring this trip for changes?"
        ctx, result = await self._present_route_with_follow_up(offer)

        self.assertTrue(result.ok, result.error)
        self.assertEqual(session_module.get_pending_continuations(ctx.session), ())
        self.assertNotIn(offer.casefold(), self._visible_text(result).casefold())

    async def test_complete_turn_cannot_offer_unsupported_business_busyness(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("answer", GoalKind.GENERAL_RESPONSE),))
        )

        result = await complete_turn.execute(
            {
                "goal_keys": ["answer"],
                "outcome": "answer",
                "message": "Want me to check how busy it might be when you arrive?",
            },
            ToolContext(
                session={},
                session_id="sess-terminal-busyness",
                turn_id="t1",
                turn_evidence=evidence,
            ),
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.internal_diagnostic)
        self.assertEqual(result.events, [])
        self.assertFalse(evidence.terminal)

    async def test_complete_turn_cannot_promise_persistent_monitoring(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("answer", GoalKind.GENERAL_RESPONSE),))
        )

        result = await complete_turn.execute(
            {
                "goal_keys": ["answer"],
                "outcome": "answer",
                "message": "I'll keep an eye on this trip and let you know if it changes.",
            },
            ToolContext(
                session={},
                session_id="sess-terminal-monitoring",
                turn_id="t1",
                turn_evidence=evidence,
            ),
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.internal_diagnostic)
        self.assertEqual(result.events, [])
        self.assertFalse(evidence.terminal)

    async def test_complete_turn_cannot_offer_conditional_future_notification(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("answer", GoalKind.GENERAL_RESPONSE),))
        )

        result = await complete_turn.execute(
            {
                "goal_keys": ["answer"],
                "outcome": "answer",
                "message": "I can let you know if something changes.",
            },
            ToolContext(
                session={},
                session_id="sess-terminal-notification",
                turn_id="t1",
                turn_evidence=evidence,
            ),
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.internal_diagnostic)
        self.assertEqual(result.events, [])
        self.assertFalse(evidence.terminal)

    async def test_bus_stop_accessibility_is_not_presented_as_station_capability(self):
        evidence_set_id, _ = transit_evidence.build_evidence_set(
            session_id="sess-bus-stop-capability",
            operation="accessibility",
            result={
                "entity_type": "BUS_STOP",
                "mode": "bus",
                "stop_id": "B35-123",
                "freshness": "live",
                "elevator_outages": [],
            },
        )
        transit_context = ToolContext(
            session={},
            session_id="sess-bus-stop-capability",
            turn_id="t1",
        )
        result = await present_transit.execute(
            {
                "evidence_set_id": evidence_set_id,
                "goal_key": "accessibility",
                "lead_in": "",
                "follow_up": "Want me to check elevator access at this bus stop?",
            },
            transit_context,
        )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.data.get("follow_up"), "")
        passenger_text = str(result.data.get("passenger_text") or "").casefold()
        self.assertNotIn("station", passenger_text)
        self.assertNotIn("elevator", passenger_text)
        self.assertEqual(
            session_module.get_pending_continuations(transit_context.session), ()
        )

    async def test_presenter_framing_rejects_internal_capability_and_evidence_ids(self):
        _ctx, route_result = await self._present_route_with_follow_up(
            "I used check_transit with evidence_set_id te_private."
        )
        self.assertFalse(route_result.ok)
        self.assertEqual(route_result.events, [])
        self.assertNotIn("te_private", str(route_result.error))

        evidence_set_id, _ = transit_evidence.build_evidence_set(
            session_id="sess-framing-transit",
            operation="service_status",
            route_ids=["Q"],
            result={
                "freshness": "live",
                "status": "no_active_alerts",
                "alerts": [],
            },
        )
        transit_result = await present_transit.execute(
            {
                "evidence_set_id": evidence_set_id,
                "goal_key": "status",
                "lead_in": "",
                "follow_up": "I used check_transit with evidence_set_id te_private.",
            },
            ToolContext(
                session={},
                session_id="sess-framing-transit",
                turn_id="t1",
            ),
        )
        self.assertFalse(transit_result.ok)
        self.assertEqual(transit_result.events, [])
        self.assertNotIn("te_private", str(transit_result.error))


if __name__ == "__main__":
    unittest.main()
