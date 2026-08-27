"""Canonical queue prose and trusted Damn Lines sources from present_places."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock, patch

from app.services import cache
from app.services.agent import discovery_store
from app.services.agent import events as agent_events
from app.services.agent.tools._types import ToolContext
from app.services.agent.tools.places import damn_lines, present_places

LINDUSTRIE_ID = "ChIJ92OsaJVZwokRsC54kf-J-3g"
UNMONITORED_ID = "google-unmonitored"
CAPTURED_AT = datetime(2026, 8, 25, 22, 58, tzinfo=UTC)
GOOGLE_MAPS_SOURCE = {
    "title": "Google Maps",
    "url": "https://www.google.com/maps",
}


def _ctx() -> ToolContext:
    return ToolContext(
        session={},
        session_id="sess-pres",
        turn_id="t-queue",
        now_et="2026-08-25T19:00:00-04:00",
        agent_mode="auto",
    )


def _store(*, mode: str, places: list[dict]) -> str:
    return discovery_store.store_discovery_set(
        session_id="sess-pres",
        query="pizza",
        search_scope={"kind": "current_location", "values": []},
        queue_context={"mode": mode, "max_wait_minutes": None},
        places=places,
    )


def _payload(set_id: str, place_ids: list[str]) -> dict:
    return {
        "discovery_set_id": set_id,
        "selections": [
            {"place_id": place_id, "reason": "preference_match"}
            for place_id in place_ids
        ],
        "research_used": False,
        "presentation_mode": "recommendations",
        "goal_key": "",
        "lead_in": "",
        "follow_up": "",
    }


def _token_text(result) -> str:
    return "".join(
        event.text
        for event in result.events
        if getattr(event, "type", None) == "token"
    )


def _source_event(result) -> agent_events.SourcesEvent | None:
    for event in result.events:
        if getattr(event, "type", None) == "sources":
            return event
    return None


class PresentPlacesQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()
        damn_lines._current_refresh_task = None
        damn_lines._history_refresh_task = None
        damn_lines._history_loaded = False
        damn_lines._history_last_success = None
        damn_lines._history_index = {}

    async def test_ignore_does_not_fetch_or_mention_queue(self):
        set_id = _store(
            mode="ignore",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "123 Bleecker St",
                    "open_status": "open",
                    "provider_place_id": LINDUSTRIE_ID,
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        current = AsyncMock()
        with patch.object(damn_lines, "get_current_observations", new=current):
            result = await present_places.execute(
                _payload(set_id, [record["places"][0]["place_id"]]),
                _ctx(),
            )

        current.assert_not_awaited()
        assert "wait" not in _token_text(result).casefold()
        source = _source_event(result)
        assert source is not None
        assert source.sources == (GOOGLE_MAPS_SOURCE,)

    async def test_heads_up_appends_live_note_and_source_after_the_list(self):
        set_id = _store(
            mode="heads_up",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "123 Bleecker St",
                    "open_status": "open",
                    "provider_place_id": LINDUSTRIE_ID,
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        current = AsyncMock(
            return_value=damn_lines.CurrentQueueResult(
                {
                    LINDUSTRIE_ID: damn_lines.QueueObservation(
                        LINDUSTRIE_ID, 12, 9.5, CAPTURED_AT
                    )
                },
                True,
            )
        )
        with patch.object(damn_lines, "get_current_observations", new=current):
            result = await present_places.execute(
                _payload(set_id, [record["places"][0]["place_id"]]),
                _ctx(),
            )

        text = _token_text(result)
        list_at = text.index("1. L'Industrie")
        wait_at = text.index("The latest estimated wait for L'Industrie")
        assert list_at < wait_at
        assert "9.5 minutes" in text
        assert "12 people" in text
        assert "as of 6:58 PM" in text
        assert "and counting" not in text.casefold()
        source = _source_event(result)
        assert source is not None
        assert source.turn_id == "t-queue"
        assert source.sources == (
            GOOGLE_MAPS_SOURCE,
            {
                "title": "Damn Lines: L'industrie Pizzeria",
                "url": "https://damnlines.com/camera/lindustrie-pizzeria",
            },
        )

    async def test_heads_up_failure_without_history_is_silent(self):
        set_id = _store(
            mode="heads_up",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "123 Bleecker St",
                    "open_status": "open",
                    "provider_place_id": LINDUSTRIE_ID,
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        with (
            patch.object(
                damn_lines,
                "get_current_observations",
                new=AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch.object(damn_lines, "get_historical_pattern", return_value=None),
        ):
            result = await present_places.execute(
                _payload(set_id, [record["places"][0]["place_id"]]),
                _ctx(),
            )

        assert "queue" not in _token_text(result).casefold()
        assert "wait" not in _token_text(result).casefold()
        source = _source_event(result)
        assert source is not None
        assert source.sources == (GOOGLE_MAPS_SOURCE,)

    async def test_decision_discloses_missing_live_coverage(self):
        set_id = _store(
            mode="decision",
            places=[
                {
                    "name": "Neighborhood Pizza",
                    "address": "9 Main St",
                    "open_status": "open",
                    "provider_place_id": UNMONITORED_ID,
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        result = await present_places.execute(
            _payload(set_id, [record["places"][0]["place_id"]]),
            _ctx(),
        )

        assert "There is no queue coverage for Neighborhood Pizza." in _token_text(
            result
        )
        source = _source_event(result)
        assert source is not None
        assert source.sources == (GOOGLE_MAPS_SOURCE,)

    async def test_historical_one_date_does_not_say_usually(self):
        set_id = _store(
            mode="historical",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "123 Bleecker St",
                    "open_status": "open",
                    "provider_place_id": LINDUSTRIE_ID,
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        pattern = damn_lines.HistoricalQueuePattern(
            LINDUSTRIE_ID,
            1,
            19,
            14.5,
            11.0,
            8,
            1,
            date(2026, 8, 18),
            date(2026, 8, 18),
        )
        with patch.object(damn_lines, "get_historical_pattern", return_value=pattern):
            result = await present_places.execute(
                _payload(set_id, [record["places"][0]["place_id"]]),
                _ctx(),
            )

        text = _token_text(result)
        assert "On August 18 around 7 PM" in text
        assert "usually" not in text.casefold()
        source = _source_event(result)
        assert source is not None
        assert source.sources[0] == GOOGLE_MAPS_SOURCE

    async def test_historical_multi_date_includes_evidence_count(self):
        set_id = _store(
            mode="historical",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "123 Bleecker St",
                    "open_status": "open",
                    "provider_place_id": LINDUSTRIE_ID,
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        pattern = damn_lines.HistoricalQueuePattern(
            LINDUSTRIE_ID,
            1,
            19,
            14.5,
            11.25,
            48,
            4,
            date(2026, 7, 28),
            date(2026, 8, 18),
        )
        with patch.object(damn_lines, "get_historical_pattern", return_value=pattern):
            result = await present_places.execute(
                _payload(set_id, [record["places"][0]["place_id"]]),
                _ctx(),
            )

        text = _token_text(result)
        assert "Across 4 recorded Tuesday periods around 7 PM" in text
        assert "historical average wait" in text

    async def test_closed_venue_does_not_use_history_in_heads_up(self):
        set_id = _store(
            mode="heads_up",
            places=[
                {
                    "name": "L'Industrie",
                    "address": "123 Bleecker St",
                    "open_status": "closed",
                    "provider_place_id": LINDUSTRIE_ID,
                }
            ],
        )
        record = discovery_store.load_discovery_set(set_id, session_id="sess-pres")
        historical = Mock(return_value=damn_lines.HistoricalQueuePattern(
            LINDUSTRIE_ID, 1, 19, 14.5, 11.0, 8, 2,
            date(2026, 8, 4), date(2026, 8, 18),
        ))
        with (
            patch.object(
                damn_lines,
                "get_current_observations",
                new=AsyncMock(return_value=damn_lines.CurrentQueueResult({}, True)),
            ),
            patch.object(damn_lines, "get_historical_pattern", new=historical),
        ):
            result = await present_places.execute(
                _payload(set_id, [record["places"][0]["place_id"]]),
                _ctx(),
            )

        historical.assert_not_called()
        assert "historical" not in _token_text(result).casefold()
        source = _source_event(result)
        assert source is not None
        assert source.sources == (GOOGLE_MAPS_SOURCE,)
