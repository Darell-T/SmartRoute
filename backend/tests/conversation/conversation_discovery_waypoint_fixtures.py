"""Batch D1 fixtures and constants for the discovery -> waypoint lifecycle.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Kept separate from ``conversation_discovery_waypoint_support`` so
every Batch D1 source file stays well below the 500-line limit.

The D1 transcript is the exact five-turn discovery -> waypoint -> removal
sequence required by the Batch D1 audit, run in ONE real server-owned session
for Auto and Quick:

  turn 1: "Get me to Barclays."                    (accepted destination route)
  turn 2: "Find pizza near Barclays."              (discovery, no route mutation)
  turn 3: "Which one is easiest to reach?"         (comparison, no trip mutation)
  turn 4: "Take me to the second one first."       (ordinal-2 intermediate waypoint)
  turn 5: "Actually remove the pizza stop."        (waypoint removal, restore trip)

Only genuine external/provider/data seams are scripted, identical to the
documented Batch A/B/C harness seams:

- ``search_local_places.execute`` -- the provider place-search seam
  inside the real discovery executor (raw Places-shaped fixtures; the real
  ``search_local_places`` executor stores and sanitizes them).
- ``prepare_route_options.prepare_single_leg`` -- the provider route/evidence
  seam of the real canonical prepare executor (also the per-segment seam of
  the real multi-stop path).
- ``trips.enrichment._enrich_route`` and ``tools.lookup_arrivals.execute`` --
  live enrichment/arrival fetches the real ``present_route`` may call.
- ``candidate_store.new_candidate_id`` -- opaque id generation, only for a
  deterministic server-issued candidate id in scripted ``present_route`` input
  (the record is still written by the real store).
- ``candidate_store.store_candidate_set`` -- observed, never replaced.

All real discovery/place/set/candidate ids are read back out of the real
stores between turns; production ids are never invented or hard-coded across
turns. Reused place fixtures come from ``tests.conversation.conversation_discovery_fixtures``
(Batch C): three stored pizza places, ordinal 2 = "B Pizza".
"""

from __future__ import annotations

from app.services.agent.tools.location_resolution import ResolvedPlace
from tests.conversation.conversation_discovery_fixtures import (
    CONFLICTING_LABEL,
    DISCOVERY_TOOL_PROFILE,
    LEAK_MARKERS,
    ROUTE_TOOL_PROFILE,
    discovery_leg_for,
    poi_result,
)
from tests.conversation.conversation_matrix_harness import discover_search_input, make_leg

# Exact Batch D1 transcript (mode-identical).
M1_GET_BARCLAYS = "Get me to Barclays."
M2_FIND_PIZZA = "Find pizza near Barclays."
M3_WHICH_EASIEST = "Which one is easiest to reach?"
M4_SECOND_FIRST = "Take me to the second one first."
M5_REMOVE_STOP = "Actually remove the pizza stop."

# Canonical destination identity. "Barclays" resolves through the real known-
# place registry to "Barclays Center"; the accepted trip keeps the raw label
# "Barclays" in trip state while cards/candidates carry the canonical name.
DESTINATION_LABEL = "Barclays"
BARCLAYS_CANONICAL_NAME = "Barclays Center"

# Turn 2 discovery: a narrow provider-shaped search near the accepted
# destination. Returns the real stored set with three opaque pl_ records.
SEARCH_INPUT = discover_search_input("pizza Barclays", borough=None)

# Server-issued candidate ids used only as scripted present_route input; the
# candidate records are still written and selected by the real store.
FIXED_CANDIDATE_BARCLAYS = "cd_batch_d1_barclays_1"
FIXED_CANDIDATE_WAYPOINT = "cd_batch_d1_waypoint_1"
FIXED_CANDIDATE_REMOVAL = "cd_batch_d1_removal_1"

# Turn 3 is a comparison question. Current production classifies it as a
# transit_question, so the real request would offer the transit/status surface
# plus the single server-side web_search; the bounded scripted model uses no
# tools and asserts none execute.
TRANSIT_QUESTION_TOOL_PROFILE = frozenset(DISCOVERY_TOOL_PROFILE)

# Tools that must never execute anywhere in the D1 transcript.
FORBIDDEN_TOOLS = ("plan_trip", "poi_search")

# Turn 1: exactly the canonical route profile is expected.
TURN1_EXPECTED_PROFILE = ROUTE_TOOL_PROFILE
TURN1_FORBIDDEN = FORBIDDEN_TOOLS + (
    "search_local_places",
    "web_search",
    "get_place_details",
    "transit_snapshot",
    "event_lookup",
    "venue_crowd_window",
    "lookup_arrivals",
    "lookup_facts",
    "accessibility_status",
    "check_area_conditions",
)

# Turn 2: discovery superset (route profile + search + web).
TURN2_EXPECTED_PROFILE = DISCOVERY_TOOL_PROFILE
TURN2_FORBIDDEN = FORBIDDEN_TOOLS + (
    "prepare_route_options",
    "present_route",
    "get_place_details",
    "transit_snapshot",
    "event_lookup",
    "venue_crowd_window",
    "lookup_arrivals",
    "lookup_facts",
    "accessibility_status",
    "check_area_conditions",
    "web_search",
)

# Turn 3: comparison question -- no executed tools expected.
TURN3_FORBIDDEN = FORBIDDEN_TOOLS + (
    "prepare_route_options",
    "present_route",
    "search_local_places",
    "get_place_details",
    "lookup_arrivals",
    "event_lookup",
    "transit_snapshot",
    "venue_crowd_window",
    "check_area_conditions",
    "web_search",
)

# Turn 4: ordinal-2 waypoint addition through the canonical route profile.
TURN4_EXPECTED_PROFILE = ROUTE_TOOL_PROFILE
TURN4_FORBIDDEN = FORBIDDEN_TOOLS + (
    "search_local_places",
    "web_search",
    "transit_snapshot",
    "event_lookup",
    "venue_crowd_window",
    "lookup_arrivals",
    "lookup_facts",
    "check_area_conditions",
)

# Turn 5: waypoint removal -- canonical prepare with explicit empty waypoints.
TURN5_EXPECTED_PROFILE = ROUTE_TOOL_PROFILE
TURN5_FORBIDDEN = FORBIDDEN_TOOLS + (
    "search_local_places",
    "get_place_details",
    "web_search",
    "transit_snapshot",
    "event_lookup",
    "venue_crowd_window",
    "lookup_arrivals",
    "lookup_facts",
    "check_area_conditions",
)

# Opaque identifiers and provider payload markers that must never leak into
# rider-facing text (or into model context for provider identities/coords).
CONTEXT_LEAK_MARKERS = ("latitude", "longitude", "ChIJ", "provider_place_id")

# Re-exported helpers: the provider seam output and the canonical leg fixture.
POI_RESULT = poi_result
DISCOVERY_LEG_FOR = discovery_leg_for


def barclays_leg():
    """One canonical prepared leg carrying the accepted Barclays destination."""

    return make_leg(route_ids=("Q",), destination=BARCLAYS_CANONICAL_NAME)


def waypoint_segment_legs(place2: dict) -> list:
    """The two DISTINCT real-shaped provider segments of the turn-4 chain.

    Segment 1 routes user -> stored B Pizza (stored identity, coordinates,
    and provider place id at the provider boundary); segment 2 routes stored
    B Pizza -> the inherited Barclays Center destination. Each
    ``prepare_single_leg`` call of the real multi-stop path returns exactly
    one of these in call order, so a B-Pizza-to-B-Pizza phantom segment can
    never be built.
    """

    first = discovery_leg_for(place2)
    second = barclays_leg()
    second.origin_place = ResolvedPlace(
        name=place2["name"],
        latitude=float(place2["latitude"]),
        longitude=float(place2["longitude"]),
        source="discovery",
        address=place2.get("address") or None,
        place_id=place2.get("provider_place_id"),
    )
    return [first, second]


__all__ = (
    "BARCLAYS_CANONICAL_NAME",
    "CONFLICTING_LABEL",
    "CONTEXT_LEAK_MARKERS",
    "DESTINATION_LABEL",
    "DISCOVERY_LEG_FOR",
    "DISCOVERY_TOOL_PROFILE",
    "FIXED_CANDIDATE_BARCLAYS",
    "FIXED_CANDIDATE_REMOVAL",
    "FIXED_CANDIDATE_WAYPOINT",
    "FORBIDDEN_TOOLS",
    "LEAK_MARKERS",
    "M1_GET_BARCLAYS",
    "M2_FIND_PIZZA",
    "M3_WHICH_EASIEST",
    "M4_SECOND_FIRST",
    "M5_REMOVE_STOP",
    "POI_RESULT",
    "ROUTE_TOOL_PROFILE",
    "SEARCH_INPUT",
    "TRANSIT_QUESTION_TOOL_PROFILE",
    "TURN1_EXPECTED_PROFILE",
    "TURN1_FORBIDDEN",
    "TURN2_EXPECTED_PROFILE",
    "TURN2_FORBIDDEN",
    "TURN3_FORBIDDEN",
    "TURN4_EXPECTED_PROFILE",
    "TURN4_FORBIDDEN",
    "TURN5_EXPECTED_PROFILE",
    "TURN5_FORBIDDEN",
    "barclays_leg",
    "waypoint_segment_legs",
)
