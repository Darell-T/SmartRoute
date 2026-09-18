"""Bounded live certification of SmartRoute's real Sonnet agent loop.

The test is opt-in because it spends real Anthropic credits. It exercises the
production prompt, state-scoped capability surface, stream parser, goal ledger,
tool loop, and canonical presenters. Only non-Anthropic provider boundaries are
replaced with deterministic data, so this check cannot spend Google, xAI, or
transit-provider quota.

Run with ``RUN_ANTHROPIC_TOOL_CONTRACT=1``. Every actual ``messages.stream``
attempt, including a retry, is persisted to the configured ledger. The default
ceiling is 24; an explicitly authorized stress pass may raise it with
``ANTHROPIC_LIVE_CALL_LIMIT``.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import anthropic
from app import observability
from app.services.agent import loop
from app.services.agent import session as session_module

LIVE_ENABLED = os.getenv("RUN_ANTHROPIC_TOOL_CONTRACT", "").strip() == "1"
MAX_PROVIDER_CALLS = max(
    1,
    int(os.getenv("ANTHROPIC_LIVE_CALL_LIMIT", "24")),
)

INTERNAL_OUTPUT_MARKERS = (
    "candidate_id",
    "discovery_set_id",
    "evidence_set_id",
    "goal_key",
    "avoid_crowds",
    "declare_goals",
    "discover_places",
    "check_transit",
    "prepare_route_options",
    "present_places",
    "present_transit",
    "present_route",
    "complete_turn",
)

def _load_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    env_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""

class _CallCounter:
    def __init__(self, limit: int, ledger_path: Path | None = None) -> None:
        self.limit = limit
        self.ledger_path = ledger_path
        self.count = self._load_count()

    def _load_count(self) -> int:
        if self.ledger_path is None:
            return 0
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return 0
        count = payload.get("anthropic_attempts", 0)
        return count if isinstance(count, int) and count >= 0 else 0

    def _persist(self) -> None:
        if self.ledger_path is None:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(
            json.dumps(
                {
                    "anthropic_attempts": self.count,
                    "hard_limit": self.limit,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def claim(self) -> None:
        if self.count >= self.limit:
            raise AssertionError(
                f"live Anthropic request ceiling ({self.limit}) would be exceeded"
            )
        self.count += 1
        self._persist()
        print(f"[LIVE ANTHROPIC ATTEMPT] {self.count}/{self.limit}")

class _CountedMessages:
    def __init__(self, messages, counter: _CallCounter) -> None:
        self._messages = messages
        self._counter = counter

    def stream(self, *args, **kwargs):
        self._counter.claim()
        return self._messages.stream(*args, **kwargs)

class _CountedClient:
    def __init__(self, client: anthropic.AsyncAnthropic, counter: _CallCounter) -> None:
        self.messages = _CountedMessages(client.messages, counter)

def _tool_names(trace: loop.TurnTrace) -> list[str]:
    return [name for name, _tool_input in trace.tool_calls]

def _passenger_text(events: list) -> str:
    return "".join(event.text for event in events if event.type == "token")

def _safe_activity(events: list) -> list[str]:
    return [
        str(event.label).strip()
        for event in events
        if event.type == "tool_start" and str(event.label).strip()
    ]


def _safe_trace_diagnostics(trace: loop.TurnTrace) -> dict[str, object]:
    telemetry = trace.telemetry
    route = telemetry.get("route_candidate_diagnostics")
    route = route if isinstance(route, dict) else {}
    goal_states = telemetry.get("goal_states")
    goal_states = goal_states if isinstance(goal_states, dict) else {}
    corrections = telemetry.get("route_decision_corrections")
    corrections = corrections if isinstance(corrections, dict) else {}
    candidate_ids = sorted(
        {
            str(tool_input.get("candidate_id") or "").strip()
            for _name, tool_input in trace.tool_calls
            if str(tool_input.get("candidate_id") or "").strip()
        }
    )
    evidence_handles = sorted(
        {
            str(state.get("evidence_handle") or "").strip()
            for state in goal_states.values()
            if isinstance(state, dict)
            and str(state.get("evidence_handle") or "").strip()
        }
    )
    return {
        "trace_id": telemetry.get("trace_id"),
        "model_request_count": trace.model_call_count,
        "candidate_ids": candidate_ids,
        "candidate_family_count": route.get(
            "final_structurally_unique_candidate_count"
        ),
        "evidence_handles": evidence_handles,
        "selection_source": trace.terminal_resolution.get("selection_source"),
        "correction_count": sum(
            value for value in corrections.values() if isinstance(value, int)
        ),
        "completion_result": trace.terminal_resolution.get("resolution"),
    }


def _assert_safe_passenger_output(events: list) -> None:
    text = _passenger_text(events).strip()
    assert text, "the rider received no conversational prose"
    lowered = text.casefold()
    for marker in INTERNAL_OUTPUT_MARKERS:
        assert marker not in lowered
    paragraphs = [
        " ".join(paragraph.casefold().split())
        for paragraph in text.split("\n\n")
        if len(paragraph.strip()) >= 20
    ]
    assert len(paragraphs) == len(set(paragraphs))

def _print_transcript(
    *,
    flow: str,
    rider: str,
    events: list,
    trace: loop.TurnTrace,
    provider_calls: int,
) -> None:
    done = events[-1]
    route_cards = [event for event in events if event.type == "route_card"]
    card_summaries = [
        {
            "destination": str(card.destination.get("label") or "destination"),
            "duration_min": card.summary.get("eta_minutes"),
            "transfers": card.summary.get("transfers"),
        }
        for card in route_cards
    ]
    failed = [
        {
            "capability": str(attempt.get("capability") or "capability"),
            "error": str(attempt.get("error") or ""),
        }
        for attempt in trace.capability_attempts
        if not attempt.get("ok")
    ]
    diagnostic_inputs = []
    for name, raw_input in trace.tool_calls:
        visible_input = {
            key: value
            for key, value in raw_input.items()
            if key not in {
                "activity_label",
                "candidate_id",
                "candidate_set_id",
                "discovery_set_id",
                "evidence_set_id",
                "goal_key",
                "goal_keys",
            }
        }
        diagnostic_inputs.append({"capability": name, "input": visible_input})
    def print_safe(value: str) -> None:
        encoding = sys.stdout.encoding or "utf-8"
        print(value.encode(encoding, errors="backslashreplace").decode(encoding))

    print_safe(f"\n[LIVE CONVERSATION] {flow}")
    print_safe(f"RIDER: {rider}")
    print_safe(f"SMARTROUTE: {_passenger_text(events).strip()}")
    print(f"ACTIVITY: {_safe_activity(events)}")
    print(f"CAPABILITIES: {_tool_names(trace)}")
    print(f"MODEL_ROUNDS: {trace.model_rounds}")
    print(f"TRACE: {_safe_trace_diagnostics(trace)}")
    print(f"CAPABILITY_INPUTS: {diagnostic_inputs}")
    print(
        "ROUTE_FRAMING: "
        + repr([
            {
                "reason_code": call.get("reason_code"),
                "lead_in": call.get("lead_in"),
                "follow_up": call.get("follow_up"),
            }
            for name, call in trace.tool_calls
            if name == "present_route"
        ])
    )
    print(f"CANONICAL_CARDS: {card_summaries}")
    print(f"FAILED_CAPABILITIES: {failed}")
    print(
        "TERMINAL_ATTEMPTS: "
        + repr(
            [
                {
                    "goal_keys": call.get("goal_keys"),
                    "outcome": call.get("outcome"),
                    "message": call.get("message"),
                }
                for name, call in trace.tool_calls
                if name == "complete_turn"
            ]
        )
    )
    print(
        "TERMINAL: "
        f"state={done.terminal_state} reason={done.stop_reason} "
        f"provider_attempts_so_far={provider_calls}"
    )


class AnthropicLiveAgentContractMixin:
    @classmethod
    def setUpClass(cls) -> None:
        key = _load_api_key()
        if not key:
            raise unittest.SkipTest("ANTHROPIC_API_KEY required for live contract tests")
        ledger_value = os.getenv("ANTHROPIC_LIVE_LEDGER_PATH", "").strip()
        ledger_path = Path(ledger_value) if ledger_value else None
        cls.api_key = key
        cls.counter = _CallCounter(MAX_PROVIDER_CALLS, ledger_path)

    async def _run_turn(
        self,
        message: str,
        *,
        turn_id: str,
    ) -> tuple[list, loop.TurnTrace]:
        session_id, session = session_module.new_session()
        return await self._run_session_turn(
            message,
            turn_id=turn_id,
            session_id=session_id,
            session=session,
        )

    async def _run_session_turn(
        self,
        message: str,
        *,
        turn_id: str,
        session_id: str,
        session: dict,
        gtfs=None,
    ) -> tuple[list, loop.TurnTrace]:
        trace = loop.TurnTrace()
        events = []
        # ``IsolatedAsyncioTestCase`` creates a fresh event loop per test.
        # Anthropic's async client owns loop-bound transports, so sharing one
        # class-level client causes false connection failures on later tests.
        async with anthropic.AsyncAnthropic(
            api_key=self.api_key,
            max_retries=0,
        ) as raw_client:
            raw_client = observability.wrap_anthropic(raw_client)
            live_client = _CountedClient(raw_client, self.counter)
            with (
                patch.object(loop, "client", live_client),
                patch.object(loop.budget, "agent_enabled", return_value=True),
                patch.object(loop.budget, "check_session_rate_limit", return_value=True),
                patch.object(loop.budget, "daily_spend_exceeded", return_value=False),
            ):
                events = [
                    event
                    async for event in loop.run_agent_turn(
                    session=session,
                    session_id=session_id,
                    turn_id=turn_id,
                    message=message,
                    now_et="2026-08-18T12:00:00-04:00",
                    gtfs=gtfs,
                    origin={"lat": 40.6494, "lng": -73.9631},
                    response_presentation="auto",
                    trace=trace,
                    )
                ]
        return events, trace

    def _assert_completed(self, events: list) -> None:
        assert events
        assert events[-1].type == "done"
        assert events[-1].terminal_state == "completed"
        assert not any(event.type == "error" for event in events)
        _assert_safe_passenger_output(events)

    def _report(
        self,
        flow: str,
        rider: str,
        events: list,
        trace: loop.TurnTrace,
    ) -> None:
        _print_transcript(
            flow=flow,
            rider=rider,
            events=events,
            trace=trace,
            provider_calls=self.counter.count,
        )
