"""Batch F1 audit fixtures: the per-turn offered-tool enforcement boundary.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest
never collects it.

The emitted leaf-tool names in these fixtures are genuinely REGISTERED
internal executors (asserted against ``COMBINED_TOOL_REGISTRY``) that the
model-led public surface deliberately does NOT offer. The first request has
the five initial capabilities; after ``declare_goals`` each transcript uses
the small state-valid subset for its declared outcome.
Native ``web_search`` is evidence-gated and is not on the first round.
"""

from __future__ import annotations

from app.services.agent.public_surface import INITIAL_TOOL_NAMES
from app.services.agent.tools import COMBINED_TOOL_REGISTRY
from tests.conversation.conversation_matrix_harness import check_transit_input

# Deterministic messages whose parsed intents own a bounded tool profile.
TRANSIT_QUESTION_MESSAGE = "Is the Q train running normally?"
TRANSIT_FACT_MESSAGE = "What are the subway transfer rules?"
ROUTE_PLANNING_MESSAGE = "Plan a trip to Coney Island."
DISCOVERY_MESSAGE = "Find me pizza places in Brooklyn."

# The first model request is state-valid before any model goal declaration.
INITIAL_TOOL_PROFILE = frozenset(INITIAL_TOOL_NAMES)
TRANSIT_QUESTION_TOOL_PROFILE = INITIAL_TOOL_PROFILE
TRANSIT_FACT_TOOL_PROFILE = INITIAL_TOOL_PROFILE
ROUTE_PLANNING_TOOL_PROFILE = INITIAL_TOOL_PROFILE
DISCOVERY_PROFILE = INITIAL_TOOL_PROFILE

# State-valid subsets after the model declares one outcome goal.
TRANSIT_STATE_VALID_PROFILE = frozenset(
    {"check_transit", "complete_turn"}
)
ROUTE_STATE_VALID_PROFILE = frozenset(
    {"discover_places", "prepare_route_options", "complete_turn"}
)
DISCOVERY_STATE_VALID_PROFILE = frozenset(
    {"discover_places", "complete_turn"}
)
TRANSIT_READY_PROFILE = frozenset({"present_transit", "complete_turn"})
ROUTE_READY_PROFILE = frozenset({"present_route", "complete_turn"})
DISCOVERY_READY_PROFILE = frozenset(
    {"present_places", "complete_turn", "web_search"}
)
GENERAL_RESPONSE_STATE_VALID_PROFILE = frozenset({"complete_turn"})

TRANSIT_FACT_GOALS_INPUT = {
    "goals": [
        {"goal_key": "transit", "kind": "transit_fact", "depends_on": []}
    ]
}
SERVICE_STATUS_GOALS_INPUT = {
    "goals": [
        {"goal_key": "transit", "kind": "service_status", "depends_on": []}
    ]
}
ROUTE_GOALS_INPUT = {
    "goals": [{"goal_key": "route", "kind": "route", "depends_on": []}]
}
DISCOVERY_GOALS_INPUT = {
    "goals": [
        {
            "goal_key": "places",
            "kind": "place_recommendation",
            "depends_on": [],
        }
    ]
}
GENERAL_RESPONSE_GOALS_INPUT = {
    "goals": [
        {
            "goal_key": "response",
            "kind": "general_response",
            "depends_on": [],
        }
    ]
}

# Model-emitted tool payloads for the audit probes.
PREPARE_ROUTE_OPTIONS_INPUT = {
    "destination": "Coney Island",
    "destination_source": "current_turn",
}
PRESENT_ROUTE_FRAMING_INPUT = {
    "lead_in": "The route options were close, so I chose this one for your trip.",
    "follow_up": "",
    "reason_code": "meets_hard_constraints",
}
SEARCH_LOCAL_PLACES_INPUT = {"query": "pizza Brooklyn", "max_results": 3}
DISCOVER_PLACES_INPUT = {
    "operation": "search",
    "query": "pizza Brooklyn",
    "scope": {"kind": "boroughs", "values": ["Brooklyn"]},
    "open_now": None,
    "max_results": 8,
    "candidate_names": [],
}
TRANSIT_SNAPSHOT_INPUT = {"lines": ["Q"]}
UNKNOWN_TOOL_NAME = "totally_unknown_tool"
UNKNOWN_TOOL_INPUT = {"anything": 1}
LOOKUP_FACTS_INPUT = {"topic": "transfers"}
CHECK_TRANSIT_FACT_INPUT = check_transit_input("fact", topic="transfers")
FIXED_CANDIDATE_ID = "cd_f1_offered_control"


def registered_tool_names() -> frozenset[str]:
    """All dispatch-registered tool names at probe time, including leaves."""

    return frozenset(COMBINED_TOOL_REGISTRY)


__all__ = (
    "CHECK_TRANSIT_FACT_INPUT",
    "DISCOVER_PLACES_INPUT",
    "DISCOVERY_GOALS_INPUT",
    "DISCOVERY_MESSAGE",
    "DISCOVERY_PROFILE",
    "DISCOVERY_READY_PROFILE",
    "DISCOVERY_STATE_VALID_PROFILE",
    "GENERAL_RESPONSE_GOALS_INPUT",
    "GENERAL_RESPONSE_STATE_VALID_PROFILE",
    "INITIAL_TOOL_PROFILE",
    "LOOKUP_FACTS_INPUT",
    "PREPARE_ROUTE_OPTIONS_INPUT",
    "PRESENT_ROUTE_FRAMING_INPUT",
    "ROUTE_GOALS_INPUT",
    "ROUTE_PLANNING_MESSAGE",
    "ROUTE_PLANNING_TOOL_PROFILE",
    "ROUTE_READY_PROFILE",
    "ROUTE_STATE_VALID_PROFILE",
    "SEARCH_LOCAL_PLACES_INPUT",
    "SERVICE_STATUS_GOALS_INPUT",
    "TRANSIT_QUESTION_MESSAGE",
    "TRANSIT_QUESTION_TOOL_PROFILE",
    "TRANSIT_FACT_MESSAGE",
    "TRANSIT_FACT_GOALS_INPUT",
    "TRANSIT_FACT_TOOL_PROFILE",
    "TRANSIT_READY_PROFILE",
    "TRANSIT_STATE_VALID_PROFILE",
    "TRANSIT_SNAPSHOT_INPUT",
    "UNKNOWN_TOOL_INPUT",
    "UNKNOWN_TOOL_NAME",
    "registered_tool_names",
)
