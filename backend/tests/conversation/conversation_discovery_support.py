"""Batch C support: shared invariants for discovery -> route scenarios.

Non-test module (no ``Test*``/``test_*`` names): pytest never collects it.
Drives the real agent loop (``loop.run_agent_turn``) with the stable
state-valid public surface and the real registered executors for
``discover_places`` / ``present_places`` / ``prepare_route_options`` /
``present_route``, plus the real discovery/candidate/trip stores. Only
genuine provider/data seams and deterministic model rounds are scripted
(see ``tests.conversation.conversation_discovery_fixtures``); Anthropic is mock text.
"""

from __future__ import annotations

import secrets
import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools.places import discover_places
from tests.conversation.conversation_discovery_fixtures import (
    CONFLICTING_LABEL,
    DISCOVERY_MESSAGE,
    DISCOVERY_TOOL_PROFILE,
    FIXED_CANDIDATE_ID,
    FOLLOWUP_MESSAGE,
    FORBIDDEN_TOOLS,
    LEAK_MARKERS,
    ROUTE_TOOL_PROFILE,
    SEARCH_INPUT,
    discovery_leg_for,
    poi_result,
)
from tests.conversation.conversation_matrix_harness import (
    _turn_round,
    clear_caches,
    new_session,
    policy_model,
    route_cards,
    run_turn,
)


class _DiscoveryRouteBase(unittest.IsolatedAsyncioTestCase):
    """Shared invariants for the Batch C discovery lifecycle scenarios."""

    loop = None  # set in setUpClass by subclasses

    def setUp(self):
        clear_caches()

    def _new_session(self, mode: str) -> tuple[str, dict]:
        session_id = f"sess-c-{mode}-{secrets.token_hex(4)}"
        _sid, session = new_session()
        return session_id, session

    def _model_tool_result_blob(self, round_index: int) -> str:
        """Model-facing tool_result content for a recorded model call."""

        content = self.loop.client.messages.calls[round_index]["messages"][-1][
            "content"
        ]
        return str(content)

    async def _discovery_turn(
        self,
        *,
        mode: str,
        scenario_id: str,
        session: dict,
        session_id: str,
    ) -> tuple[dict, str, str, dict]:
        rounds = [
            _turn_round("discover_places", "tu-disc", dict(SEARCH_INPUT)),
            _turn_round(
                "present_places",
                "tu-pres",
                {
                    "discovery_set_id": "ds_disc_1",
                    "selections": [
                        {"place_id": "pl_disc_1", "reason": "top_pick"},
                        {"place_id": "pl_disc_2", "reason": "preference_match"},
                    ],
                    "research_used": False,
                },
            ),
        ]
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        place_ids = iter(("pl_disc_1", "pl_disc_2", "pl_disc_3"))
        with (
            patch.object(
                discover_places.search_local_places,
                "execute",
                new=AsyncMock(return_value=poi_result()),
            ),
            patch.object(
                discovery_store,
                "new_discovery_set_id",
                return_value="ds_disc_1",
            ),
            patch.object(
                discovery_store,
                "new_place_id",
                side_effect=lambda: next(place_ids),
            ),
        ):
            events, trace = await run_turn(
                self.loop,
                session=session,
                session_id=session_id,
                message=DISCOVERY_MESSAGE,
                rounds=rounds,
                mode=mode,
                trace=trace,
                mocks=mocks,
                turn_id="t1",
            )
        state = trip_state_module.get_trip_state(session)
        set_id = state["active_discovery_set_id"]
        self.assertTrue(
            bool(set_id) and set_id.startswith("ds_"),
            f"{scenario_id} search must bind a real server-owned discovery set",
        )
        record = discovery_store.load_discovery_set(set_id, session_id=session_id)
        self.assertIsNotNone(record, f"{scenario_id} stored discovery record")
        self._assert_discovery_turn(
            scenario_id=scenario_id,
            events=events,
            trace=trace,
            mocks=mocks,
            session=session,
            session_id=session_id,
            mode=mode,
            set_id=set_id,
            record=record,
        )
        return session, session_id, set_id, record

    def _assert_discovery_turn(
        self,
        *,
        scenario_id: str,
        events: list,
        trace,
        mocks: dict,
        session: dict,
        session_id: str,
        mode: str,
        set_id: str,
        record: dict,
    ) -> None:
        """Required discovery-only invariants (C-DISC-01/02 and case 5/9/10/11)."""

        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names,
            ["discover_places", "present_places"],
            f"{scenario_id} discovery runs search then canonical presentation",
        )
        self.assertFalse(
            set(names) & set(FORBIDDEN_TOOLS),
            f"{scenario_id} forbidden tool executed",
        )
        self.assertNotIn(
            "prepare_route_options", names, f"{scenario_id} discovery never prepares"
        )
        self.assertNotIn(
            "present_route", names, f"{scenario_id} discovery never presents"
        )
        self.assertNotIn(
            "get_place_details", names, f"{scenario_id} discovery never resolves"
        )
        # Case 5 (both modes): discovery alone must not plan/present a card.
        self.assertEqual(
            route_cards(events), [], f"{scenario_id} discovery emits no route card"
        )
        self.assertEqual(
            mocks["stored_candidate_set_ids"],
            [],
            f"{scenario_id} discovery stores no candidate set",
        )
        self.assertEqual(events[0].type, "meta", f"{scenario_id} meta first")
        self.assertEqual(events[-1].type, "done", f"{scenario_id} done last")
        state = trip_state_module.get_trip_state(session)
        self.assertEqual(
            state["active_candidate_set_id"],
            None,
            f"{scenario_id} no active candidate set after discovery",
        )
        self.assertEqual(
            state["selected_candidate_id"],
            None,
            f"{scenario_id} no selected candidate after discovery",
        )
        self.assertEqual(
            state["selected_place_id"],
            None,
            f"{scenario_id} no place selected until the rider picks one",
        )
        self.assertEqual(
            (
                state["temporary_candidate_set_id"],
                state["temporary_selected_candidate_id"],
                state["temporary_base_candidate_set_id"],
            ),
            (None, None, None),
            f"{scenario_id} no temporary scenario after discovery",
        )
        self.assertEqual(
            state["active_discovery_set_id"], set_id, scenario_id
        )
        # Real stored set: ordinal 2 is the fixture's second place (Auto=Quick).
        self.assertEqual(
            [place["ordinal"] for place in record["places"]],
            [1, 2, 3],
            f"{scenario_id} stored ordinals",
        )
        self.assertEqual(
            record["places"][1]["name"], "B Pizza", f"{scenario_id} ordinal 2"
        )
        self.assertEqual(
            record["places"][1]["provider_place_id"],
            "ChIJ-bbb",
            f"{scenario_id} ordinal 2 stored provider identity",
        )
        for place in record["places"]:
            self.assertTrue(
                place["place_id"].startswith("pl_"),
                f"{scenario_id} opaque place id",
            )
        # Offered tool profile for the discovery intent.
        offered = {
            schema["name"]
            for schema in self.loop.client.messages.calls[0]["tools"]
        }
        self.assertEqual(
            offered,
            DISCOVERY_TOOL_PROFILE,
            f"{scenario_id} discovery tool profile",
        )
        # Auto/Quick differ only in policy; canonical facts stay identical.
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual(
            (trace.initial_mode, trace.final_mode),
            (expected_mode, expected_mode),
            f"{scenario_id} policy mode",
        )
        self.assertEqual(
            [call["model"] for call in self.loop.client.messages.calls],
            [expected_model, expected_model],
            f"{scenario_id} policy models",
        )
        self.assertEqual(
            len(self.loop.client.messages.calls),
            2,
            f"{scenario_id} discovery then one canonical presentation",
        )
        # Rider-facing text is the one bounded server projection and never
        # leaks opaque identities or provider payloads.
        lowered = trace.final_text.casefold()
        for marker in LEAK_MARKERS:
            self.assertNotIn(
                marker, lowered, f"{scenario_id} rider text leaked {marker}"
            )

    async def _followup_turn(
        self,
        *,
        mode: str,
        scenario_id: str,
        session: dict,
        session_id: str,
        set_id: str,
        record: dict,
    ) -> tuple[list, object, dict, dict]:
        """Full real chain: ordinal=2 against the active set, the REAL
        ordinal-2 opaque id (read back from the store) with a conflicting
        free-text label, and the fixed candidate id via the id seam.
        """

        place2 = record["places"][1]
        rounds = [
            _turn_round(
                "prepare_route_options",
                "tu-prepare",
                {
                    "destination": CONFLICTING_LABEL,
                    "destination_place_id": place2["place_id"],
                },
            ),
            _turn_round(
                "present_route",
                "tu-present",
                {"candidate_id": FIXED_CANDIDATE_ID},
            ),
        ]
        trace = self.loop.TurnTrace()
        mocks: dict = {}
        events, trace = await run_turn(
            self.loop,
            session=session,
            session_id=session_id,
            message=FOLLOWUP_MESSAGE,
            rounds=rounds,
            mode=mode,
            trace=trace,
            mocks=mocks,
            turn_id="t2",
            prepare_leg=discovery_leg_for(place2),
            fixed_candidate_id=FIXED_CANDIDATE_ID,
        )
        return events, trace, mocks, place2

    def _assert_followup_attempted(
        self,
        *,
        scenario_id: str,
        events: list,
        trace,
        mocks: dict,
        session: dict,
        session_id: str,
        mode: str,
        place2: dict,
    ) -> None:
        """Partial evidence that holds even when the chain cannot resolve."""

        names = [name for name, _input in trace.tool_calls]
        self.assertEqual(
            names,
            ["prepare_route_options", "present_route"],
            f"{scenario_id} follow-up tool sequence",
        )
        for name in names:
            self.assertEqual(
                names.count(name),
                1,
                f"{scenario_id} exactly one {name} (no duplicate path)",
            )
        self.assertFalse(
            set(names) & set(FORBIDDEN_TOOLS),
            f"{scenario_id} forbidden tool executed on follow-up",
        )
        offered = {
            schema["name"]
            for schema in self.loop.client.messages.calls[0]["tools"]
        }
        self.assertEqual(
            offered,
            ROUTE_TOOL_PROFILE,
            f"{scenario_id} follow-up tool profile",
        )
        self.assertEqual(
            trace.tool_calls[0][1]["destination_place_id"],
            place2["place_id"],
            f"{scenario_id} prepare routes by the real ordinal-2 opaque id",
        )
        expected_mode, expected_model = policy_model(self.loop, mode)
        self.assertEqual(
            (trace.initial_mode, trace.final_mode),
            (expected_mode, expected_mode),
            f"{scenario_id} policy mode",
        )
        # Success path: prepare then present, then terminal card text.
        self.assertEqual(
            [call["model"] for call in self.loop.client.messages.calls],
            [expected_model] * 2,
            f"{scenario_id} policy models",
        )
        lowered = trace.final_text.casefold()
        for marker in LEAK_MARKERS:
            self.assertNotIn(
                marker, lowered, f"{scenario_id} rider text leaked {marker}"
            )

    def _assert_followup_chain(
        self,
        *,
        scenario_id: str,
        events: list,
        trace,
        mocks: dict,
        session: dict,
        session_id: str,
        mode: str,
        set_id: str,
        place2: dict,
    ) -> None:
        """The required full-chain invariants, with all evidence embedded."""

        end_map = {
            event.tool: (event.ok, event.summary)
            for event in events
            if event.type == "tool_end"
        }
        state = trip_state_module.get_trip_state(session)
        context = str(self.loop.client.messages.calls[0]["messages"][-1]["content"])
        self.assertEqual(
            events[-1].type, "done", f"{scenario_id} terminal event"
        )
        with self.subTest(gap="ordinal2_resolution"):
            self.assertEqual(
                trace.tool_calls[0][1].get("destination_place_id"),
                place2["place_id"],
                f"{scenario_id} prepare must use the REAL stored ordinal-2 "
                f"opaque id; tool_ends={end_map}; tool_calls={trace.tool_calls}; "
                f"state.active_discovery_set_id={state['active_discovery_set_id']!r}; "
                f"context_has_active_discovery_line={'active_discovery: ' in context}; "
                f"context_tail={context[-500:]!r}",
            )
        with self.subTest(gap="stored_identity_at_boundary"):
            self.assertEqual(
                mocks["prepare_single_leg"].await_count,
                1,
                f"{scenario_id} real prepare executor must reach the provider "
                f"seam exactly once; actual={mocks['prepare_single_leg'].await_count}",
            )
            resolved = (
                mocks["prepare_single_leg"].await_args.kwargs.get(
                    "resolved_destination"
                )
                if mocks["prepare_single_leg"].await_count
                else None
            )
            self.assertIsNotNone(
                resolved,
                f"{scenario_id} provider boundary must receive the stored "
                f"canonical destination",
            )
            self.assertEqual(
                resolved.name, place2["name"], f"{scenario_id} stored name wins"
            )
            self.assertEqual(
                resolved.latitude,
                float(place2["latitude"]),
                f"{scenario_id} stored latitude wins",
            )
            self.assertEqual(
                resolved.longitude,
                float(place2["longitude"]),
                f"{scenario_id} stored longitude wins",
            )
            self.assertEqual(
                resolved.place_id,
                place2["place_id"],
                f"{scenario_id} stored opaque identity wins",
            )
            self.assertEqual(
                resolved.provider_place_id,
                place2["provider_place_id"],
                f"{scenario_id} stored provider identity stays private",
            )
        with self.subTest(gap="single_card_commit"):
            cards = route_cards(events)
            self.assertEqual(
                len(cards),
                1,
                f"{scenario_id} exactly one route card; actual={len(cards)}",
            )
            self.assertEqual(
                len(mocks["stored_candidate_set_ids"]),
                1,
                f"{scenario_id} exactly one candidate set stored on the "
                f"follow-up; actual={mocks['stored_candidate_set_ids']}",
            )
            expected_set_id = (
                mocks["stored_candidate_set_ids"][0]
                if mocks["stored_candidate_set_ids"]
                else None
            )
            self.assertEqual(
                state["active_candidate_set_id"],
                expected_set_id,
                f"{scenario_id} accepted active candidate set committed",
            )
            self.assertEqual(
                state["selected_candidate_id"],
                FIXED_CANDIDATE_ID,
                f"{scenario_id} accepted selected candidate committed",
            )
            self.assertEqual(
                (
                    state["temporary_candidate_set_id"],
                    state["temporary_selected_candidate_id"],
                    state["temporary_base_candidate_set_id"],
                ),
                (None, None, None),
                f"{scenario_id} no temporary scenario after the commit",
            )
            self.assertEqual(
                state["selected_place_id"],
                place2["place_id"],
                f"{scenario_id} canonical selected place stays the ordinal-2 "
                f"opaque id",
            )
            self.assertEqual(
                state["active_discovery_set_id"],
                set_id,
                f"{scenario_id} discovery context stays the real set",
            )
            self.assertEqual(
                state["destination"],
                place2["name"],
                f"{scenario_id} destination is the stored canonical name",
            )


__all__ = ("_DiscoveryRouteBase",)
