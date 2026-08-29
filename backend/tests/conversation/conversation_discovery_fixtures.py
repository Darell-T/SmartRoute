"""Batch C fixtures and constants for discovery -> route scenarios.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Kept separate from ``conversation_discovery_support`` so every
Batch C source file stays well below the 500-line limit.

Only genuine external/provider/data seams are scripted (identical to the
documented Batch A/B harness seams):

- ``search_local_places.execute`` -- the provider place-search seam
  inside the real discovery executor (raw Google-Places-shaped fixtures come
  from here; ``search_local_places`` itself stores and sanitizes them).
- ``prepare_route_options.prepare_single_leg`` -- the provider route/evidence
  seam of the real canonical prepare executor.

The follow-up ``prepare_route_options`` round is built from the **real** opaque
place id read back out of the real discovery store after the discovery turn --
production ids are never invented or hard-coded across turns.
"""

from __future__ import annotations

from app.services.agent.tools._types import ToolResult
from app.services.agent.tools.location_resolution import ResolvedPlace
from app.services.agent.tools.route.preparation_adapter import PreparedLeg

from tests.conversation.conversation_matrix_harness import (
    PUBLIC_TOOL_PROFILE,
    discover_search_input,
    make_leg,
)

DISCOVERY_MESSAGE = "Find me pizza places in Brooklyn."
FOLLOWUP_MESSAGE = "Take me to the second one."
REFERENCE_MESSAGE = "The second one."
NAVIGATION_MESSAGE = "Take me there."
CONFLICTING_LABEL = "Completely Different Text"
FIXED_CANDIDATE_ID = "cd_batch_c_route_1"

SEARCH_INPUT = discover_search_input("pizza Brooklyn", borough="Brooklyn")

ROUTE_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
DISCOVERY_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
# Offered surface is always the eight public tools. Selection/navigation
# still execute only the capability the scripted model chooses.
DISCOVERY_REFERENCE_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
ROUTE_NAVIGATION_TOOL_PROFILE = set(PUBLIC_TOOL_PROFILE)
FORBIDDEN_TOOLS = (
    "plan_trip",
    "web_search",
    "poi_search",
    "event_lookup",
    "transit_snapshot",
    "lookup_arrivals",
    "lookup_facts",
    "venue_crowd_window",
    "check_area_conditions",
)
REFERENCE_FORBIDDEN_TOOLS = (*FORBIDDEN_TOOLS, "prepare_route_options", "present_route", "search_local_places")
NAVIGATION_FORBIDDEN_TOOLS = (*FORBIDDEN_TOOLS, "search_local_places")
LEAK_MARKERS = ("pl_", "ds_", "cd_", "cs_", "rc_", "chij")

# Raw provider-shaped fixture: three distinct places; ordinal 2 is "B Pizza".
# Coordinates/provider ids are the "stored" canonical facts the chain must
# preserve at the route boundary even when the model supplies another label.
PLACES_FIXTURE = (
    {
        "name": "A Pizza",
        "address": "1 A St, Brooklyn, NY",
        "lat": 40.71,
        "lng": -73.98,
        "open_now": True,
        "price_level": 2,
        "rating": 4.7,
        "review_count": 500,
        "place_id": "ChIJ-aaa",
        "address_components": [
            {"longText": "Brooklyn", "types": ["sublocality_level_1"]}
        ],
    },
    {
        "name": "B Pizza",
        "address": "2 B Ave, Brooklyn, NY",
        "lat": 40.72,
        "lng": -73.97,
        "open_now": True,
        "price_level": 1,
        "rating": 4.2,
        "review_count": 100,
        "place_id": "ChIJ-bbb",
        "address_components": [
            {"longText": "Brooklyn", "types": ["sublocality_level_1"]}
        ],
    },
    {
        "name": "C Pizza",
        "address": "3 C St, Brooklyn, NY",
        "lat": 40.70,
        "lng": -73.96,
        "open_now": True,
        "price_level": 3,
        "rating": 4.9,
        "review_count": 900,
        "place_id": "ChIJ-ccc",
        "address_components": [
            {"longText": "Brooklyn", "types": ["sublocality_level_1"]}
        ],
    },
)


def poi_result() -> ToolResult:
    """Provider seam output: raw Places-shaped results (never sanitized yet)."""

    return ToolResult(
        ok=True,
        data={"results": [dict(place) for place in PLACES_FIXTURE]},
        summary="3 places",
    )


def discovery_leg_for(place: dict) -> PreparedLeg:
    """One canonical prepared leg carrying the STORED ordinal-N identity.

    Mirrors what the real provider seam returns after
    ``prepare_single_leg`` consumed the server-resolved destination: the
    destination_place carries the stored name, coordinates, address, and
    provider place id -- never a model-retyped label.
    """

    leg = make_leg(route_ids=("Q",), destination=place["name"])
    leg.destination_place = ResolvedPlace(
        name=place["name"],
        latitude=float(place["latitude"]),
        longitude=float(place["longitude"]),
        source="discovery",
        address=place.get("address") or None,
        place_id=place.get("provider_place_id"),
    )
    leg.destination_raw = place["name"]
    return leg


__all__ = (
    "CONFLICTING_LABEL",
    "DISCOVERY_MESSAGE",
    "DISCOVERY_REFERENCE_TOOL_PROFILE",
    "DISCOVERY_TOOL_PROFILE",
    "FIXED_CANDIDATE_ID",
    "FOLLOWUP_MESSAGE",
    "FORBIDDEN_TOOLS",
    "LEAK_MARKERS",
    "NAVIGATION_FORBIDDEN_TOOLS",
    "NAVIGATION_MESSAGE",
    "PLACES_FIXTURE",
    "REFERENCE_FORBIDDEN_TOOLS",
    "REFERENCE_MESSAGE",
    "ROUTE_NAVIGATION_TOOL_PROFILE",
    "ROUTE_TOOL_PROFILE",
    "SEARCH_INPUT",
    "discovery_leg_for",
    "poi_result",
)
