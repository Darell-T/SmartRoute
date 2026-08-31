# Complete the lint cleanup

Use this plan to finish the SmartRoute lint cleanup without repeating the
failed whole-backend rewrite. Work in large subsystem batches, but checkpoint
and review each subsystem before another worker starts.

The worker implements. A separate reviewer follows
`docs/lint-cleanup-review-spec.md`. The worker never approves its own work.

## Trusted starting point

Commit `427fbc8` remains the historical reset point. Checkpoint `16390f3`
contains Batches 2 through 6F and the committed F0 frontend quality contract.
Batch F1 is Codex-approved on the tree from that checkpoint. Start Batch 7
only from the accepted F1 commit. The discarded whole-backend rewrite is not
part of this history.

The repository audit recorded on 2026-08-30 found a bounded backend closure,
a frontend measurement correction, and proven dead frontend code. It did not
justify another architecture rewrite. Start each remaining batch from the
latest accepted commit, record that exact SHA, and do not work from a dirty
checkout.

## Starting inventory (2026-08-27)

These counts were measured on 2026-08-27 with the checked-in policy update.
They are the starting snapshot, not the current backlog. After Batches 2
through 6, backend Ruff is 0. Regenerated complexity counts live in
`docs/lint-cleanup-handoff.md` and
`py scripts/report_backend_debt.py --max-existing 12`.

| Tool | Findings | Files | Notes |
|---|---:|---:|---|
| Ruff | 1,047 | 173 | 266 production, 781 tests |
| Ruff C901 | 66 | | McCabe ceiling 10 |
| Ruff PLR0912 | 33 | | Branch ceiling 12 |
| Ruff PLR0915 | 14 | | Statement ceiling 50 |
| complexipy | 276 | 106 | Legacy functions above cognitive 10 |
| Oxlint | 1,162 | 192 | 719 in the generator |
| ESLint | 193 | 76 | 185 complexity, 8 max-depth |
| Quality baseline | 353 | | Combined Python and TypeScript CC debt |

The largest Ruff rule is `PT009` with 614 findings. That is test assertion
style debt, not production complexity. `TRY003` is intentionally ignored. It
encouraged exception boilerplate without improving passenger behavior or
debuggability.

The fresh policy certification passed:

- frontend: 314 passed
- backend: 1,813 passed, 21 skipped, 444 subtests
- cyclomatic ratchet: 0 new, 0 worsened, 0 stale
- cognitive delta against `427fbc8`: 0 new or worsened

## Structural quality policy

Use four complementary signals. Do not substitute one for another.

1. Ruff C901 limits Python McCabe complexity to 10.
2. complexipy limits Python cognitive complexity to 10 for new functions and
   blocks regressions above 10 in existing functions.
3. Ruff PLR0912 limits Python branches to 12.
4. Ruff PLR0915 limits Python statements to 50.
5. Oxlint limits every authored frontend JavaScript and TypeScript function
   to cyclomatic complexity 12, including tests and development tools.
   `scripts/js_function_metrics.mjs --authored` is the matching inventory.
   ESLint applies the same ceiling only to application paths. It ignores
   `scripts/**`, `tools/**`, and `*.test.mjs`. 12 is a guardrail, not a
   target.

CRAP remains in `scripts/check_quality.py` as a coverage-aware regression
signal for baseline functions. CRAP has no absolute ceiling. A hard CRAP value
of 6 would silently restore the discarded complexity ceiling of 6 and reward
unnecessary tests.

Function length above 100 lines and file length above 500 lines are review
signals. They do not automatically fail a batch. A cohesive 500 to 800 line
module is healthy when C901 stays at 10. A 1000 line file is a sign of leftover
complexity, not a split trigger by itself. Do not peel a 100-200 line fragment
that must always be read with its parent.

### Required refactoring style

Prefer:

- guard clauses that remove nesting
- early returns that clarify the successful path
- lookup tables for genuine data-to-result mappings
- named policy stages with distinct inputs and outputs
- deletion of dead branches and duplicate behavior
- existing helpers and standard-library behavior

Do not:

- extract a helper only to silence C901
- turn an explicit policy branch into a cryptic expression
- move branches into several one-use wrappers
- add a service, factory, strategy, interface, or configuration layer without
  an existing need
- split a file only because a length signal fired
- add tests only to lower CRAP

A one-use helper is acceptable when it owns a real policy, lifecycle,
side-effect boundary, parsing boundary, or recovery boundary. Its name must
describe that responsibility. Call count alone does not prove or disprove its
value.

## Fixed-point and baseline rules

Every batch starts from a commit. Record it before editing:

```powershell
$fixedPoint = git rev-parse HEAD
git status --short
```

Compare cognitive complexity to that exact point throughout the batch:

```powershell
py scripts/check_quality.py --cognitive-only --quality-ref $fixedPoint
py scripts/check_quality.py --quality-ref $fixedPoint
```

The worker must not run `--update-baseline`. The reviewer may shrink
`quality/baseline.json` only after the implementation is accepted, fresh tests
pass, and the review proves each stale entry represents a real improvement or
deletion. Never increase a baseline value.

## Batch execution loop

Use this loop for every batch:

1. Record the fixed point and clean status.
2. Regenerate scoped and global inventories.
3. Save the exact owned production and test files.
4. Read the owned behavior, direct callers, provider boundaries, and current
   tests.
5. Write a short invariant list before editing.
6. Repair one cohesive cluster.
7. Run its focused behavioral tests.
8. Run scoped Ruff, complexipy delta, and the relevant frontend checks.
9. Continue with the next cluster inside the same subsystem.
10. Run the full affected subsystem suite once the batch is green.
11. Run the full backend or frontend suite once at batch end.
12. Run `scripts/check_quality.py` against the fixed point.
13. Inspect the complete diff for behavior drift and metric gaming.
14. Send the isolated diff to the reviewer.
15. Stop after approval. Create the next checkpoint only with user approval.

Do not run the full repository suite after every mechanical edit. Focused tests
belong after a cohesive behavior cluster. The full suite belongs at the batch
boundary.

## Test policy

Preserve behavior with the smallest useful test set.

Add a characterization test before refactoring only when the behavior is not
already protected and a plausible regression would matter. Good targets are:

- provider fallback and redaction
- cancellation and resource cleanup
- cache and session ownership
- route identity and canonical itinerary contracts
- state-machine terminal outcomes
- parser rejection and boundary normalization
- generated artifact determinism

Do not add tests for:

- a private helper's exact internal call order when no contract depends on it
- impossible inputs rejected by an earlier typed or validated boundary
- Python syntax or a linter rule
- every branch of a trivial lookup table
- an outrageously rare state that cannot enter through a production boundary
- a refactor implementation detail that may change again

Do not delete a test merely to improve coverage or CRAP. Delete a test only
when it duplicates stronger evidence or tests a removed feature. Follow
`docs/test-suite-rationalization.md` and record the exact reason and count
change.

## Change-size guardrails

Large subsystem ownership does not authorize an uncontrolled rewrite. Stop the
current worker at the last green cohesive cluster when any review trigger is
met:

- total diff churn exceeds 2,500 changed lines
- production code grows by more than 500 net lines
- the patch adds 25 more production functions than it removes
- a new module boundary is required
- a public contract, retry policy, timeout, cache policy, or provider boundary
  must change
- the worker cannot explain a helper without referring to a lint rule
- focused behavior becomes harder to test than before

These are review triggers, not automatic rejection. The reviewer decides
whether the growth is justified or whether the remaining cluster should become
the next checkpoint.

## Non-negotiable product guardrails

- Preserve one backend-owned canonical itinerary.
- Preserve candidate identity, evidence association, and deterministic
  fallback selection.
- Keep capability choice model-led and state-scoped.
- Keep the destination decision with the user.
- Never invent live data or turn missing evidence into confirmed safety.
- Never expose prompts, reasoning, raw model output, provider payloads,
  credentials, or unnecessary coordinates.
- Keep queue information conversational and in PromptKit sources.
- Keep queue information off the map and route card.
- Preserve live-over-historical Damn Lines precedence.
- Never calculate an unprovided queue wait.
- Preserve provider timeout, retry, cache, and failure semantics.
- Preserve official MTA colors, route identity, station relationships, and
  deterministic generated artifacts.

## Remaining batch order

The old 20-batch plan was too granular. Batches 2 through 6E and F0 are
complete. The backend test-assurance pass completes the missing public
evidence test and sets the branch-aware coverage, mutation, and CI contracts.
Batch 6F now removes only proven backend leftovers. Batch F1 corrects the
frontend coverage gate and subtracts proven dead frontend code. Do not start
Batch 7 until both closures are reviewed and committed.

Measure backend debt with `scripts/report_backend_debt.py --max-existing 12`.
Keep the official Python ceilings in `pyproject.toml` at 10. Measure frontend
debt with `scripts/report_frontend_debt.py`. The frontend cyclomatic ceiling is
12. New Python functions must stay at 10 or lower. A coherent frontend
function at 8 or 11 may stay as it is.

The 91 backend functions reported at 11 or 12 are the maximum of the Radon
cyclomatic and complexipy cognitive measurements. They are not 91 unresolved
Ruff C901 violations. Ruff is clean at its maximum of 10, and the accepted
legacy cognitive ratchet has no new or worsened functions. Do not launch a
metric-only pass to turn all 91 into 10. Revisit one only when a behavior,
coverage, or navigation problem gives the refactor an independent reason.

After the backend test-assurance patch is reviewed and committed, protect its
measured floor: 89.14% combined line and branch coverage, 91.77% statement
coverage, 81.64% branch coverage, 50 zero-covered authored functions, and no
function with CRAP above 30. Keep the CI floor at 85%. Follow
`docs/backend-test-quality.md` when regenerating or interpreting these counts.
Do not turn the 17 remaining provider, startup, shutdown, logging, persistence,
and live orchestration paths into mocked unit tests merely to raise coverage.
Protect those paths with boundary or deployment evidence when their behavior
changes.

| Batch | Subsystem | Why it is cohesive |
|---:|---|---|
| 2 | Realtime foundation and providers | Runtime, transport, MTA, and incident data lifecycle |
| 3 | Canonical trip planning | One itinerary and selection domain |
| 4 | Agent capability tools | One model-visible capability boundary |
| 5 | Agent orchestration and state | Model, session, loop, and turn lifecycle |
| 6 | Backend test style remainder | Test-only Ruff debt after production settles |
| 6A | Realtime, MTA, incidents, and routers | Live data, GTFS, alerts, and HTTP entry points |
| 6B | Canonical trips | One itinerary and incident-matching domain |
| 6C | Agent place, route, and shared tools | Place resolution and route preparation |
| 6D | Agent transit tools | Arrivals, evidence, and area conditions |
| 6E | Agent model, session, and turn | Prompt, stream, session, and turn lifecycle |
| 6F | Backend closure | Delete only symbols whose production call-site search stays empty |
| F0 | Frontend quality foundation | Complexity ceiling, coverage denominator, and debt report, committed at `9b4327d` |
| F1 | Frontend measurement and subtraction | Correct coverage semantics and remove proven dead production code |
| 7 | Application and library boundaries | `frontend/app/**` and `frontend/lib/**` |
| 8 | Components, interaction, and maps | `frontend/components/**` and `frontend/tests/release/**` |
| 9 | Transit artifact generation | `frontend/scripts/**` |

## Batch 0: route intelligence and quality stabilization

Completed and included in `427fbc8`. Do not repeat it.

Preserve the schema version 2 route-intelligence boundary, removed live-shadow
behavior, deterministic replays, and existing quality-gate coverage fixes.

## Batch 1: deterministic and release tooling

Completed and included in `427fbc8`. Do not repeat it.

The owned release and deterministic tooling Ruff boundary is zero. Preserve
release evidence hashes, policy expiration, provider-fault seeds, CLI safety,
and the explicit opt-in requirement for live checks.

## Batch 2: realtime foundation and providers

Owned production paths:

- `backend/app/main.py`
- `backend/app/observability.py`
- `backend/app/runtime.py`
- `backend/app/routers/agent_chat.py`
- `backend/app/routers/trips.py`
- `backend/app/routers/live_feed/**`
- `backend/app/services/admission.py`
- `backend/app/services/cache.py`
- `backend/app/services/directions.py`
- `backend/app/services/evidence.py`
- `backend/app/services/geography.py`
- `backend/app/services/live_feed/**`
- `backend/app/services/incidents/**`
- `backend/app/services/mta/**`

Work in four clusters: runtime and admission, live-feed transport, incidents,
then MTA adapters and indexes. The cluster order follows the runtime data flow.

Preserve startup validation, readiness, backpressure, WebSocket close codes,
disconnect cleanup, cache fail-open behavior, provider provenance, incident
identity, refresh atomicity, GTFS semantics, BusTime behavior, and all timeout
and retry counts.

Run direct readiness, admission, live-feed, incident, NY511, MTA, GTFS,
scheduled-arrival, and stop-pattern tests after their cluster. Finish with all
owned tests, the full backend suite, scoped Ruff, complexipy delta, and fresh
quality.

## Batch 3: canonical trip planning

Owned production path:

- `backend/app/services/trips/**`

Work in four clusters: preparation, incident association, crowd evidence, then
itinerary, enrichment, scoring, and selection.

Preserve candidate identity, selection order, scoring semantics, merged walks,
transfers, waypoint and dwell provenance, multi-stop behavior, constraint
relaxation, and fallback selection. Do not change a response contract to make
lint easier.

Run tests matching `test_trip*`, `test_trips*`, `test_route*`,
`test_itinerary*`, `test_plan_trip*`, plus transfer and crowd tests. Finish
with the full backend suite and quality gates.

## Batch 4: agent capability tools

Owned production path:

- `backend/app/services/agent/tools/**`

Work in four clusters: shared tool boundary, places, transit, then route tools.

Preserve strict tool schemas, presented-entity references, evidence binding,
place pagination, route preparation ownership, and all Damn Lines invariants.
Do not add a parallel provider client or duplicate a canonical contract.

Run strict-schema, public-surface, discovery, Damn Lines, arrivals, transit
evidence, route preparation, route projection, and presentation tests. Finish
with capability reliability tests, the full backend suite, and quality gates.

## Batch 5: agent orchestration and state

Owned production paths:

- `backend/app/services/agent/model/**`
- root modules under `backend/app/services/agent/`
- `backend/app/services/agent/turn/**`

Exclude `tools/**`, which Batch 4 owns.

Work in four clusters: model request and stream boundaries, persistent stores
and sessions, candidate and reference state, then loop and turn lifecycle.

Preserve token budgets, overload behavior, cancellation, session leases,
transcript persistence, candidate lifecycle, one terminal outcome, ledger
ordering, continuation, tool-round limits, and passenger-output redaction.

Run model, stream, session, restore, lease, output, candidate, loop, turn,
continuation, and cleanup suites. Run order-sensitive files in both orders.
Finish with the full backend suite and quality gates.

## Batch 6: backend test style remainder

Own every remaining Ruff-reported file under `backend/tests/**`. Save the exact
manifest before editing. Do not edit production code in this batch.

Most debt is `PT009`. Convert assertions without changing operand order,
failure meaning, async sequencing, mock arguments, call counts, or fixture
identity. Keep protocol parameters that are required even when a local test
does not read them.

Do not create new test helper layers merely to remove repeated assertions. Do
not delete tests unless the rationalization guide proves exact duplicate
evidence. Run each changed test file, then the owned list in forward and reverse
order, then the full backend suite. Global Ruff must be zero at batch end.

Batch 6 is committed at `c058199`. That commit is the immutable fixed point
for Batches 6A through 6E.

## Backend complexity program

Record the fixed point before editing:

```powershell
$fixedPoint = git rev-parse HEAD
py scripts/report_backend_debt.py --self-test
py scripts/report_backend_debt.py --max-existing 12 --output .audit/backend-debt.json
```

Targets for 6A through 6E:

- Every existing production function has cyclomatic complexity of 12 or lower.
- Every existing production function has cognitive complexity of 12 or lower.
- Every new function stays at 10 or lower in both measurements.
- Functions that remain at 11 or 12 must not exceed the survivor count in
  `docs/lint-cleanup-handoff.md` at `c058199`. Record each survivor in the
  handoff.
- Resolve the CRAP scores above 30 listed in that handoff through useful
  tests, clearer code, or deletion.
- Keep branch-aware backend coverage at or above the handoff percentage.
- Do not change public REST, WebSocket, model-tool, itinerary, or event
  contracts.

A helper may be introduced only when it owns a named policy, parsing step,
aggregation, lifecycle, or side effect. Do not create a one-call helper only
to lower a score. Keep a function at 11 or 12 when that is clearer than
another layer.

After each of 6A through 6E, run the owned tests, scoped Ruff, the debt
report, complexipy against `c058199`, the full backend suite with branch
coverage, and `py scripts/check_quality.py --quality-ref c058199`. Leave
`quality/baseline.json` for the reviewer.

## Batch 6A: realtime, MTA, incidents, and routers

Own every production function under `backend/app` except `services/trips/**`
and `services/agent/**`. Regenerated above-12 and CRAP counts are in
`docs/lint-cleanup-handoff.md`.

Delete `GTFSStaticData.get_unique_routes_for_stops`. It has no production or
test caller.

Refactor `_build_subway_vehicle_positions` into named parsing, selection, and
diagnostic phases. Do not change vehicle identity, route filtering,
staleness, stop-only handling, colors, or debug output.

Simplify alert stop-name enrichment and GTFS query retry. Preserve failure
behavior, including stale-connection retry, statement timeout without retry,
and connection return.

Cover these cases through the cheapest public path before the structural
move:

- Vehicle route filtering, missing and zero coordinates, duplicate IDs,
  stale timestamps, stop-only vehicles, and diagnostics
- Alert stop-name lookup, directional child-stop fallback, duplicate names,
  missing GTFS, and lookup failure
- BusTime partial stop failures, expired arrivals, ordering, and empty-stop
  results
- Subway-stop endpoint readiness, cache reuse, coordinate omission, route
  colors, and GeoJSON output
- Database stale-connection retry, second failure, statement timeout without
  retry, and connection return
- Stable alert IDs and WebSocket change detection

Preserve startup validation, readiness, backpressure, WebSocket close codes,
cache fail-open behavior, provider provenance, incident identity, GTFS
semantics, BusTime behavior, and all timeout and retry counts.

Reviewer final (2026-08-29). Fixed point `c058199`. Batch 6A has
`above_12=0`, `at_11_or_12=13`, and `crap_above_30=0`. Branch-aware coverage
is 88.4%. Global Ruff is 0. Cognitive new or worsened is 0. The reviewer
removed exactly 34 proven-stale entries, which reduced `quality/baseline.json`
from 329 to 295 entries. The final quality command exits 0 with
`approval_eligible: true`. Batch 6B was not started.

## Batch 6B: canonical trips

Own `backend/app/services/trips/**`. At `c058199` that is 62 functions
above 12.

Worker completion is uncommitted on `damn-lines-integration` against
checkpoint `140495a`. Quality comparison stays `c058199`. See
`docs/lint-cleanup-handoff.md`. Do not start Batch 6C until Codex accepts
Batch 6B.

Prioritize `match_cached_incidents`, `_prefer`, and
`build_chained_itinerary`. Separate incident-to-stop matching from impact
classification. Separate official-source precedence from evidence merging.
Separate chained-segment construction from total calculation.

Preserve canonical itinerary arithmetic, selection order, dwell provenance,
transfers, merged walks, incident identity, route matching, crowd evidence,
constraints, and fallback behavior.

Reviewer final (2026-08-29). Scope checkpoint `140495a`. Batch 6B has
`above_12=0`, `at_11_or_12=24`, and `crap_above_30=0`. Branch-aware coverage
is 88.6%. The reviewer removed 59 proven-stale entries and reduced
`quality/baseline.json` from 295 to 236 entries. The update also lowered the
existing ceilings for `lookup_events` and `prepare_structural_candidates`.
The final quality command exits 0 with `approval_eligible: true`. Batch 6C was
not started.

## Batch 6C: agent place, route, and shared tools

Own `backend/app/services/agent/tools/**` except `tools/transit/**`. At
`c058199` that is 40 functions above 12.

Remove the hidden `discovery_set_id` input from `place_reference.execute`.
The strict `get_place_details` schema cannot supply it, and no production
caller uses it. Keep presented-place lookup and active-set fallback as one
named internal policy.

Preserve strict schemas, session ownership, opaque identities, evidence
binding, route preparation ownership, candidate identity, and passenger
redaction.

The worker completed against checkpoint `c8a0381`. Quality comparison stays
`c058199`. Codex review and the growth correction are complete. See
`docs/lint-cleanup-handoff.md`. Batch 6D did not start during Batch 6C review.

Reviewer complete (2026-08-29). Scope checkpoint `c8a0381`. Codex accepted
the frozen `RoutePreparationAdmission` returned by
`_admit_route_preparation`, and `execute` consumes named attributes. Batch
6C has `above_12=0`, `at_11_or_12=19`, and `crap_above_30=0`. Branch-aware
coverage is 88.7%. Codex removed exactly 38 stale 6C IDs and reduced
`quality/baseline.json` from 236 entries to 198. The update also lowered three
surviving ceilings without widening any entry. The reviewer quality command
exits 0 with `approval_eligible: true`, `tests_ran: true`, new 0, worsened 0,
and cognitive new or worsened 0. After the first local commit, Codex removed
15 unearned helper functions. Final Batch 6C production growth is 1,919
insertions and 1,141 deletions, net +778. Function inventory is 417. The final
full gate has 1,895 backend passes, 21 skips, 444 subtests, and 314 frontend
passes. Batch 6D was not started.

## Batch 6D: agent transit tools

Own `backend/app/services/agent/tools/transit/**`. At `c058199` that is 35
functions above 12.

Prioritize `lookup_arrivals_bus.execute`, direction resolution, transit
evidence projection, accessibility binding, snapshot construction, and area
condition checks. Separate stop resolution, provider access, filtering,
grouping, and response construction when those are independent
responsibilities.

Preserve accepted-itinerary binding, route and direction matching, live
versus scheduled provenance, outage handling, provider timeouts, and
graceful unavailable results.

Reviewer complete (2026-08-30). Scope checkpoint `676ff13`. Quality
comparison stays `c058199`. Batch 6D has 321 functions, `above_12=0`,
`at_11_or_12=17`, and one uncovered CRAP signal. Codex repaired
multi-candidate status evidence aggregation and corrected the alert-provider
conversation seam. Final production growth is 1,559 insertions and 993
deletions, net +566. Codex removed 14 worker helper functions. The reviewer
removed 30 stale baseline entries and lowered four surviving ceilings.
`quality/baseline.json` now has 168 entries. The full gate exits 0 with 1,896
backend passes, 21 skips, 444 subtests, 314 frontend passes, and no new or
worsened violations. Batch 6E and Batch 7 were not started.

## Batch 6E: agent model, session, state, and turn lifecycle

Own remaining modules under `backend/app/services/agent/` except
`tools/**`. At `c058199` that is 53 functions above 12.

Prioritize `presented_entity_registry.resolve`, `build_turn_context`,
candidate persistence, public tool selection, model streaming, session
restoration, and turn completion. Split selector policies and independent
context summaries while keeping the public interface small.

Preserve token budgets, cancellation, overload handling, leases, session
persistence, tool ordering, continuation behavior, one terminal outcome, and
rider-output sanitization.

After Batch 6E, rerun `scripts/report_backend_debt.py --max-existing 12`
across all backend production code. No function may remain above 12. Then
run frontend lint and Oxlint to record the inherited frontend backlog. Run
typecheck, unit tests, release checks, and `scripts/check_quality.py` once
against the final backend commit. Do not start Batch 7 until those checks pass
and the quality ratchet reports no new or worsened frontend findings. Batches
7 through 9 own the inherited raw frontend findings.

Reviewer complete 2026-08-30. Scope checkpoint `2298e32`. Quality comparison
`c058199`. Batch 6E has 547 functions, `above_12=0`, `at_11_or_12=18`,
`crap_above_30=0`, and coverage 88.8%. Global production has `above_12=0`.
Codex removed eight dead symbols, three trivial helpers, and a temporary audit
script. Final production growth is 2,037 insertions and 1,308 deletions, net
+729. The reviewer removed 43 stale entries from `quality/baseline.json`,
which now has 125 entries. Final quality exits 0 with
`approval_eligible: true`; backend has 1,899 passes, 21 skips, and 444
subtests; frontend unit has 314 passes; release CI has 14 passes and 4 skips.
Batch 7 was not started.

## Batch 6F: backend closure

This is a deletion-only batch, not another backend complexity or coverage
program. Start only from a clean accepted commit that includes the backend
test-assurance patch. Record that commit as the immutable fixed point before
editing. Stop if the worktree is dirty or the patch is absent. Do not touch
`quality/baseline.json`, frontend production, provider behavior, canonical
itinerary arithmetic, or any accepted 11-or-12 survivor merely to lower a
metric.

Own only these files and their directly named tests:

- `backend/app/services/agent/tools/route/route_projection.py`
- `backend/app/services/agent/presented_entity_registry.py`
- `backend/app/services/agent/session.py`
- `backend/app/services/agent/trip_state.py`
- `backend/app/services/geography.py`
- `backend/tests/test_route_itinerary_contract.py`
- `backend/app/services/trips/text.py`
- `backend/app/services/agent/tools/places/geography.py`
- `backend/app/services/mta/static_gtfs/store.py`
- `backend/app/services/trips/itinerary.py`
- `backend/app/services/trips/route_incidents/context.py`
- `backend/app/services/agent/tools/transit/evidence_projection.py`
- `backend/tests/test_transit_evidence.py`
- tests that directly own one of the listed symbols

Complete the batch in this order:

1. Record `git status --short` and `git rev-parse HEAD`. Confirm that the fixed
   point includes `backend/cosmic-ray.toml`, `docs/backend-test-quality.md`, and
   the branch-aware backend CI gate. Stop without editing if it does not.
2. Repeat repository-wide searches for every candidate symbol. Search
   production, tests, imports, exports, scripts, and current documentation.
   Record the results before deleting anything. If an intervening production
   or test caller exists, skip that symbol and report the caller. Do not refactor
   the caller to make the deletion possible. A reference contained only inside
   the same dead candidate subtree is not an independent caller. Treat a
   `__main__` demo as a contract only when a current command or document invokes
   it.
3. Delete `reconcile_first_boarding_timing` and its private subtree
   `_catchable_offset_seconds`, `_first_transit_index`,
   `_itinerary_component_totals`, `_leg_seconds`,
   `_stamp_reconciled_clocks`, `_leg_component_seconds`, and `_retime_legs`.
   No production caller reaches this subtree. Delete only the two tests that
   exist solely for that unreachable API. Do not replace it with another
   timing adapter.
4. Delete the following candidates only when the repeated search stays empty:
   `_expand_abbreviations`, `_TTS_ABBREVIATIONS`, `is_nyc_locality`,
   `GTFSStaticData.get_stop_names`, `presented_entity_registry.clear`,
   `PendingContinuation.recovery_options`, `trip_state.set_origin`,
   `trip_state.clear_route_selection`, `services.geography.geocode_address`,
   `services.geography.walking_time_minutes`,
   `route_incidents.context.stop_reference`,
   `CandidateStopContext.directions`, `CandidateStopContext.stop_reference`,
   and `CandidateStopContext.as_dict`. Remove imports, exports, and private data
   made unused by an accepted deletion. Do not delete a class, field, or helper
   that a surviving path still needs.
5. Remove the stale `Later wiring` paragraph from
   `chain_canonical_itineraries`. The chained itinerary is already wired.
   Keep the input shape, totals, clocks, and no-frontend-dwell explanation.
6. Preserve the existing public test through
   `evidence_projection.operation_facts("area_conditions", row)`. It already
   protects the incident and event bounds, ignored non-dict rows, passenger
   fields, resolved-area fallback, and evidence statuses. Do not add a second
   test for the same behavior. Do not add tests for dead code or equivalent
   mutants.
7. Run the focused tests after each deletion cluster. Run the full backend
   suite with branch coverage, Ruff, the backend debt report, the cognitive
   delta, and full quality against the fixed point at the boundary.

Acceptance requires no product behavior change, no new production module, no
replacement helper layer, negative net backend production growth, no new or
worsened complexity, `above_12=0`, and `crap_above_30=0`. Combined backend
coverage may not fall below 89.14%. The count of zero-covered authored
functions must fall by exactly the number of zero-covered functions deleted.
Keep the CI floor at 85%. Do not rerun the Cosmic Ray canary unless
`backend/app/services/evidence.py` or
`backend/tests/test_evidence_freshness.py` changes. Those files are outside
this batch. The worker leaves the tree uncommitted for Codex review. Codex
alone decides whether any stale baseline entry should be removed.

Reviewer final (2026-08-31). Fixed point `ac96d12`. Batch 6F deleted 22
production functions and 329 net backend production lines. All 13 zero-covered
candidate surfaces had no independent caller and were deleted. The unreachable
first-boarding timing subtree and its two private-only tests were also deleted.
The regenerated debt report contains 2,415 functions, 37 with zero coverage,
none above 12, and none with CRAP above 30. Combined coverage is 89.34%.
The full backend suite passed with 1,915 tests, 21 skips, and 446 subtests.
Full quality against the fixed point exits 0 with `approval_eligible: true`.
`quality/baseline.json`, the 85% CI floor, Cosmic Ray configuration, frontend,
and passenger behavior are unchanged.

## Batch F0: frontend quality foundation

F0 is committed at `9b4327d`. It established the frontend complexity ceiling,
scope inventory, one unit runner, and c8 source mapping. It did not refactor
application, component, map, or artifact-generation behavior. The F1 audit
correction below supersedes only F0's function-coverage gate. It does not
change the F0 complexity or source-scope contracts.

The immutable checkpoint is `ae27211fa9ba`. Compare quality to that commit.

### Complexity contract

- Python cyclomatic maximum remains 10 in `pyproject.toml` and
  `scripts/check_quality.py`.
- Frontend JavaScript and TypeScript cyclomatic maximum is 12. Oxlint and
  `scripts/js_function_metrics.mjs --authored` are the exhaustive owners for
  every authored frontend JS/TS function, including tests and development
  tools. ESLint remains the application-focused linter and ignores
  `scripts/**`, `tools/**`, and `*.test.mjs`. Generated, vendored
  (`tools/oxlint/**`), declaration-only, dependency, and build-output paths
  are lifecycle exclusions.
- `scripts/check_quality.py` uses the same frontend ceiling of 12. Unchanged
  over-12 TypeScript functions that were newly inventoried against
  `--quality-ref` are pre-existing scope debt, not new debt, and are not
  added to `quality/baseline.json`.
- Do not replace the Python maximum with 12.
- Functions from 7 through 12 become compliant. Do not extract helpers only
  to turn 13 into 12.

### Coverage scope

Include production TypeScript and JavaScript under `frontend/app/**`,
`frontend/lib/**`, `frontend/components/**`, and `frontend/scripts/**`.

Exclude tests, specs, check scripts, declarations, configuration, vendored
code, `node_modules/**`, `.next/**`, `public/**`, coverage output, and
release evidence. Every exclusion is in `scripts/frontend_quality_scope.json`
with a lifecycle reason.

An included production file that was never executed counts as zero. Do not
omit it. Do not collapse unresolved function mappings into covered or
uncovered.

### Coverage execution

`frontend/tools/run-unit-tests.mjs` is the single unit-test contract.
`npm run test:unit` and `npm run test:coverage` use it. Node 23 native V8
coverage records tsx compiled scripts, omits unexecuted files, and does not
emit stable source-mapped TypeScript totals. `c8` is the coverage-specific
dependency that remaps to `.ts` and `.tsx` and includes missing files as
zero.

Playwright release tests do not contribute source-mapped `.ts` and `.tsx`
coverage. Browser-only React and MapLibre files stay unexecuted until Batch 8
can map bundled source. Do not add a second component-test architecture in
F0.

### Targets for Batches 7 through 9

The complete production scope after Batch 9 targets at least 95% line, branch,
and standard c8 source-mapped function coverage. The target is not permission
to rename anonymous callbacks, export private helpers, add source-text tests,
or exercise impossible typed states.

F0's authored-function mapper matches raw V8 records primarily by file and
function name. At the F0 checkpoint, 688 functions are unresolved: 618 are
anonymous and 70 are named functions missing from raw V8. Counting all 688 as
uncovered makes source spelling affect the gate and can reward production
refactors that do not improve test evidence. Keep the authored inventory for
complexity and mapping diagnostics. Do not use `measured / all inventoried`
as the approval percentage.

Use the source-mapped c8 function total as the function gate only after every
owned production file has executed. Before that point, function and branch
gate status is `unresolved`, because c8 cannot discover a complete branch or
function denominator for a file that never loaded. Line coverage continues to
include missing production files as zero.

Each owning batch must:

1. Target at least 95% line, exact c8 branch, and exact c8 function coverage
   for its owned production files. The exact branch and function checks become
   eligible only when owned unexecuted production files equal zero.
2. Leave no owned production file completely unexecuted. This is a hard gate.
3. Avoid decreasing any whole-frontend coverage metric.
4. Leave no owned function above complexity 12.
5. Test important failure, fallback, cancellation, stale-data, and
   malformed-input decisions through public behavior.
6. Demonstrate that representative new tests fail when the protected decision
   is temporarily inverted or removed.
7. Avoid direct private-helper tests unless the helper is an earned module
   interface.
8. Do not add source-text, style-text, broad snapshot, or mock-call-count tests
   as substitutes for behavior.
9. Prefer real values and small fakes over mocks. Keep tests DAMP and readable.
10. If an owned scope remains below 95%, the worker must stop for reviewer
    disposition with the exact uncovered files and decisions. Only the
    reviewer may accept a documented exception for framework glue,
    unreachable generated branches, or impossible validated states. A worker
    may not declare the batch complete by lowering the target or adding
    coverage-ignore comments.

Aim for 100% branch coverage in pure critical modules where the remaining
branches represent real behavior: state reducers, event validators, proxy and
stream protocol handling, canonical itinerary adapters, route selection and
planning policies, and deterministic transit artifact transformations. Do not
force 100% by testing impossible TypeScript states, framework internals,
decorative rendering, or implementation details. Do not introduce
coverage-ignore comments in worker code.

### Commands

```powershell
py scripts/check_quality.py --self-test
py scripts/report_frontend_debt.py --self-test
Set-Location frontend
npm run typecheck
npm run typecheck:scripts
npm run test:unit
npm run test:coverage
npm run verify:transit-artifacts
npm run lint
npm run lint:oxlint
Set-Location ..
py scripts/report_frontend_debt.py --quality-ref ae27211fa9ba --output .audit/frontend-debt.json
py scripts/check_quality.py --quality-ref ae27211fa9ba
```

Frontend ESLint and Oxlint may remain red solely for the accepted inventory
above complexity 12. F0 must not refactor those production functions.

## Batch F1: frontend measurement correction and subtraction

F1 is Codex-approved on the tree from `16390f3`. Batch 7 was not started.

F1 makes the denominator truthful before any worker writes coverage tests or
refactors a high-complexity frontend function. Start from the accepted Batch
6F commit. Record the actual fixed point. The worker must not change
`quality/baseline.json`, add a dependency, add a test framework, begin Batch
7, or redesign a visible surface.

### F1A: correct the coverage gate

Own only the F0 quality scope, reporter, quality runner, their self-tests, and
the two lint-cleanup documents. Preserve the raw authored mapping as a
diagnostic for per-function CRAP and unresolved records. Change the aggregate
approval semantics as follows:

1. Report standard source-mapped c8 function coverage as the aggregate
   function metric.
2. Report function gate status as `unresolved` while any owned production file
   is unexecuted. Do the same for exact branch coverage.
3. Keep authored measured, uncovered, anonymous-unresolved, and
   named-unresolved counts visible. Do not convert unresolved mappings to
   covered or uncovered.
4. Keep line coverage's missing-file zero behavior.
5. Add self-tests proving that an unexecuted file blocks exact branch and
   function status, all-executed c8 records provide the exact totals, and
   anonymous authored callbacks cannot lower or raise the c8 percentage by
   being renamed.
6. Remove the old `confirmed_function` approval wording from reports and
   documents. Do not delete the authored inventory itself.

### F1B: delete proven dead frontend production

Repeat exact import and symbol searches first. At `9b4327d`, the following
files have no application or build-pipeline caller:

- `frontend/components/smart-route/chat/chat-top-bar.tsx`
- `frontend/components/smart-route/chat/tab-toggle.tsx`
- `frontend/components/smart-route/chat/near-you-row.tsx`
- `frontend/components/smart-route/left-rail/demo-data.ts`
- `frontend/components/smart-route/left-rail/incident-format.ts`
- `frontend/scripts/build/line-geometry-cleanup.ts`

Delete them only if the repeated fixed-point search agrees. These complete
files contain 892 production lines before CSS and in-file dead exports are
counted. Then make these exact follow-up removals:

1. Delete `line-geometry-cleanup.test.ts`. It protects only the abandoned
   helper, and no active generator stage imports that helper.
2. In `near-you.ts`, keep `buildHomeNearbyModel`. Delete the test-only
   `deriveNearbyRouteIds`, `stationNameForRoute`, and
   `buildArrivalsPayloadForRoute` exports and their dead tests. Replace the
   538-line `DEMO_RAIL_DATA` dependency in `near-you.test.ts` with one small,
   local, typed `LeftRailLiveData` fixture containing only values the public
   model reads. Do not create another production fixture file.
3. Delete only the CSS selectors owned by the removed top bar, Near You row,
   and floating tab toggle. Keep `.sr-tab-shell*`, `.sr-chat-theme-toggle*`,
   and every selector still used by the current sidebar or mobile navigation.
   Remove comments that describe the deleted floating toggle or top bar.
4. Keep `artifact-fingerprint.ts`. It has no import caller because it is a
   standalone CLI, and the build README classifies it as a utility. Batch 9
   may document its exact command and output lifecycle. It may delete it only
   after the reviewer confirms the manual workflow is no longer used.
5. Do not delete `frontend/app/manifest.ts`, any `*.check.mjs` transit
   validation entry point, or `scripts/release/build-browser-evidence.ts`.
   Static import graphs report these as roots, but Next, package commands, or
   the release guide invokes them.

F1 must be net-negative production code and must not add a replacement
component, compatibility export, wrapper, or fixture module. Verify the
current sidebar and mobile navigation still switch views and toggle the
theme. Run typecheck, script typecheck, unit, coverage, release CI, both
linters, transit artifact verification, the corrected reporter self-tests,
and full quality against the fixed point. Generated GeoJSON and manifest
hashes must not change. Leave the tree uncommitted for Codex review.

### F1 reviewer result

Codex repeated the dead-code searches, removed two stale comment banners that
still described the deleted top bar and Near You row, and verified desktop and
mobile theme switching against the running application. The final production
diff is +5 / -1222 lines, net -1217, with no new production file or dependency.
The reviewer removed exactly the two proven-stale TypeScript entries from
`quality/baseline.json`, reducing it from 113 to 111 without changing another
entry. Full quality against `16390f3` exits 0 with `approval_eligible: true`:
551 frontend tests and 1,915 backend tests passed, with 21 backend skips and
446 subtests. New, worsened, stale, cognitive-delta, Ruff C901, and Ruff
structural counts are all 0. Batch 7 must use the accepted F1 commit as its
immutable quality reference.

## Batch 7: application and library boundaries

Owned paths:

- `frontend/app/**`
- `frontend/lib/**`
- `frontend/types/**` only for the canonical response types used by those
  boundaries

F0 complexity-above-12 inventory: 10 functions. Regenerate the count after
F1. Start from the accepted F1 commit and use it as the immutable quality
reference.

Start with `agent-chat-state.ts` (47), `agent-chat-controller.ts` (35),
`mapbox-search.ts` (18), backend proxy and stream proxy functions (17),
`app/page.tsx` (16), and agent event validator, session, stream, and
proxy-core functions from 13 through 14.

Complete these clusters in order. Do not combine them into one rewrite.

### Batch 7A: one canonical route boundary

The REST trip client currently returns `res.json()` as `TripResponse` without
runtime validation. The agent SSE validator already contains the canonical
itinerary Zod schema, while `types/api.ts` imports that type indirectly from
the stream module and leaves canonical candidate fields optional. Repair this
boundary before component work:

1. Verify the backend route-candidate and canonical-itinerary response fields
   against backend response models and contract tests. Require only fields the
   backend actually guarantees. A legitimately nullable clock remains
   nullable and displays as unavailable later.
2. Move the canonical itinerary Zod policy out of the event-only validator
   into one earned shared `frontend/lib` schema module. Reuse it from both the
   SSE route-card validator and the REST trip-response validator. Do not copy a
   second itinerary schema and do not make REST import the entire event
   validator.
3. Import canonical itinerary types directly from their contract owner, not
   through `agent-chat-stream.ts`.
4. Define a narrowed canonical route-candidate type for data that passed the
   boundary. It must make the guaranteed itinerary id, duration, transfer
   count, legs, and other verified fields required. Keep a separate raw type
   only where untrusted JSON first enters.
5. Parse `planTrip` response JSON as `unknown`. Reject malformed or
   noncanonical candidates through the existing API error path. Do not cast,
   use `any`, or silently filter every invalid candidate into an apparently
   successful empty plan.
6. Make `normalizeTripCandidates` and agent route-card selection return the
   narrowed type after their checks. Downstream map and rail state should not
   repeatedly ask whether required canonical facts exist.
7. Add boundary tests for one valid REST response, a missing canonical
   itinerary, malformed totals or transfer count, and an SSE card using the
   same itinerary schema. Invert one required check to prove the malformed
   response test goes red.

Stop this cluster if a UI-required value is not in the backend canonical
contract. Record the exact missing field for a small backend contract change.
Do not invent it from route steps or passenger-facing prose.

### Batch 7B: agent state transitions

Refactor `applyAgentEvent` by protocol responsibility, not by extracting each
case into a one-call peeler. Use a small number of typed reducers for:

- session and turn start
- streamed text, reasoning, sources, and progress
- tool lifecycle
- route and arrival cards
- terminal success, clarification, cancellation, and failure

Keep `ChatReducerAction` discriminated. Preserve exhaustive checking with a
`never` assertion or the existing equivalent. Do not use a string-keyed
handler registry, mutable context bag, class hierarchy, or catch-all partial
state merge. Protect turn identity, unique tool and card updates, exactly one
terminal outcome, and ignored stale-turn events through public reducer tests.

### Batch 7C: controller and transport lifecycle

Separate one network attempt from the retry policy in `runTurn`. The attempt
returns a small discriminated outcome based on the existing failure taxonomy.
The outer function owns expired-session recovery and the existing retry
limit. The `finally` path continues to clear abort and active-request state.
Preserve cancellation, dropped-stream classification, meta-event timing,
session replacement, and one terminal callback. Do not add a stateful runner
class or duplicate the stream parser.

Then repair `mapbox-search.ts`, proxy, stream proxy, WebSocket, session, and
provider boundaries with guard clauses and named parse or recovery policies.
Keep timeouts, abort signals, status codes, redaction, and streaming headers
unchanged. Validate untrusted values once at the outer boundary.

### Batch 7D: page composition and gate

Reduce `app/page.tsx` only by extracting independently testable state or
interaction ownership. Do not create pass-through hooks or move a large prop
bag into another file. Finish the remaining owned complexity findings, then
close line, exact branch, and exact function coverage under the F1 rules.

Batch 7 acceptance also requires no frontend calculation of itinerary timing,
duration, transfers, ranking, or recommendation facts. It requires zero owned
functions above 12, zero owned unexecuted production files, no new source-text
tests, and neutral or negative net production growth unless the one shared
boundary schema is the measured, reviewer-accepted reason for growth.

## Batch 8: components, interaction, and maps

Owned paths:

- `frontend/components/**`
- `frontend/tests/release/**`

F0 complexity-above-12 inventory: 46 functions. Regenerate it after F1 and
Batch 7. Start from the accepted Batch 7 commit.

Prioritize `route-plan.buildPlan` (65), `DestinationInput` (46), subway-network
feature construction (43), itinerary event adaptation (39), `AssistantMessage`
(34), route steps and reasoning (26 through 32), route-view itinerary, alert
normalization, route-stop feature construction, and SmartRoute map lifecycle.

Complete these clusters in order.

### Batch 8A: prove browser source coverage

Before refactoring a React or MapLibre component, extend the existing
Playwright and c8 path just enough to prove Chromium execution maps to exact
original `.ts` and `.tsx` files and line ranges. Use the existing Next dev
Webpack server, Playwright, browser coverage API or CDP coverage, source maps,
and c8's existing remapper. Do not install a second component framework.

The proof must exercise one known interaction in an existing release test and
show hits in the expected original component lines, not only a hashed bundle
URL. Add a small mapper fixture self-test if mapping code is introduced. If
the proof cannot map exact original sources, stop the batch and report the
blocker. Do not compensate with source-regex assertions or production callback
renames.

### Batch 8B: remove frontend-owned itinerary arithmetic

Use the narrowed Batch 7 canonical candidate type throughout the rail, map,
and route-view adapters. In particular:

1. In `left-rail/live-data/route-plan.ts`, remove the `now + total minutes`
   arrival fallback, transfer recount from transit steps, and leave-by clock
   derived by subtracting the first walk from a live countdown.
2. In `left-rail/live-data/route-candidates.ts`, remove fallback totals from
   `route_total_minutes` or the maximum `minutes_until_arrival`, client-made
   arrival and departure clocks, and regex parsing of recommendation prose to
   recover minutes or transfer counts.
3. Format backend-owned `departure_at`, `arrival_at`,
   `total_duration_seconds`, `transfer_count`, structured recommendation
   reasons, and explicit live-arrival context without changing their meaning.
   If a canonical fact is absent, render the existing unavailable state. Do
   not guess.
4. Do not rank candidates, select a recommendation, or infer a rejection
   reason in React. Candidate identity, ranking, and facts remain backend
   owned. Preserve geometry and route identity for the map.
5. Add adapter tests with deliberately conflicting legacy step fields to prove
   the canonical value wins, plus missing-clock and malformed-boundary cases.
   The tests call the public adapter, not its private formatting helpers.

Stop and request a backend contract field if the product truly needs a fact
the canonical response does not provide.

### Batch 8C: component behavior and reader load

Repair chat, route-view, destination input, itinerary event adaptation, and
alert presentation by cohesive interaction responsibility. Prefer rendered or
browser behavior for keyboard navigation, focus, disclosure, route selection,
mobile navigation, reduced motion, and unavailable states.

The following fixed-point tests contain substantial source or CSS text
assertions and are not proof that the component works:

- `chat-arrivals-card.test.mjs`
- `chat-composer.test.mjs`
- `chat-route-card.test.mjs`
- `chat-sidebar.test.mjs`
- `chat-working-panel.test.mjs`
- `home-screen-layout.test.mjs`
- `mobile-navigation.test.mjs`
- `left-rail/hydration.test.mjs`
- `left-rail/route-view.characterization.test.mjs`

When a touched behavior is covered only by one of those assertions, replace
that assertion with rendered or browser behavior and then remove the weaker
assertion. Do not rewrite unrelated stable tests only to increase a deletion
count. Keep artifact-byte, generated-output, and actual rendered-markup tests
when those outputs are the contract.

### Batch 8D: map projection and lifecycle

Separate pure feature projection from MapLibre source and layer lifecycle only
where each side becomes independently understandable and testable. Preserve
official colors, shared-corridor separation, station relationships, stable
feature ids, event cleanup, map resize behavior, and reduced motion. Do not
split `subway-network.ts` or `smart-route-map.tsx` by size alone. Do not move
canonical route decisions into map helpers.

Finish all remaining component findings and the owned coverage gate only after
the behavior clusters are green. Acceptance requires zero owned functions
above 12, zero owned unexecuted production files, source-mapped browser proof,
no source-text tests added, no frontend itinerary arithmetic, and no visible
behavior drift outside an explicitly approved correction.

## Batch 9: transit artifact generation and frontend tools

Owned paths:

- `frontend/scripts/**`

F0 complexity-above-12 inventory: 70 functions. Regenerate it after F1 and
Batch 8. Start from the accepted Batch 8 commit.

Prioritize `bundle-stage.ts` (107), `shared-corridor-separation-stage.ts`
(87), same-color merge and Mott Haven stages (49), lane continuity, route-gap,
snapping, physical-bundle, collapse, and finalization stages, and
`regenerate-canonical-from-gtfs.ts`.

Complete these clusters in order.

### Batch 9A: subtract stale pipeline state

`bundle-stage.ts` initializes `unbundledFeatures` to an empty array and never
adds an item. Remove that internal array and its propagation through bundle
artifact parameter types, writer, metadata, and validation stages. If the
public artifact schema requires `remaining_unbundled_corridors`, keep that
output as the explicit invariant value `0`. Do not retain a dead collection to
produce it. Remove stale `Fix 2` migration comments after tests prove the
current policy. Generated runtime artifacts must remain byte-for-byte or
semantically identical according to their documented lifecycle.

### Batch 9B: type the touched stage boundaries

The F0 tree has more than 100 explicit `any` uses in Batch 9, concentrated in
diagnostics, validation, phase 3c, bundle output, and metadata. Do not launch a
blind repository-wide `any` replacement and do not create one giant property
interface with every field optional.

Use the existing generic feature model as the base. For each touched pipeline
boundary, define the smallest stage-owned property type that represents the
fields that stage requires and produces. Use `unknown` at untrusted JSON
boundaries and narrow it. Preserve generic geometry and feature types. Never
use a cast, non-null assertion, or index signature merely to silence the
checker. Record explicit `any` counts before and after each touched cluster.
The count must not rise.

### Batch 9C: bundle and shared corridors

Refactor `buildBundleArtifacts` by real pipeline stages:

- build the anchor and corridor index
- classify and project solo lanes
- project bundles and their lanes
- project gap markers
- bake final lane geometry
- sort deterministically and assemble output

Each extracted function owns one phase with a typed input and output. Do not
extract per-branch wrappers or pass one mutable context bag through every
phase. Preserve anchor identity, corridor membership, lane order, gap
semantics, route colors, feature ids, and deterministic ordering. Apply the
same rule to shared-corridor separation: split only classification,
intersection policy, projection, and finalization responsibilities that can be
tested independently.

### Batch 9D: geometry repair and remaining tools

Repair Mott Haven, same-color merge, lane continuity, route gaps, snapping,
physical bundles, collapse, finalization, and GTFS regeneration in cohesive
location or phase clusters. Keep local NYC geometry policy local when it is
not the same concept as a shared transform. Do not generalize one-off
cartographic exceptions into a framework.

For every cluster, run focused transformation tests, typecheck scripts,
Oxlint, and the exact artifact checks. At the boundary, regenerate through the
documented commands, inspect every generated diff, run the complete generator
test manifest in both relevant orders, and run release CI. Never hand-edit a
generated GeoJSON file or accept a hash change without explaining the exact
source transformation that caused it.

Batch 9 acceptance requires zero owned functions above 12, zero owned
unexecuted production files, the F1 coverage target or a reviewer-recorded
exception, no unexplained `any` growth, no dead pipeline state, deterministic
artifacts, and no helper or module added solely to satisfy a metric.

## Navigability and growth rules

For every later cluster:

- Record production lines added and removed.
- Record production functions added and removed.
- Prefer neutral or negative net production growth.
- Stop when net new production exceeds 500 lines unless the added behavior or
  independent module boundary is explicitly justified.
- A 500 to 800 line cohesive module is acceptable.
- Do not replace one difficult function with dozens of tiny one-call helpers.
- A helper must own a named policy, phase, lifecycle, side effect, parser, or
  recovery responsibility.
- Do not create pass-through modules.
- Do not merge independent domains merely to reduce file count.
- Delete dead compatibility paths after all callers migrate.
- Preserve API contracts and canonical itinerary ownership.

## Final completion gate

Cleanup is complete only when these commands exit 0:

```powershell
py -m ruff check --config pyproject.toml backend
py scripts/report_backend_debt.py --max-existing 12
py scripts/check_quality.py --cognitive-only --quality-ref HEAD
py scripts/check_quality.py --quality-ref HEAD

Set-Location frontend
npm run lint
npm run lint:oxlint
npm run typecheck
npm run typecheck:scripts
npm run test:unit
npm run test:coverage
npm run verify:transit-artifacts
Set-Location ..
py scripts/report_frontend_debt.py --output .audit/frontend-debt.json
```

Also require:

- zero Ruff, Oxlint, and ESLint findings
- zero new or worsened cyclomatic, cognitive, or CRAP debt
- zero stale baseline entries
- no unexplained new suppression
- no baseline increase
- no test or skip reduction without approved feature deletion or duplicate
  evidence
- no unexpected generated artifact change
- no frontend ownership of canonical itinerary arithmetic
- zero unexecuted production files in each completed frontend batch
- at least 95% line, exact c8 branch, and exact c8 function coverage on
  production frontend scope, or a reviewer-recorded exception that names the
  remaining non-behavioral branches and why testing them would be harmful
- a final handoff with exact counts, commands, and authorized exceptions

## Current status 2026-08-31

The accepted backend test-assurance checkpoint has 1,917 passes, 21 skips,
and 446 subtests. Combined line and branch
coverage is 89.14%. Statement coverage is 91.77% (20,419 / 22,251). Branch
coverage is 81.64% (6,360 / 7,790). The authored-function report has 50
zero-covered functions out of 2,437 and no function with CRAP above 30. The
Cosmic Ray canary killed 48 of 54 executed candidates. Batch 6F must start
from a clean commit that contains this checkpoint.

F0 is committed at `9b4327d`. Frontend ceiling 12. Python Ruff ceiling 10.
Unit 557 passed. Release 14 passed, 4 skipped. Line coverage is 52.27%
(23449 / 44859). Exact branch and function gates remain unresolved while 114
production files are unexecuted. Standard c8 source-mapped function coverage
is 82.27% (1601 / 1946), but it is not yet gate-eligible because missing files
do not contribute a complete function denominator. The authored mapper has
847 measured, 927 uncovered, and 688 unresolved functions. Keep those counts
as diagnostics after F1.

Oxlint complexity above 12 remains 126 at F0 (Batch 7: 10, Batch 8: 46,
Batch 9: 70). `quality/baseline.json` has 113 entries after Codex removed the
12 proven-stale TypeScript entries. F0 quality against `ae27211fa9ba` exits 0
with `approval_eligible: true` and no new, worsened, cognitive, Ruff C901,
Ruff structural, or stale violations.

The whole-repository audit did not start Batch 7. The next implementation
order is Batch 6F, F1, 7, 8, and 9. The audit documents the only justified
backend closure, a corrected frontend coverage gate, 892 whole-file frontend
production lines proven dead before follow-up CSS and exports, the canonical
route boundary needed before component cleanup, and deletion-first generator
work.
