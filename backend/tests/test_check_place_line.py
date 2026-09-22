"""Direct line checks for monitored venues."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.model import request as model_request
from app.services.agent.tools.base import ToolContext
from app.services.agent.tools.places import check_place_line, damn_lines
from app.services.agent.turn.contract import TurnContract
from app.services.agent.turn.evidence import TurnEvidence

LINDUSTRIE_ID = "ChIJ92OsaJVZwokRsC54kf-J-3g"
CAPTURED_AT = datetime(2026, 9, 21, 22, 21, tzinfo=UTC)


def _ctx() -> ToolContext:
    return ToolContext(session={}, session_id="sess-line")


def _ready(place_id: str = LINDUSTRIE_ID) -> damn_lines.CurrentQueueResult:
    return damn_lines.CurrentQueueResult(
        {
            place_id: damn_lines.QueueObservation(place_id, 20, 18.2, CAPTURED_AT),
        },
        True,
    )


class CheckPlaceLineTests(unittest.IsolatedAsyncioTestCase):
    async def test_west_village_name_returns_the_live_line(self):
        with patch.object(
            damn_lines,
            "get_current_observations",
            new=AsyncMock(return_value=_ready()),
        ) as current:
            result = await check_place_line.execute(
                {
                    "goal_key": "",
                    "venue_name": "L'industrie",
                    "area": "West Village",
                    "place_id": "",
                },
                _ctx(),
            )

        current.assert_awaited_once_with([LINDUSTRIE_ID])
        assert result.ok
        assert result.data == {
            "status": "ready",
            "name": "L'industrie Pizzeria",
            "area": "West Village",
            "wait_minutes": 18.2,
            "people_count": 20,
            "observed_at": "6:21 PM",
        }

    async def test_other_branch_is_not_monitored(self):
        with patch.object(
            damn_lines, "get_current_observations", new=AsyncMock()
        ) as current:
            result = await check_place_line.execute(
                {
                    "goal_key": "",
                    "venue_name": "L'industrie",
                    "area": "Williamsburg",
                    "place_id": "",
                },
                _ctx(),
            )

        current.assert_not_awaited()
        assert result.data == {"status": "not_monitored"}

    async def test_shared_name_asks_which_place(self):
        result = await check_place_line.execute(
            {
                "goal_key": "",
                "venue_name": "Salt",
                "area": "",
                "place_id": "",
            },
            _ctx(),
        )
        assert result.data["status"] == "ambiguous"
        assert result.data["names"] == [
            "Breakfast by Salt's Cure",
            "Salt Hank's",
        ]

    async def test_missing_observation_is_unavailable(self):
        with patch.object(
            damn_lines,
            "get_current_observations",
            new=AsyncMock(return_value=damn_lines.CurrentQueueResult({}, False)),
        ):
            result = await check_place_line.execute(
                {
                    "goal_key": "",
                    "venue_name": "Golden Diner",
                    "area": "",
                    "place_id": "",
                },
                _ctx(),
            )
        assert result.data["status"] == "unavailable"
        assert result.data["name"] == "Golden Diner"
        assert "wait_minutes" not in result.data

    async def test_discovered_place_id_uses_the_registry_match(self):
        session: dict = {}
        set_id = discovery_store.store_discovery_set(
            session_id="sess-line",
            session=session,
            places=[
                {
                    "name": "L'industrie Pizzeria",
                    "address": "104 Christopher St",
                    "provider_place_id": LINDUSTRIE_ID,
                    "latitude": 40.73,
                    "longitude": -74.0,
                }
            ],
        )
        trip_state_module.bind_discovery_set(session, set_id)
        record = discovery_store.load_discovery_set(set_id, session_id="sess-line")
        place_id = record["places"][0]["place_id"]
        with patch.object(
            damn_lines,
            "get_current_observations",
            new=AsyncMock(return_value=_ready()),
        ):
            result = await check_place_line.execute(
                {
                    "goal_key": "",
                    "venue_name": "",
                    "area": "",
                    "place_id": place_id,
                },
                ToolContext(session=session, session_id="sess-line"),
            )
        assert result.data["status"] == "ready"
        assert result.data["name"] == "L'industrie Pizzeria"

    def test_general_response_offers_the_line_check(self):
        evidence = TurnEvidence()
        evidence.bind_contract(
            TurnContract.from_payload(
                {
                    "goals": [
                        {
                            "goal_key": "line",
                            "kind": "general_response",
                            "depends_on": [],
                        }
                    ]
                }
            )
        )
        names = {
            schema["name"]
            for schema in model_request.tools_for_state(turn_evidence=evidence)
        }
        assert names == {"complete_turn", "check_place_line"}
