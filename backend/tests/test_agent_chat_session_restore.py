"""Conversation transcript persistence, restore, and reset boundaries."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import pytest
from app.routers import agent_chat
from app.services import cache
from app.services.agent import events
from app.services.agent import session as session_module
from fastapi import HTTPException


def route_card(index: int) -> events.RouteCardEvent:
    return events.RouteCardEvent(
        card_id=f"rc-{index}",
        turn_id=f"t{index}",
        role="recommended",
        origin={"label": "Your location", "lat": 40.64, "lng": -73.96},
        destination={"label": f"Place {index}", "lat": 40.7, "lng": -73.9},
        summary={"eta_minutes": 20, "transfers": 0, "lines": ["Q"]},
        route=[],
        alerts=[],
    )


def arrival_card(turn_id: str) -> events.ArrivalCardEvent:
    return events.ArrivalCardEvent(
        turn_id=turn_id,
        route_id="Q",
        stop={"name": "Church Av", "latitude": 40.64, "longitude": -73.96},
        directions=[],
        updated_at="2026-08-13T12:00:00-04:00",
        source_status="live",
        resolution_status="resolved",
    )


class SessionTranscriptTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_complete_transcript_is_independent_from_bounded_model_history(self):
        session_id, session = session_module.new_session()
        for index in range(20):
            session_module.append_history(
                session, "user", f"request {index}", turn_id=f"t{index}"
            )
            session_module.append_history(
                session, "assistant", f"answer {index}", turn_id=f"t{index}"
            )
            session_module.add_visible_events(session, [route_card(index)])
        session_module.save_session(session_id, session)

        loaded = session_module.load_session(session_id)
        assert loaded is not None
        assert len(loaded["history"]) <= session_module.MAX_HISTORY_MESSAGES
        snapshot = session_module.transcript_snapshot(loaded)
        assert len(snapshot["history"]) == 40
        assert len(snapshot["route_cards"]) == 20
        assert snapshot["history"][0]["text"] == "request 0"

        raw_core = cache.cache_get(session_module._session_key(session_id))
        core = json.loads(raw_core)
        assert session_module._TRANSCRIPT_FIELD not in core
        assert "route_card_payloads" not in core

    def test_snapshot_contains_only_visible_prose_and_canonical_cards(self):
        _session_id, session = session_module.new_session()
        session_module.append_history(session, "user", "When is the Q?", turn_id="t1")
        session_module.append_tool_summary(session, "lookup_arrivals", "found arrivals")
        session_module.append_history(
            session, "assistant", "The next Q is due.", turn_id="t1"
        )
        session_module.add_visible_events(session, [route_card(1), arrival_card("t1")])

        snapshot = session_module.transcript_snapshot(session)
        assert [entry["role"] for entry in snapshot["history"]] == ["user", "assistant"]
        assert len(snapshot["route_cards"]) == 1
        assert len(snapshot["arrival_cards"]) == 1
        assert snapshot["sources"] == []

    def test_snapshot_restores_trusted_sources_for_the_producing_turn(self):
        _session_id, session = session_module.new_session()
        session_module.append_history(
            session, "assistant", "Here is the wait.", turn_id="t1"
        )
        session_module.add_visible_events(
            session,
            [
                events.SourcesEvent(
                    turn_id="t1",
                    sources=(
                        {
                            "title": "Damn Lines: L'industrie Pizzeria",
                            "url": "https://damnlines.com/camera/lindustrie-pizzeria",
                        },
                    ),
                )
            ],
        )

        snapshot = session_module.transcript_snapshot(session)
        assert snapshot["sources"] == [
            {
                "turn_id": "t1",
                "sources": [
                    {
                        "title": "Damn Lines: L'industrie Pizzeria",
                        "url": "https://damnlines.com/camera/lindustrie-pizzeria",
                    }
                ],
            }
        ]

    def test_adding_sources_replaces_malformed_in_memory_entries(self):
        _session_id, session = session_module.new_session()
        session[session_module._TRANSCRIPT_FIELD]["sources"] = [None, "invalid"]

        session_module.add_visible_events(
            session,
            [
                events.SourcesEvent(
                    turn_id="t1",
                    sources=(
                        {
                            "title": "Damn Lines",
                            "url": "https://damnlines.com/camera/lindustrie-pizzeria",
                        },
                    ),
                )
            ],
        )

        assert session_module.transcript_snapshot(session)["sources"] == [
            {
                "turn_id": "t1",
                "sources": [
                    {
                        "title": "Damn Lines",
                        "url": "https://damnlines.com/camera/lindustrie-pizzeria",
                    }
                ],
            }
        ]

    def test_retry_after_empty_failure_does_not_duplicate_visible_user_turn(self):
        _session_id, session = session_module.new_session()
        session_module.append_history(session, "user", "Route me home", turn_id="t1")
        session_module.append_history(session, "user", "Route me home", turn_id="t2")
        snapshot = session_module.transcript_snapshot(session)
        assert snapshot["history"] == [
            {"role": "user", "text": "Route me home", "turn_id": "t1"}
        ]

    def test_legacy_session_bootstrap_does_not_duplicate_first_new_entry(self):
        session = {"history": [{"role": "user", "text": "older"}]}
        session_module.append_history(session, "assistant", "answer", turn_id="t1")
        snapshot = session_module.transcript_snapshot(session)
        assert [entry["text"] for entry in snapshot["history"]] == ["older", "answer"]

    def test_reset_prevents_late_stream_save_from_resurrecting_session(self):
        session_id, session = session_module.new_session()
        session_module.append_history(session, "user", "private trip", turn_id="t1")
        session_module.save_session(session_id, session)

        session_module.delete_session(session_id)
        session_module.append_history(session, "assistant", "late answer", turn_id="t1")
        session_module.save_session(session_id, session)

        assert session_module.load_session(session_id) is None
        assert cache.cache_get(session_module._session_key(session_id)) is None
        assert cache.cache_get(session_module._transcript_key(session_id)) is None


class SessionRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()

    async def test_snapshot_restores_full_transcript(self):
        session_id, session = session_module.new_session()
        session_module.append_history(session, "user", "Route me home", turn_id="t1")
        session_module.append_history(
            session, "assistant", "Here is the route.", turn_id="t1"
        )
        session_module.add_visible_events(session, [route_card(1)])
        session_module.save_session(session_id, session)

        with patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True):
            response = await agent_chat.agent_chat_session_snapshot(
                agent_chat.SessionRequest(session_id=session_id)
            )
        assert response["session_id"] == session_id
        assert len(response["history"]) == 2
        assert response["route_cards"][0]["turn_id"] == "t1"

    async def test_missing_snapshot_is_stable_expiry(self):
        with (
            patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True),
            pytest.raises(HTTPException) as error,
        ):
            await agent_chat.agent_chat_session_snapshot(
                agent_chat.SessionRequest(session_id="missing")
            )
        assert error.value.status_code == 404
        assert error.value.detail == "session_expired"

    async def test_reset_is_idempotent_and_snapshot_no_longer_exists(self):
        session_id, session = session_module.new_session()
        session_module.save_session(session_id, session)
        request = agent_chat.SessionRequest(session_id=session_id)
        with patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True):
            assert await agent_chat.agent_chat_session_reset(request) == {"ok": True}
            assert await agent_chat.agent_chat_session_reset(request) == {"ok": True}
            with pytest.raises(HTTPException) as error:
                await agent_chat.agent_chat_session_snapshot(request)
        assert error.value.status_code == 404


if __name__ == "__main__":
    unittest.main()
