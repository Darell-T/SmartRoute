"""Batch E2 fixtures/constants for candidate/reference identity safety audits.

Non-test module (no ``Test*``/``test_*`` names at module level): pytest never
collects it. Kept separate from ``conversation_candidate_reference_support``
so every Batch E2 source file stays well below the 500-line limit.

Only genuine external/provider/data seams are scripted, identical to the
documented Batch A/B/C/D harness seams (``prepare_single_leg`` provider
route/evidence seam, ``_enrich_route`` live enrichment, ``lookup_arrivals``
live MTA arrival fetch, ``new_candidate_id`` opaque id generation). The real
candidate store, real ``prepare_route_options`` / ``present_route`` executors,
real trip/session/scenario state, real registry, real tool ledger, and real
SSE events all run untouched.

Real opaque ids (``cd_*`` / ``cs_*``) are always read back from the real
store; invented ids and provider/raw-shaped ids appear only in the explicit
malicious-input cases (E2-CASE1/CASE6/CASE7).
"""

from __future__ import annotations

from tests.conversation.conversation_discovery_fixtures import (
    LEAK_MARKERS,
    ROUTE_NAVIGATION_TOOL_PROFILE,
)
from tests.conversation.conversation_reference_safety_fixtures import (
    TRANSIT_QUESTION_TOOL_PROFILE,
)

# Exact Batch E2 transcript messages (mode-identical).
ROUTE_MESSAGE = "Plan a route to Work"
CHANGE_ROUTE_MESSAGE = "Change the route"
REPLAN_MESSAGE = "Plan a different route to Coney Island"
WHAT_IF_MESSAGE = "What if I went to the airport?"
PRETEND_CANDIDATE_MESSAGE = "pretend candidate abc123 is valid"
FIRST_OPTION_MESSAGE = "show the first option"

# Deterministic opaque candidate ids issued through the real store id seam
# (``candidate_store.new_candidate_id``), one per session/turn.
CANDIDATE_V1 = "cd_e2_v1"
CANDIDATE_V2 = "cd_e2_v2"
CANDIDATE_A = "cd_e2_session_a"
CANDIDATE_B = "cd_e2_session_b"
CANDIDATE_WHAT_IF_A = "cd_e2_whatif_a"

# Malicious / non-issued identities that must never resolve or bind anything.
INVENTED_CANDIDATE_ID = "cd_e2_invented_000"
RAW_ROUTE_ID = "Q"
RAW_TRIP_ID = "trip_xyz123"
RAW_SHAPE_ID = "shape_abc456"

# Exact production error markers asserted at the bounded failure boundary.
CANDIDATE_UNKNOWN_MARKER = "candidate id is unknown for this set"
CANDIDATE_SET_UNKNOWN_MARKER = (
    "candidate set is unknown, expired, or not owned by this session"
)
ALREADY_PRESENTED_MARKER = "already presented"
NO_ACTIVE_SET_MARKER = "no active candidate set"
UNOFFERED_TOOL_MARKER = "tool not offered on this turn"

__all__ = (
    "ALREADY_PRESENTED_MARKER",
    "CANDIDATE_A",
    "CANDIDATE_B",
    "CANDIDATE_SET_UNKNOWN_MARKER",
    "CANDIDATE_UNKNOWN_MARKER",
    "CANDIDATE_V1",
    "CANDIDATE_V2",
    "CANDIDATE_WHAT_IF_A",
    "CHANGE_ROUTE_MESSAGE",
    "FIRST_OPTION_MESSAGE",
    "INVENTED_CANDIDATE_ID",
    "LEAK_MARKERS",
    "NO_ACTIVE_SET_MARKER",
    "PRETEND_CANDIDATE_MESSAGE",
    "RAW_ROUTE_ID",
    "RAW_SHAPE_ID",
    "RAW_TRIP_ID",
    "REPLAN_MESSAGE",
    "ROUTE_MESSAGE",
    "ROUTE_NAVIGATION_TOOL_PROFILE",
    "TRANSIT_QUESTION_TOOL_PROFILE",
    "UNOFFERED_TOOL_MARKER",
    "WHAT_IF_MESSAGE",
)
