"""Sonnet 5 model migration and request-shape guards."""

from __future__ import annotations

import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from evaluation.route_intelligence import advisor as ai_advisor
from app.services.agent import loop as agent_loop
from app.services.agent.turn import stream as turn_stream
from app.services.agent.model import policy
from app.services.agent.model import request as model_request
from app.services.agent.turn.contract import (
    GoalKind,
    GoalState,
    OutcomeGoal,
    TurnContract,
)
from app.services.agent.turn.evidence import TurnEvidence


class Sonnet5RequestTests(unittest.IsolatedAsyncioTestCase):
    def test_auto_defaults_to_sonnet_5(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_AUTO_MODEL", "AGENT_SONNET_MODEL", "AGENT_MODEL"):
                os.environ.pop(key, None)
            automatic = policy.policy_for_mode("auto")
        self.assertEqual(automatic.model, "claude-sonnet-5")
        self.assertEqual(automatic.mode, "auto")

    def test_agent_auto_model_overrides_default(self) -> None:
        with patch.dict(
            os.environ,
            {"AGENT_AUTO_MODEL": "claude-sonnet-4-5-20250929"},
            clear=False,
        ):
            automatic = policy.policy_for_mode("auto")
        self.assertEqual(automatic.model, "claude-sonnet-4-5-20250929")

    def test_quick_uses_the_same_sonnet_model(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_QUICK_MODEL", "AGENT_HAIKU_MODEL"):
                os.environ.pop(key, None)
            quick = policy.policy_for_mode("quick")
        self.assertEqual(quick.model, "claude-sonnet-5")
        self.assertNotIn("haiku", quick.model.casefold())

    def test_auto_outer_request_uses_sonnet_5_without_unsupported_fields(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_AUTO_MODEL", "AGENT_SONNET_MODEL", "AGENT_MODEL"):
                os.environ.pop(key, None)
            mode = policy.policy_for_mode("auto")
        tools = agent_loop._tools_for_state(mode)
        kwargs = agent_loop._build_stream_kwargs(
            messages=[{"role": "user", "content": "shape-only"}],
            system_blocks=[{"type": "text", "text": "system"}],
            mode_policy=mode,
            tools=tools,
        )
        self.assertEqual(kwargs["model"], "claude-sonnet-5")
        self.assertEqual(kwargs["max_tokens"], mode.max_output_tokens)
        self.assertEqual(kwargs["output_config"], {"effort": "medium"})
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("top_p", kwargs)
        self.assertNotIn("top_k", kwargs)
        self.assertEqual(kwargs.get("tool_choice"), {"type": "any"})
        self.assertIn("tools", kwargs)
        # Presenter tools are state-valid only after provider evidence exists;
        # the initial request exposes the declaration, capabilities, and
        # terminal tool.
        self.assertEqual(len(kwargs["tools"]), 5)
        self.assertEqual(
            {tool.get("name") for tool in kwargs["tools"] if tool.get("type") != "web_search_20250305"},
            {
                "declare_goals",
                "discover_places",
                "check_transit",
                "prepare_route_options",
                "complete_turn",
            },
        )
        self.assertEqual(mode.explanation_style, "comparative")
        diagnostics = model_request.request_diagnostics(kwargs)
        self.assertIn("model=claude-sonnet-5", diagnostics)
        self.assertIn("thinking_supplied=0", diagnostics)
        self.assertNotIn("shape-only", diagnostics)

    async def test_auto_advisor_sdk_kwargs_use_sonnet_5_without_unsupported_fields(self) -> None:
        """Assert final Anthropic stream kwargs for the route-selection advisor."""
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_AUTO_MODEL", "AGENT_SONNET_MODEL", "AGENT_MODEL"):
                os.environ.pop(key, None)
            mode = policy.policy_for_mode("auto")

        captured: dict = {}

        @asynccontextmanager
        async def fake_stream(**kwargs):
            captured.update(kwargs)

            class _Stream:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                @property
                def text_stream(self):
                    return self._text()

                async def _text(self):
                    for chunk in ():
                        yield chunk

            yield _Stream()

        with (
            patch.dict(
                os.environ,
                {"JARVIS_MOCK_ADVISOR": "0", "SMARTROUTE_ENV": "test"},
                clear=False,
            ),
            patch.object(ai_advisor.client.messages, "stream", side_effect=fake_stream),
        ):
            chunks = []
            async for chunk in ai_advisor.stream_agent_recommendation(
                {"routes": [{"summary": "shape-only"}]},
                model=mode.model,
                explanation_style=mode.explanation_style,
            ):
                chunks.append(chunk)

        self.assertEqual(captured["model"], "claude-sonnet-5")
        self.assertEqual(captured["max_tokens"], 512)
        self.assertNotIn("thinking", captured)
        self.assertNotIn("temperature", captured)
        self.assertNotIn("top_p", captured)
        self.assertNotIn("top_k", captured)
        self.assertNotIn("tools", captured)
        self.assertNotIn("tool_choice", captured)
        self.assertIn("messages", captured)
        self.assertIn("system", captured)

    def test_auto_request_structure_otherwise_unchanged(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in ("AGENT_AUTO_MODEL", "AGENT_SONNET_MODEL", "AGENT_MODEL"):
                os.environ.pop(key, None)
            mode = policy.policy_for_mode("auto")
        self.assertEqual(mode.max_route_candidates, 5)
        self.assertEqual(mode.retry_count, 1)
        self.assertEqual(mode.max_output_tokens, 2048)
        self.assertEqual(mode.output_effort, "medium")
        self.assertEqual(mode.max_rounds, 5)
        self.assertTrue(mode.optional_enrichment)

    def test_initial_request_requires_goal_declaration_without_disabling_parallel_calls(self) -> None:
        mode = policy.policy_for_mode("auto")
        evidence = TurnEvidence()
        tools = agent_loop._tools_for_state(mode, turn_evidence=evidence)
        request_options = turn_stream._initial_goal_request_options(
            evidence,
            frozenset(tool["name"] for tool in tools),
        )

        kwargs = agent_loop._build_stream_kwargs(
            messages=[{"role": "user", "content": "shape-only"}],
            system_blocks=[{"type": "text", "text": "system"}],
            mode_policy=mode,
            tools=tools,
            request_options=request_options,
        )

        self.assertEqual(
            kwargs["tool_choice"],
            {"type": "tool", "name": "declare_goals"},
        )
        self.assertNotIn("disable_parallel_tool_use", kwargs)

    def test_post_declaration_request_keeps_any_tool_choice(self) -> None:
        mode = policy.policy_for_mode("auto")
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("response", GoalKind.GENERAL_RESPONSE),))
        )
        tools = agent_loop._tools_for_state(mode, turn_evidence=evidence)
        request_options = turn_stream._initial_goal_request_options(
            evidence,
            frozenset(tool["name"] for tool in tools),
        )

        kwargs = agent_loop._build_stream_kwargs(
            messages=[{"role": "user", "content": "shape-only"}],
            system_blocks=[{"type": "text", "text": "system"}],
            mode_policy=mode,
            tools=tools,
            request_options=request_options,
        )

        self.assertEqual(kwargs["tool_choice"], {"type": "any"})
        self.assertNotIn("disable_parallel_tool_use", kwargs)

    def test_single_ready_result_requires_its_canonical_presenter(self) -> None:
        mode = policy.policy_for_mode("auto")
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract((OutcomeGoal("places", GoalKind.PLACE_RECOMMENDATION),))
        )
        evidence.record_goal(
            "places",
            GoalState.EVIDENCE_READY,
            attempted=True,
        )
        tools = agent_loop._tools_for_state(mode, turn_evidence=evidence)
        request_options = turn_stream._initial_goal_request_options(
            evidence,
            frozenset(tool["name"] for tool in tools),
        )

        self.assertEqual(
            request_options,
            {"tool_choice": {"type": "tool", "name": "present_places"}},
        )

    def test_multiple_ready_results_leave_presenter_order_to_model(self) -> None:
        mode = policy.policy_for_mode("auto")
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("places", GoalKind.PLACE_RECOMMENDATION),
                    OutcomeGoal("status", GoalKind.SERVICE_STATUS),
                )
            )
        )
        for goal_key in ("places", "status"):
            evidence.record_goal(
                goal_key,
                GoalState.EVIDENCE_READY,
                attempted=True,
            )
        tools = agent_loop._tools_for_state(mode, turn_evidence=evidence)
        request_options = turn_stream._initial_goal_request_options(
            evidence,
            frozenset(tool["name"] for tool in tools),
        )

        self.assertEqual(request_options, {})

    def test_ready_result_with_pending_goal_does_not_force_presenter(self) -> None:
        mode = policy.policy_for_mode("auto")
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract(
                (
                    OutcomeGoal("route", GoalKind.ROUTE),
                    OutcomeGoal("status", GoalKind.SERVICE_STATUS),
                )
            )
        )
        evidence.record_goal(
            "route",
            GoalState.EVIDENCE_READY,
            attempted=True,
        )
        tools = agent_loop._tools_for_state(mode, turn_evidence=evidence)
        request_options = turn_stream._initial_goal_request_options(
            evidence,
            frozenset(tool["name"] for tool in tools),
        )

        self.assertEqual(request_options, {})


if __name__ == "__main__":
    unittest.main()
