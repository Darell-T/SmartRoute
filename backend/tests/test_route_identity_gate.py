"""Opaque verified-place requirement after discovery or web research."""

from __future__ import annotations

import unittest

from app.services.agent.tools._types import ToolContext
from app.services.agent.turn.evidence import TurnEvidence
from app.services.agent.turn.tool_round import _missing_verified_destination


class RouteIdentityGateTests(unittest.TestCase):
    def test_web_research_requires_opaque_destination(self):
        evidence = TurnEvidence()
        evidence.note_web(ok=True)
        ctx = ToolContext(turn_evidence=evidence)
        researched = {
            "destination": "A place mentioned online",
            "destination_source": "current_turn",
        }
        verified = {
            "destination_place_id": "pl_verified",
            "destination_source": "current_turn",
        }
        assert _missing_verified_destination(
            "prepare_route_options", researched, ctx
        ) is not None
        assert _missing_verified_destination(
            "prepare_route_options", verified, ctx
        ) is None

    def test_ordinary_route_still_allows_free_text(self):
        ctx = ToolContext(turn_evidence=TurnEvidence())
        assert _missing_verified_destination("prepare_route_options", {"destination": "Barclays Center", "destination_source": "current_turn"}, ctx) is None

    def test_current_turn_destination_cannot_be_omitted(self):
        ctx = ToolContext(turn_evidence=TurnEvidence())

        error = _missing_verified_destination(
            "prepare_route_options",
            {"destination_source": "current_turn"},
            ctx,
        )

        assert "current-turn destination is missing" in (error or "")

    def test_accepted_trip_may_resolve_destination_from_server_state(self):
        ctx = ToolContext(turn_evidence=TurnEvidence())

        assert _missing_verified_destination("prepare_route_options", {"destination_source": "accepted_trip"}, ctx) is None
