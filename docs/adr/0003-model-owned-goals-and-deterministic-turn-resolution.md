---
status: accepted
---

# Agent-owned goals and deterministic turn resolution

SmartRoute has one Agent-led Turn Contract, refining ADR 0002. The Agent owns
rider semantics and, when possible, declares compact Outcome Goals in the same
response that makes the first capability call. The backend owns validation,
provider and itinerary facts, capability execution, Turn Evidence, and
canonical presenters' provider-grounded output. The state-valid stable
capabilities are `declare_goals`, `discover_places`, `check_transit`,
`prepare_route_options`, `present_places`, `present_transit`, `present_route`,
and `complete_turn`. Presenters are composable. Completion Policy blocks
incomplete turns and decides termination from Goal Resolutions and Turn
Evidence. `complete_turn` may not present provider-grounded facts, and no
nested planner or new model call is introduced.

Canonical presentation separates voice from truth. The Agent may supply
bounded Conversational Framing around a place, transit, or route presentation,
while the presenter remains the sole source of provider-grounded facts and
cards. Evidence capabilities may also carry an optional Activity Phrase, but
the runtime reveals it only after the operation actually starts. Raw pre-tool
prose remains discarded, preventing an intention sentence from masquerading
as execution. Simple turns may omit both activity and framing when they add no
value.

A Clarification closes the current Agent Turn and creates a 30-minute Pending
Continuation with at most three continuation attempts. Unrelated turns are
never hijacked, and New Trip clears the continuation. Semantic rider-language
regexes move from authoritative decisions to shadow disagreement telemetry and
then removal. Bounded syntax regexes remain. We reject a phrase-family regex
control plane, a separate intent-classifier or planner call, unrestricted
model prose, and a generic `present_result` because each weakens semantic
ownership, grounding, or deterministic completion.

Delegated destination choice and route choice remain model decisions over
server-owned evidence. "Find a good place and route me there" creates a
destination-selection goal followed by a dependent route goal. The Agent chooses
one verified place ID without presenting a shortlist, unless the rider asked to
see options. For routes, the Agent receives finalized factor comparisons and no
composite score, rank, winner label, or score-derived ordering. The backend's
existing ranking is private fallback behavior only when the Agent cannot return a
valid candidate selection. Every rider-visible time, transfer, route-chain,
walking, and disruption fact is rendered from the selected canonical itinerary
and its immutable evidence snapshot.
