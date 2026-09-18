"""Batch E1 fixtures/constants for discovery-reference safety audits.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Kept separate from ``conversation_reference_safety_support`` so
every Batch E1 source file stays well below the 500-line limit.

Only genuine external/provider/data seams are scripted, identical to the
documented Batch A/B/C/D harness seams:

- ``discover_places.search_local_places.execute`` -- the provider place-search seam
  inside the real discovery executor.
- ``prepare_route_options.prepare_single_leg`` -- the provider route/evidence
  seam of the real canonical prepare executor (only as a recording seam for
  the label-fallback probe; the canonical stale-reference path must never
  reach it).
- ``candidate_store.store_candidate_set`` -- observed, never replaced.

Reused Batch C fixtures: the three-place pizza set (ordinal 2 = "B Pizza"),
discovery/reference/navigation messages, ``poi_result``, ``discovery_leg_for``,
``CONFLICTING_LABEL``, and the canonical model-led tool profiles.
"""

from __future__ import annotations

from tests.conversation.conversation_discovery_fixtures import (
    CONFLICTING_LABEL,
    DISCOVERY_MESSAGE,
    LEAK_MARKERS,
    NAVIGATION_MESSAGE,
    REFERENCE_MESSAGE,
    SEARCH_INPUT,
    discovery_leg_for,
    poi_result,
)

# The model-led loop exposes only provider capabilities on the initial model
# request.  State-valid presenters are added after their corresponding
# declaration/evidence is available; they are never part of this startup
# profile.
INITIAL_TOOL_PROFILE = frozenset(
    {
        "declare_goals",
        "discover_places",
        "check_transit",
        "prepare_route_options",
        "complete_turn",
    }
)

DISCOVERY_TOOL_PROFILE = INITIAL_TOOL_PROFILE
DISCOVERY_REFERENCE_TOOL_PROFILE = INITIAL_TOOL_PROFILE
ROUTE_NAVIGATION_TOOL_PROFILE = INITIAL_TOOL_PROFILE

# Exact Batch E1 transcript messages (mode-identical).
RECOVERY_MESSAGE = "Okay, search again."
CONTROL_RESEARCH_MESSAGE = "Find me pizza places."
COFFEE_MESSAGE = "Find me coffee places."

# The minimum structured discovery search path the exact recovery message
# MUST offer (the "search again" recovery contract): the one real executor
# that creates a fresh session-owned discovery set.
RECOVERY_REQUIRED_PROFILE = frozenset(DISCOVERY_TOOL_PROFILE)

# Production's actual startup offer for a reference/transit turn.  The
# state-valid presenter projection is asserted by the loop transcript after
# the declaration when evidence exists.
TRANSIT_QUESTION_TOOL_PROFILE = frozenset(DISCOVERY_TOOL_PROFILE)

# Invented/malicious opaque values that must never bind anything through the
# real ``present_places`` inputs.
INVENTED_PLACE_ID = "pl_e1_invented_place_000"
INVENTED_SET_ID = "ds_e1_invented_set_000"

EXPIRED_ERROR_MARKER = "expired"
CROSS_SESSION_ERROR_MARKER = "not owned"
PLACE_ID_UNKNOWN_MARKER = "unknown for this discovery set"

__all__ = (
    "COFFEE_MESSAGE",
    "CONFLICTING_LABEL",
    "CONTROL_RESEARCH_MESSAGE",
    "CROSS_SESSION_ERROR_MARKER",
    "DISCOVERY_MESSAGE",
    "DISCOVERY_REFERENCE_TOOL_PROFILE",
    "DISCOVERY_TOOL_PROFILE",
    "EXPIRED_ERROR_MARKER",
    "INITIAL_TOOL_PROFILE",
    "INVENTED_PLACE_ID",
    "INVENTED_SET_ID",
    "LEAK_MARKERS",
    "NAVIGATION_MESSAGE",
    "PLACE_ID_UNKNOWN_MARKER",
    "RECOVERY_MESSAGE",
    "RECOVERY_REQUIRED_PROFILE",
    "REFERENCE_MESSAGE",
    "ROUTE_NAVIGATION_TOOL_PROFILE",
    "SEARCH_INPUT",
    "TRANSIT_QUESTION_TOOL_PROFILE",
    "discovery_leg_for",
    "poi_result",
)
