# SmartRoute deterministic conversation matrix

This matrix documents the current model-led conversation contract. The tests
run the production loop, state-scoped public capability surface, real
registries, stores, evidence transitions, canonical presenters, and SSE event
path. Anthropic decisions and external provider boundaries are scripted unless
a test explicitly says otherwise. These tests prove runtime correctness, not
natural-language model quality.

## Public protocol

The initial model request offers only:

- `declare_goals`
- `discover_places`
- `check_transit`
- `prepare_route_options`
- `complete_turn`

After the model declares a `TurnContract`, `TurnEvidence` exposes only the
capabilities valid for current goal state. `present_places`, `present_transit`,
and `present_route` appear only when owned canonical evidence is ready.
Registered internal leaf tools remain unreachable from the model.

The backend does not classify rider language into an intent family. The Agent
declares outcome goals and chooses capabilities; the runtime validates every
call, records actual evidence, and prevents termination while declared work is
unresolved.

## Behavioral coverage

| Behavioral class | Primary test modules | Contract proved |
| --- | --- | --- |
| Goal declaration and completion | `test_turn_contract.py`, `test_completion_policy.py`, `test_turn_resolution.py`, `test_turn_terminal_contract.py` | Goals are bounded, dependency-safe, and cannot be abandoned. |
| Compound requests | `test_model_led_goal_loop.py`, `test_conversation_multi_intent_tool_sequencing.py` | Place, transit, and route goals remain pending until separately resolved. |
| Place discovery and references | `test_conversation_discovery_route.py`, `test_conversation_discovery_reference.py`, `test_discovery_references.py` | Only verified opaque place identities can be presented or routed. |
| Route preparation and presentation | `test_single_agent_route_tools.py`, `test_present_route_framing.py`, `test_route_identity_gate.py` | Candidate preparation is non-terminal; one canonical route card completes presentation. |
| Transit evidence and direction | `test_check_transit.py`, `test_transit_evidence.py`, `test_present_transit.py` | Status, arrivals, direction, incidents, and coverage remain scoped to typed evidence. |
| Clarification and continuation | `test_conversation_ambiguity_contradiction_temporal.py`, `test_pending_continuation.py`, `test_session_pending_continuations.py` | Clarification is used only for missing authoritative state and creates bounded continuation. |
| Active trip and what-if lifecycle | `test_conversation_what_if_lifecycle.py`, `test_conversation_candidate_lifecycle_safety.py`, `test_route_constraint_relaxation.py` | Temporary scenarios cannot overwrite the accepted trip before presentation. |
| No-good and provider failure paths | `test_conversation_no_good_aggregate.py`, `test_conversation_no_good_nonfatal_followup.py`, `test_conversation_failure_matrix.py` | Failed or unusable candidates never become active; recovery is truthful and bounded. |
| Tool-surface enforcement | `test_public_tool_surface.py`, `test_conversation_unoffered_tool_enforcement.py`, `test_strict_tool_schema.py` | Unknown, internal, and state-invalid tools do not execute or mutate state. |
| External-content safety | `test_conversation_external_content_security.py`, `test_conversation_reference_safety.py` | Web or model text cannot substitute destination identity or reach internal/provider boundaries unsafely. |
| Long-session memory | `test_conversation_long_state_retention.py`, `test_agent_context_projection.py`, `test_agent_chat_session_restore.py` | Authoritative session state survives bounded context projection and refresh. |
| Cancellation and races | `test_conversation_cancellation_recovery.py`, `test_conversation_presentation_race.py`, `test_plan_trip_prepare_cancellation.py` | Cancellation and concurrent presentation preserve one terminal event and one canonical owner. |
| Latency and deadlines | `test_turn_latency_guards.py`, `test_agent_loop.py::DeadlineTests` | Tool timeouts are clamped to the turn deadline and terminate once with the correct reason. |

## Required invariants

1. No provider-grounded fact is emitted through raw model prose.
2. A capability promise is not evidence; only successful execution advances a
   goal.
3. A presenter can use only an owned, current opaque evidence handle.
4. A turn cannot finish while a declared dependent goal remains unresolved.
5. `complete_turn` cannot replace place, transit, or route presentation.
6. An unavailable outcome requires a real attempted capability failure.
7. Unknown or unoffered tools never reach an executor or mutate session state.
8. Auto and Quick share the Agent and the same correctness contract; only budgets
   and response density differ.
9. New Trip clears transcript and trip state. A successful response refreshes
   the 30-minute conversation-session expiry.
10. Every stream ends with exactly one `done` event.

## Live-model boundary

The deterministic matrix cannot prove that the Agent understands every natural
paraphrase. Bounded live evaluations cover semantic interpretation, reference
resolution, compound-goal declaration, correct capability choice, and grounded
use of evidence. Live calls must never replace the deterministic matrix, and
must use an explicit request budget.
