---
status: superseded
superseded-by: 0003-model-owned-goals-and-deterministic-turn-resolution
---

# 0002. Model-led capability orchestration

This decision is retained as historical context and is superseded by ADR 0003.
The current implementation has one conversational Agent. The Agent owns
semantic interpretation, goal declaration, conversational reference
interpretation, public capability choice, and route choice. The backend owns
provider evidence, hard constraints, canonical itinerary facts, evidence
freshness, completion policy, and canonical presenters.

The public surface is exactly eight capabilities:
`declare_goals`, `discover_places`, `check_transit`,
`prepare_route_options`, `present_places`, `present_transit`,
`present_route`, and `complete_turn`. Raw model prose is discarded and cannot
terminate a grounded turn. The Agent receives unordered candidate factors without
scores, ranks, winner or other superlative labels, or score-derived ordering; a
deterministic route choice is private fallback behavior only when a valid model
selection is unavailable.

The Anthropic adapter sends public schemas with strict grammar compilation
disabled, while server-side schema, ownership, evidence, hard-constraint, and
completion validation remains strict. Session identity and safe references
retain for 30 minutes of sliding inactivity; volatile evidence is refreshed
only by a new relevant request. Broad incident intelligence is refreshed in
the background and read from a shared index by rider requests.

Rejected alternatives were phrase-driven semantic routing, a separate intent
or route-selection model, unrestricted model-authored facts, and a duplicate
agent architecture.
